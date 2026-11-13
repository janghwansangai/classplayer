"""영상 라이브러리: 지정한 폴더를 훑어 목록을 만들고 검색한다."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QSize, QStandardPaths, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QToolButton, QVBoxLayout, QWidget)

from classplayer.bootstrap import VIDEO_EXTENSIONS


@dataclass
class Entry:
    path: str
    name: str
    folder: str
    size: int
    mtime: float


def scan_folders(folders: list[str], max_files: int = 20000) -> list[Entry]:
    seen: set[str] = set()
    out: list[Entry] = []
    for root in folders:
        rp = Path(root)
        if not rp.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(rp):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for fn in filenames:
                if Path(fn).suffix.lower() not in VIDEO_EXTENSIONS or fn.startswith("."):
                    continue
                full = os.path.join(dirpath, fn)
                if full in seen:
                    continue
                seen.add(full)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                out.append(Entry(full, fn, os.path.basename(dirpath), st.st_size, st.st_mtime))
                if len(out) >= max_files:
                    return out
    out.sort(key=lambda e: e.mtime, reverse=True)
    return out


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


class _ScanNotifier(QObject):
    done = Signal(object)
    thumb = Signal(str, str, float)   # path, 썸네일 파일, 길이(초)


def _fmt_dur(sec: float) -> str:
    s = int(sec)
    h, rem = divmod(s, 3600)
    m, ss = divmod(rem, 60)
    return f"{h}:{m:02d}:{ss:02d}" if h else f"{m:02d}:{ss:02d}"


class LibraryThumbs:
    """목록용 작은 썸네일과 길이를 ffmpeg/ffprobe 로 만들어 캐시한다."""

    def __init__(self, ffmpeg: str | None, ffprobe: str | None) -> None:
        self.ffmpeg, self.ffprobe = ffmpeg, ffprobe
        base = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation) or "/tmp")
        self.root = base / "libthumbs"
        self.root.mkdir(parents=True, exist_ok=True)

    def _key(self, path: str) -> str:
        try:
            mt = os.stat(path).st_mtime
        except OSError:
            mt = 0
        return hashlib.sha1(f"{path}|{mt}".encode()).hexdigest()[:16]

    def get(self, path: str) -> tuple[str, float] | None:
        k = self.root / self._key(path)
        meta = k.with_suffix(".json")
        if meta.exists() and k.with_suffix(".jpg").exists():
            try:
                return str(k.with_suffix(".jpg")), float(json.loads(meta.read_text()).get("duration", 0))
            except Exception:  # noqa: BLE001
                return None
        return None

    def make(self, path: str) -> tuple[str, float] | None:
        cached = self.get(path)
        if cached:
            return cached
        if not self.ffmpeg:
            return None
        k = self.root / self._key(path)
        dur = 0.0
        if self.ffprobe:
            try:
                out = subprocess.run([self.ffprobe, "-v", "quiet", "-show_entries", "format=duration", "-of", "json", path],
                                     capture_output=True, text=True, timeout=20, check=False).stdout
                dur = float(json.loads(out or "{}").get("format", {}).get("duration") or 0)
            except Exception:  # noqa: BLE001
                dur = 0.0
        ss = max(0.0, min(dur * 0.1, 30.0)) if dur else 1.0
        try:
            subprocess.run([self.ffmpeg, "-y", "-v", "error", "-ss", f"{ss:.2f}", "-i", path, "-frames:v", "1",
                            "-vf", "scale=160:-2", "-q:v", "5", str(k.with_suffix(".jpg"))],
                           capture_output=True, timeout=60, check=False)
        except Exception:  # noqa: BLE001
            return None
        if not k.with_suffix(".jpg").exists():
            return None
        k.with_suffix(".json").write_text(json.dumps({"duration": dur}))
        return str(k.with_suffix(".jpg")), dur


class LibraryPanel(QWidget):
    play_requested = Signal(str)
    folders_changed = Signal(list)

    def __init__(self, folders: list[str], extra_folders: list[str] | None = None, parent=None,
                 ffmpeg: str | None = None, ffprobe: str | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("library")
        self._folders = list(folders)
        self._extra = list(extra_folders or [])   # 다운로드 폴더처럼 항상 포함되는 폴더
        self._entries: list[Entry] = []
        self._items: dict[str, QListWidgetItem] = {}
        self._thumbs = LibraryThumbs(ffmpeg, ffprobe)
        self._thumb_gen = 0
        self._notifier = _ScanNotifier()
        self._notifier.done.connect(self._on_scanned)
        self._notifier.thumb.connect(self._on_thumb)

        self.search = QLineEdit()
        self.search.setPlaceholderText("영상 검색 (파일명, 폴더명)")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)

        self.list = QListWidget()
        self.list.setIconSize(QSize(80, 50))
        self.list.setSpacing(1)
        self.list.itemActivated.connect(self._on_activated)
        self.list.itemDoubleClicked.connect(self._on_activated)

        self.folder_combo = QComboBox()
        self.folder_combo.setToolTip("라이브러리 폴더")
        self.btn_add = QToolButton()
        self.btn_add.setText("+")
        self.btn_add.setToolTip("폴더 추가")
        self.btn_add.clicked.connect(self.add_folder_dialog)
        self.btn_remove = QToolButton()
        self.btn_remove.setText("−")
        self.btn_remove.setToolTip("선택한 폴더 제거")
        self.btn_remove.clicked.connect(self._remove_current_folder)
        self.btn_refresh = QToolButton()
        self.btn_refresh.setText("↻")
        self.btn_refresh.setToolTip("다시 훑기")
        self.btn_refresh.clicked.connect(self.rescan)
        self.lbl_count = QLabel("")
        self.lbl_count.setStyleSheet("color: gray;")

        top = QHBoxLayout()
        top.addWidget(self.folder_combo, 1)
        top.addWidget(self.btn_add)
        top.addWidget(self.btn_remove)
        top.addWidget(self.btn_refresh)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.addLayout(top)
        lay.addWidget(self.search)
        lay.addWidget(self.list, 1)
        lay.addWidget(self.lbl_count)
        self._refresh_combo()
        self.rescan()

    # ------------------------------------------------------------------
    @property
    def folders(self) -> list[str]:
        return list(self._folders)

    def set_extra_folders(self, extra: list[str]) -> None:
        self._extra = list(extra)
        self.rescan()

    def _refresh_combo(self) -> None:
        self.folder_combo.clear()
        for f in self._folders:
            self.folder_combo.addItem(Path(f).name or f, f)
        if not self._folders:
            self.folder_combo.addItem("폴더를 추가하세요 (+)", "")

    def add_folder_dialog(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "라이브러리에 추가할 폴더")
        if d:
            self.add_folder(d)

    def add_folder(self, folder: str) -> None:
        if folder and folder not in self._folders:
            self._folders.append(folder)
            self._refresh_combo()
            self.folders_changed.emit(self.folders)
            self.rescan()

    def _remove_current_folder(self) -> None:
        f = self.folder_combo.currentData()
        if f in self._folders:
            self._folders.remove(f)
            self._refresh_combo()
            self.folders_changed.emit(self.folders)
            self.rescan()

    def rescan(self) -> None:
        folders = list(dict.fromkeys(self._folders + self._extra))
        self.lbl_count.setText("훑는 중...")

        def work() -> None:
            self._notifier.done.emit(scan_folders(folders))

        threading.Thread(target=work, daemon=True).start()

    def _on_scanned(self, entries: object) -> None:
        self._entries = list(entries)  # type: ignore[arg-type]
        self._apply_filter(self.search.text())

    def _apply_filter(self, text: str) -> None:
        tokens = [t for t in text.lower().split() if t]
        self.list.clear()
        self._items.clear()
        shown = 0
        pending: list[str] = []
        for e in self._entries:
            hay = (e.name + " " + e.folder).lower()
            if all(t in hay for t in tokens):
                item = QListWidgetItem(f"{Path(e.name).stem}\n{e.folder} · {_human_size(e.size)}")
                item.setToolTip(e.path)
                item.setData(Qt.ItemDataRole.UserRole, e.path)
                cached = self._thumbs.get(e.path)
                if cached:
                    self._apply_thumb(item, e, cached[0], cached[1])
                else:
                    pending.append(e.path)
                self.list.addItem(item)
                self._items[e.path] = item
                shown += 1
        self.lbl_count.setText(f"{shown}개 표시 / 전체 {len(self._entries)}개")
        if pending:
            self._start_thumbs(pending[:300])

    def _apply_thumb(self, item: QListWidgetItem, e: Entry, jpg: str, dur: float) -> None:
        pm = QPixmap(jpg)
        if not pm.isNull():
            item.setIcon(QIcon(pm))
        tail = f"{_fmt_dur(dur)} · " if dur else ""
        item.setText(f"{Path(e.name).stem}\n{tail}{e.folder} · {_human_size(e.size)}")

    def _start_thumbs(self, paths: list[str]) -> None:
        self._thumb_gen += 1
        gen = self._thumb_gen

        def work() -> None:
            for p in paths:
                if gen != self._thumb_gen:
                    return
                r = self._thumbs.make(p)
                if r:
                    self._notifier.thumb.emit(p, r[0], r[1])

        threading.Thread(target=work, daemon=True).start()

    def _on_thumb(self, path: str, jpg: str, dur: float) -> None:
        item = self._items.get(path)
        if item is None:
            return
        e = next((x for x in self._entries if x.path == path), None)
        if e:
            self._apply_thumb(item, e, jpg, dur)

    def _on_activated(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            self.play_requested.emit(str(path))

    def focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()
