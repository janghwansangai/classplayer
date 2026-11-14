"""재생목록: 여러 영상(파일·유튜브 링크)을 순서대로 묶어 차례로 재생한다.

목록은 앱 데이터 폴더의 playlists.json 에 저장해 앱을 다시 켜도 남는다.
"""
from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QStandardPaths, Signal


@dataclass
class PlaylistItem:
    target: str            # 파일 경로 또는 링크
    title: str = ""

    @property
    def is_link(self) -> bool:
        return self.target.startswith(("http://", "https://"))

    def label(self) -> str:
        return self.title or (self.target if self.is_link else Path(self.target).stem)


@dataclass
class Playlist:
    name: str
    items: list[PlaylistItem] = field(default_factory=list)


class PlaylistStore(QObject):
    """모든 재생목록과 '지금 재생 중인 목록·위치'를 들고 있다."""

    changed = Signal()                       # 목록 내용이 바뀜
    position_changed = Signal(str, int)      # 재생 중인 목록 이름, 위치(-1 = 재생 중 아님)

    def __init__(self, path: Path | None = None, parent=None) -> None:
        super().__init__(parent)
        if path is None:
            base = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation) or ".")
            base.mkdir(parents=True, exist_ok=True)
            path = base / "playlists.json"
        self.path = path
        self.lists: list[Playlist] = []
        self.active: str = ""            # 재생 중인 목록
        self.index: int = -1
        self.repeat: bool = False
        self.shuffle: bool = False
        self._order: list[int] = []
        self.load()

    # ------------------------------------------------------------------ 저장
    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.lists = [Playlist(d["name"], [PlaylistItem(**it) for it in d.get("items", [])])
                          for d in data.get("lists", [])]
            self.repeat = bool(data.get("repeat", False))
            self.shuffle = bool(data.get("shuffle", False))
        except Exception:  # noqa: BLE001
            self.lists = []
        if not self.lists:
            self.lists = [Playlist("수업 목록")]

    def save(self) -> None:
        data = {"lists": [{"name": p.name, "items": [asdict(i) for i in p.items]} for p in self.lists],
                "repeat": self.repeat, "shuffle": self.shuffle}
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    def _touch(self) -> None:
        self.save()
        self.changed.emit()

    # ------------------------------------------------------------------ 목록 관리
    def names(self) -> list[str]:
        return [p.name for p in self.lists]

    def get(self, name: str) -> Playlist | None:
        return next((p for p in self.lists if p.name == name), None)

    def unique_name(self, base: str) -> str:
        name, n = base, 2
        while self.get(name):
            name = f"{base} {n}"
            n += 1
        return name

    def create(self, name: str) -> str:
        name = self.unique_name(name.strip() or "새 목록")
        self.lists.append(Playlist(name))
        self._touch()
        return name

    def rename(self, old: str, new: str) -> str:
        p = self.get(old)
        new = new.strip()
        if not p or not new or new == old:
            return old
        new = self.unique_name(new)
        p.name = new
        if self.active == old:
            self.active = new
        self._touch()
        return new

    def delete(self, name: str) -> None:
        self.lists = [p for p in self.lists if p.name != name]
        if not self.lists:
            self.lists = [Playlist("수업 목록")]
        if self.active == name:
            self.stop()
        self._touch()

    def add(self, name: str, target: str, title: str = "") -> bool:
        p = self.get(name)
        if not p or not target:
            return False
        p.items.append(PlaylistItem(target, title))
        self._order = []
        self._touch()
        return True

    def remove(self, name: str, idx: int) -> None:
        p = self.get(name)
        if p and 0 <= idx < len(p.items):
            del p.items[idx]
            if self.active == name and idx <= self.index:
                self.index -= 1
            self._order = []
            self._touch()

    def move(self, name: str, idx: int, delta: int) -> int:
        p = self.get(name)
        if not p:
            return idx
        j = idx + delta
        if 0 <= idx < len(p.items) and 0 <= j < len(p.items):
            p.items[idx], p.items[j] = p.items[j], p.items[idx]
            if self.active == name:
                if self.index == idx:
                    self.index = j
                elif self.index == j:
                    self.index = idx
            self._order = []
            self._touch()
            return j
        return idx

    # ------------------------------------------------------------------ 재생 순서
    def start(self, name: str, idx: int = 0) -> PlaylistItem | None:
        p = self.get(name)
        if not p or not p.items:
            return None
        self.active, self.index = name, max(0, min(idx, len(p.items) - 1))
        self._order = []
        self.position_changed.emit(self.active, self.index)
        return p.items[self.index]

    def stop(self) -> None:
        self.active, self.index = "", -1
        self._order = []
        self.position_changed.emit("", -1)

    def _step(self, delta: int) -> PlaylistItem | None:
        p = self.get(self.active)
        if not p or not p.items:
            self.stop()
            return None
        n = len(p.items)
        if self.shuffle and n > 1:
            if not self._order or len(self._order) != n:
                rest = [i for i in range(n) if i != self.index]
                random.shuffle(rest)
                self._order = [self.index] + rest if 0 <= self.index < n else rest
            pos = self._order.index(self.index) if self.index in self._order else -1
            pos += delta
            if pos >= n or pos < 0:
                if not self.repeat:
                    return None
                random.shuffle(self._order)
                pos %= n
            self.index = self._order[pos]
        else:
            j = self.index + delta
            if j >= n or j < 0:
                if not self.repeat:
                    return None
                j %= n
            self.index = j
        self.position_changed.emit(self.active, self.index)
        return p.items[self.index]

    def next(self) -> PlaylistItem | None:
        return self._step(1)

    def prev(self) -> PlaylistItem | None:
        return self._step(-1)

    def set_options(self, repeat: bool | None = None, shuffle: bool | None = None) -> None:
        if repeat is not None:
            self.repeat = repeat
        if shuffle is not None:
            self.shuffle = shuffle
            self._order = []
        self.save()
