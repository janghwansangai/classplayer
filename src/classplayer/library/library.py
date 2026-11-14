"""영상 라이브러리: 지정한 폴더를 훑어 목록을 만들고 검색한다."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QRectF, QSize, QStandardPaths, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QToolButton, QVBoxLayout, QWidget)

from classplayer.bootstrap import AUDIO_EXTENSIONS, MEDIA_EXTENSIONS, is_cloud_placeholder
from classplayer.ui import icons

THUMB_W, THUMB_H = 96, 56
REC_TAGS = ("[녹화", "[녹음")


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
                if Path(fn).suffix.lower() not in MEDIA_EXTENSIONS or fn.startswith("."):
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
    thumb = Signal(str, str, float, bool)   # path, 썸네일 파일, 길이(초), 360도 여부


def badge_pixmap(jpg: str, dur: float, spherical: bool, audio: bool) -> QPixmap:
    """시안 C 목록 썸네일: 둥근 모서리 + 왼쪽 위 360° 배지 + 오른쪽 아래 길이."""
    dpr = 2.0
    w, h = int(THUMB_W * dpr), int(THUMB_H * dpr)
    pm = QPixmap(w, h)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(0, 0, w, h), 7 * dpr, 7 * dpr)
    p.setClipPath(clip)
    src = QPixmap(jpg) if jpg else QPixmap()
    if not src.isNull():
        scaled = src.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
        p.drawPixmap((w - scaled.width()) // 2, (h - scaled.height()) // 2, scaled)
    else:
        g = QLinearGradient(0, 0, w, h)
        g.setColorAt(0, QColor("#3b2f12" if audio else "#2c3e6b"))
        g.setColorAt(1, QColor("#1b1206" if audio else "#17213a"))
        p.fillRect(0, 0, w, h, g)
        ic = icons.pixmap("music" if audio else "play", "#fbbf24" if audio else "#8b8e99", 22, dpr)
        p.drawPixmap((w - ic.width()) // 2, (h - ic.height()) // 2, ic)
    f = QFont()
    f.setPixelSize(int(10 * dpr))
    f.setBold(True)
    p.setFont(f)
    fm = p.fontMetrics()
    if dur:
        t = _fmt_dur(dur)
        tw = fm.horizontalAdvance(t) + int(8 * dpr)
        r = QRectF(w - tw - 4 * dpr, h - 16 * dpr - 4 * dpr, tw, 16 * dpr)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 170))
        p.drawRoundedRect(r, 4 * dpr, 4 * dpr)
        p.setPen(QColor("#ffffff"))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, t)
    if spherical:
        t = "360°"
        tw = fm.horizontalAdvance(t) + int(8 * dpr)
        r = QRectF(4 * dpr, 4 * dpr, tw, 16 * dpr)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#f59e0b"))
        p.drawRoundedRect(r, 4 * dpr, 4 * dpr)
        p.setPen(QColor("#1b1206"))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, t)
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


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

    def get(self, path: str) -> tuple[str, float, bool] | None:
        k = self.root / self._key(path)
        meta = k.with_suffix(".json")
        if not meta.exists():
            return None
        try:
            d = json.loads(meta.read_text())
        except Exception:  # noqa: BLE001
            return None
        if "sph" not in d:          # 예전 캐시: 360도 정보를 다시 만든다
            return None
        jpg = k.with_suffix(".jpg")
        return (str(jpg) if jpg.exists() else ""), float(d.get("duration", 0)), bool(d.get("sph"))

    def make(self, path: str) -> tuple[str, float, bool] | None:
        cached = self.get(path)
        if cached:
            return cached
        if is_cloud_placeholder(path):
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
        audio = Path(path).suffix.lower() in AUDIO_EXTENSIONS
        sph = False
        if not audio:
            try:
                from classplayer.youtube.probe import probe_spherical
                sph = probe_spherical(self.ffprobe, path).is_spherical
            except Exception:  # noqa: BLE001
                sph = False
        jpg = k.with_suffix(".jpg")
        if self.ffmpeg:
            ss = max(0.0, min(dur * 0.1, 30.0)) if dur and not audio else 0.0
            try:
                subprocess.run([self.ffmpeg, "-y", "-v", "error", "-ss", f"{ss:.2f}", "-i", path, "-frames:v", "1",
                                "-vf", f"scale={THUMB_W * 2}:-2", "-q:v", "4", str(jpg)],
                               capture_output=True, timeout=60, check=False)
            except Exception:  # noqa: BLE001
                pass
        k.with_suffix(".json").write_text(json.dumps({"duration": dur, "sph": sph}))
        return (str(jpg) if jpg.exists() else ""), dur, sph


class LibraryPanel(QWidget):
    play_requested = Signal(str)
    folders_changed = Signal(list)
    open_saved_requested = Signal()

    FILTERS = (("all", "전체"), ("360", "360°"), ("saved", "저장함"), ("rec", "녹화·녹음"))

    def __init__(self, folders: list[str], extra_folders: list[str] | None = None, parent=None,
                 ffmpeg: str | None = None, ffprobe: str | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("library")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._folders = list(folders)
        self._extra = list(extra_folders or [])   # 다운로드 폴더처럼 항상 포함되는 폴더
        self._entries: list[Entry] = []
        self._items: dict[str, QListWidgetItem] = {}
        self._sph: dict[str, bool] = {}
        self._filter = "all"
        self._thumbs = LibraryThumbs(ffmpeg, ffprobe)
        self._thumb_gen = 0
        self._notifier = _ScanNotifier()
        self._notifier.done.connect(self._on_scanned)
        self._notifier.thumb.connect(self._on_thumb)

        self.search = QLineEdit()
        self.search.setPlaceholderText("영상 검색…")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(QIcon(icons.pixmap("search", "#6b6e7a", 16)), QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(lambda _t: self._apply_filter())

        chips = QHBoxLayout()
        chips.setSpacing(6)
        self._chip_group = QButtonGroup(self)
        self._chip_group.setExclusive(True)
        self.chips: dict[str, QPushButton] = {}
        for key, label in self.FILTERS:
            b = QPushButton(label)
            b.setObjectName("chip")
            b.setCheckable(True)
            b.setChecked(key == "all")
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(lambda _c, k=key: self.set_filter(k))
            self._chip_group.addButton(b)
            self.chips[key] = b
            chips.addWidget(b)
        chips.addStretch(1)

        self.list = QListWidget()
        self.list.setIconSize(QSize(THUMB_W, THUMB_H))
        self.list.setSpacing(1)
        self.list.setUniformItemSizes(True)
        self.list.itemActivated.connect(self._on_activated)
        self.list.itemDoubleClicked.connect(self._on_activated)

        self.folder_combo = QComboBox()
        self.folder_combo.setToolTip("라이브러리 폴더")

        def tbtn(name: str, tip: str, slot) -> QToolButton:
            b = QToolButton()
            b.setIcon(QIcon(icons.pixmap(name, "#d9dbe3", 16)))
            b.setToolTip(tip)
            b.clicked.connect(slot)
            return b

        self.btn_add = tbtn("plus", "폴더 추가", self.add_folder_dialog)
        self.btn_remove = tbtn("minus", "선택한 폴더 제거", self._remove_current_folder)
        self.btn_refresh = tbtn("refresh", "다시 훑기", self.rescan)
        self.btn_open_saved = tbtn("folder", "저장 폴더 열기 (녹화·녹음·미리 받기)", lambda: self.open_saved_requested.emit())
        self.lbl_count = QLabel("")
        self.lbl_count.setObjectName("muted")

        top = QHBoxLayout()
        top.setSpacing(6)
        top.addWidget(self.folder_combo, 1)
        top.addWidget(self.btn_add)
        top.addWidget(self.btn_remove)
        top.addWidget(self.btn_refresh)
        top.addWidget(self.btn_open_saved)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 4, 10, 8)
        lay.setSpacing(8)
        lay.addLayout(top)
        lay.addWidget(self.search)
        lay.addLayout(chips)
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

    def set_filter(self, key: str) -> None:
        self._filter = key
        if key in self.chips:
            self.chips[key].setChecked(True)
        self._apply_filter()

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
        for e in self._entries:
            c = self._thumbs.get(e.path)
            if c:
                self._sph[e.path] = c[2]
        self._apply_filter()

    def _is_saved(self, e: Entry) -> bool:
        return any(os.path.normcase(e.path).startswith(os.path.normcase(os.path.join(d, ""))) for d in self._extra)

    def _passes(self, e: Entry) -> bool:
        f = self._filter
        if f == "360":
            return self._sph.get(e.path, False)
        if f == "saved":
            return self._is_saved(e)
        if f == "rec":
            return any(t in e.name for t in REC_TAGS)
        return True

    def _apply_filter(self, _text: str | None = None) -> None:
        tokens = [t for t in self.search.text().lower().split() if t]
        self.list.clear()
        self._items.clear()
        shown = 0
        pending: list[str] = []
        for e in self._entries:
            hay = (e.name + " " + e.folder).lower()
            if not all(t in hay for t in tokens) or not self._passes(e):
                continue
            item = QListWidgetItem()
            item.setToolTip(e.path)
            item.setData(Qt.ItemDataRole.UserRole, e.path)
            item.setSizeHint(QSize(0, THUMB_H + 14))
            cached = self._thumbs.get(e.path)
            if cached:
                self._apply_thumb(item, e, *cached)
            else:
                self._apply_thumb(item, e, "", 0.0, False)
                pending.append(e.path)
            self.list.addItem(item)
            self._items[e.path] = item
            shown += 1
        total = len(self._entries)
        self.lbl_count.setText(f"{shown}개 표시 / 전체 {total}개" if shown != total else f"{total}개")
        if pending:
            self._start_thumbs(pending[:300])

    def _apply_thumb(self, item: QListWidgetItem, e: Entry, jpg: str, dur: float, sph: bool) -> None:
        audio = Path(e.name).suffix.lower() in AUDIO_EXTENSIONS
        item.setIcon(QIcon(badge_pixmap(jpg, dur, sph, audio)))
        kind = "소리" if audio else ("360° 영상" if sph else "")
        meta = " · ".join(x for x in (kind, e.folder, _human_size(e.size)) if x)
        item.setText(f"{Path(e.name).stem}\n{meta}")

    def _start_thumbs(self, paths: list[str]) -> None:
        self._thumb_gen += 1
        gen = self._thumb_gen

        def work() -> None:
            for p in paths:
                if gen != self._thumb_gen:
                    return
                r = self._thumbs.make(p)
                if r:
                    self._notifier.thumb.emit(p, r[0], r[1], r[2])

        threading.Thread(target=work, daemon=True).start()

    def _on_thumb(self, path: str, jpg: str, dur: float, sph: bool) -> None:
        self._sph[path] = sph
        item = self._items.get(path)
        if item is None:
            return
        e = next((x for x in self._entries if x.path == path), None)
        if e:
            self._apply_thumb(item, e, jpg, dur, sph)

    def _on_activated(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            self.play_requested.emit(str(path))

    def focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()
