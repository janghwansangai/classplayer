"""영상 라이브러리: 지정한 폴더를 훑어 목록을 만들고 검색한다."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QRectF, QSize, QStandardPaths, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QToolButton, QVBoxLayout, QWidget)

from classplayer.bootstrap import AUDIO_EXTENSIONS, MEDIA_EXTENSIONS, NO_WINDOW, is_cloud_placeholder
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
    root: str = ""        # 이 파일을 찾은 라이브러리 폴더 (목록에서 폴더별로 묶을 때)


# 영상이 들어 있을 리 없는 폴더 (프로그램 코드·시스템 폴더). 통째로 건너뛴다.
SKIP_DIRS = {"node_modules", "__pycache__", "site-packages", ".git", ".venv", "venv", "AppData",
             "$RECYCLE.BIN", "System Volume Information", "bower_components"}
# 미리 받기로 저장한 파일 이름 끝: "제목 [유튜브ID].mp4"
_YTDLP_NAME = re.compile(r"\[[A-Za-z0-9_-]{11}\]\.[A-Za-z0-9]+$")
# 확장자가 영상과 겹치는 다른 파일 (예: .ts = 타입스크립트 코드) 은 내용을 보고 거른다
_SNIFF_EXT = {".ts", ".mts", ".m2ts"}


def is_app_saved(name: str) -> bool:
    """ClassPlayer 가 저장한 파일인지 (미리 받기 또는 녹화·녹음)."""
    return any(t in name for t in REC_TAGS) or bool(_YTDLP_NAME.search(name))


def is_downloaded_name(name: str) -> bool:
    """미리 받기(yt-dlp)로 저장한 파일 이름인지 (녹화·녹음 제외)."""
    return bool(_YTDLP_NAME.search(name)) and not any(t in name for t in REC_TAGS)


def looks_like_mpegts(path: str) -> bool:
    """.ts/.mts 파일이 진짜 MPEG-TS 영상인지 (타입스크립트 코드 파일은 아님).

    MPEG-TS 는 188바이트마다 0x47, 블루레이(M2TS)는 192바이트마다 4바이트 뒤에 0x47 이 나온다.
    """
    try:
        with open(path, "rb") as f:
            b = f.read(192 * 3 + 8)
    except OSError:
        return False
    ts = len(b) > 376 and b[0] == 0x47 and b[188] == 0x47 and b[376] == 0x47
    m2ts = len(b) > 388 and b[4] == 0x47 and b[196] == 0x47 and b[388] == 0x47
    return ts or m2ts


def _is_media_name(fn: str) -> bool:
    return not fn.startswith(".") and Path(fn).suffix.lower() in MEDIA_EXTENSIONS


def scan_folders(folders: list[str], max_files: int = 20000, saved_folders: list[str] | None = None) -> list[Entry]:
    """라이브러리 폴더(하위 폴더 포함)와 저장 폴더(맨 위만, 앱이 저장한 파일만)에서 영상·소리 파일을 찾는다.

    저장 폴더가 '다운로드'처럼 아무 파일이나 있는 곳이어도 미리 받기·녹화·녹음한 것만 목록에 나온다.
    """
    seen: set[str] = set()
    out: list[Entry] = []

    def add(full: str, fn: str, dirpath: str, root: str) -> bool:
        if full in seen:
            return True
        if Path(fn).suffix.lower() in _SNIFF_EXT and not looks_like_mpegts(full):
            return True
        seen.add(full)
        try:
            st = os.stat(full)
        except OSError:
            return True
        out.append(Entry(full, fn, os.path.basename(dirpath), st.st_size, st.st_mtime, root))
        return len(out) < max_files

    for root in folders:
        rp = Path(root)
        if not rp.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(rp):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS]
            for fn in filenames:
                if _is_media_name(fn) and not add(os.path.join(dirpath, fn), fn, dirpath, root):
                    return out
    for root in saved_folders or []:
        try:
            names = os.listdir(root)
        except OSError:
            continue
        for fn in names:
            if _is_media_name(fn) and is_app_saved(fn) and not add(os.path.join(root, fn), fn, root, root):
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
                                     capture_output=True, text=True, timeout=20, check=False,
                                     creationflags=NO_WINDOW).stdout
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
                               capture_output=True, timeout=60, check=False, creationflags=NO_WINDOW)
            except Exception:  # noqa: BLE001
                pass
        k.with_suffix(".json").write_text(json.dumps({"duration": dur, "sph": sph}))
        return (str(jpg) if jpg.exists() else ""), dur, sph


class LibraryPanel(QWidget):
    play_requested = Signal(str)
    folders_changed = Signal(list)
    open_saved_requested = Signal()

    FILTERS = (("all", "전체"), ("360", "360°"), ("saved", "미리 받기"), ("rec", "녹화·녹음"))
    FILTER_TIPS = {"all": "라이브러리 폴더와 저장 폴더의 영상·소리 전부",
                   "360": "360도 영상만",
                   "saved": "유튜브에서 '미리 받기'로 저장한 영상",
                   "rec": "녹화·녹음 기능으로 저장한 파일 (이름에 [녹화 …]/[녹음 …])"}
    HEADER_ROLE = Qt.ItemDataRole.UserRole + 1

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
        self._folder_filter = ""          # 고른 라이브러리 폴더 ("" = 모든 폴더)
        self._collapsed: set[str] = set()  # 접어 둔 폴더 묶음
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
            b.setToolTip(self.FILTER_TIPS.get(key, ""))
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(lambda _c, k=key: self.set_filter(k))
            self._chip_group.addButton(b)
            self.chips[key] = b
            chips.addWidget(b)
        chips.addStretch(1)

        self.list = QListWidget()
        self.list.setIconSize(QSize(THUMB_W, THUMB_H))
        self.list.setSpacing(1)
        self.list.setUniformItemSizes(False)     # 폴더 제목 줄은 높이가 다르다
        self.list.itemClicked.connect(self._on_clicked)
        self.list.itemActivated.connect(self._on_activated)
        self.list.itemDoubleClicked.connect(self._on_activated)

        self.folder_combo = QComboBox()
        self.folder_combo.setToolTip("보여 줄 폴더 고르기")
        self.folder_combo.currentIndexChanged.connect(self._on_folder_combo)

        def tbtn(name: str, tip: str, slot) -> QToolButton:
            b = QToolButton()
            b.setIcon(QIcon(icons.pixmap(name, "#d9dbe3", 16)))
            b.setToolTip(tip)
            b.clicked.connect(slot)
            return b

        self.btn_add = tbtn("plus", "폴더 추가", self.add_folder_dialog)
        self.btn_remove = tbtn("minus", "라이브러리에서 폴더 빼기 (파일은 지우지 않음)", self._remove_folder_menu)
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
        counts: dict[str, int] = {}
        for e in self._entries:
            counts[e.root] = counts.get(e.root, 0) + 1
        self.folder_combo.blockSignals(True)
        self.folder_combo.clear()
        if not self._folders:
            self.folder_combo.addItem("폴더를 추가하세요 (+)", "")
        else:
            self.folder_combo.addItem(f"모든 폴더 ({len(self._folders)}개)", "")
            for f in self._folders:
                n = counts.get(f)
                self.folder_combo.addItem(f"{Path(f).name or f}" + (f" · {n}개" if n is not None else ""), f)
        idx = self.folder_combo.findData(self._folder_filter)
        self.folder_combo.setCurrentIndex(max(0, idx))
        if idx < 0:
            self._folder_filter = ""
        self.folder_combo.blockSignals(False)
        self.btn_remove.setEnabled(bool(self._folders))

    def _on_folder_combo(self, _i: int) -> None:
        self._folder_filter = str(self.folder_combo.currentData() or "")
        self._apply_filter()

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

    def _remove_folder_menu(self) -> None:
        """− 버튼: 고른 폴더가 있으면 그 폴더를, 아니면 메뉴에서 골라 뺀다."""
        if self._folder_filter in self._folders:
            self.remove_folder(self._folder_filter)
            return
        if len(self._folders) == 1:
            self.remove_folder(self._folders[0])
            return
        from PySide6.QtGui import QCursor
        from PySide6.QtWidgets import QMenu
        m = QMenu(self)
        acts = {m.addAction(f"‘{Path(f).name or f}’ 빼기"): f for f in self._folders}
        a = m.exec(QCursor.pos())
        if a is not None:
            self.remove_folder(acts[a])

    def remove_folder(self, folder: str, confirm: bool = True) -> None:
        """라이브러리에서 폴더를 뺀다 (파일은 그대로)."""
        if folder not in self._folders:
            return
        if confirm:
            from PySide6.QtWidgets import QMessageBox
            n = sum(1 for e in self._entries if e.root == folder)
            r = QMessageBox.question(self, "폴더 빼기",
                                     f"‘{Path(folder).name or folder}’ 폴더를 라이브러리 목록에서 뺄까요?\n"
                                     f"(목록에서 {n}개가 사라집니다. 컴퓨터의 파일은 지우지 않습니다)")
            if r != QMessageBox.StandardButton.Yes:
                return
        self._folders.remove(folder)
        self._collapsed.discard(folder)
        if self._folder_filter == folder:
            self._folder_filter = ""
        self._refresh_combo()
        self.folders_changed.emit(self.folders)
        self.rescan()

    def toggle_collapsed(self, root: str) -> None:
        if root in self._collapsed:
            self._collapsed.discard(root)
        else:
            self._collapsed.add(root)
        self._apply_filter()

    def header_root(self, item: QListWidgetItem | None) -> str | None:
        """폴더 제목 줄이면 그 폴더 경로."""
        if item is None:
            return None
        v = item.data(self.HEADER_ROLE)
        return str(v) if v else None

    def is_library_folder(self, root: str) -> bool:
        return root in self._folders

    def rescan(self) -> None:
        folders = list(dict.fromkeys(self._folders))
        saved = [d for d in dict.fromkeys(self._extra) if d not in folders]
        self.lbl_count.setText("훑는 중...")

        def work() -> None:
            self._notifier.done.emit(scan_folders(folders, saved_folders=saved))

        threading.Thread(target=work, daemon=True).start()

    def _on_scanned(self, entries: object) -> None:
        self._entries = list(entries)  # type: ignore[arg-type]
        for e in self._entries:
            c = self._thumbs.get(e.path)
            if c:
                self._sph[e.path] = c[2]
        self._refresh_combo()
        self._apply_filter()

    def _is_saved(self, e: Entry) -> bool:
        return is_downloaded_name(e.name)

    def _passes(self, e: Entry) -> bool:
        if self._folder_filter and e.root != self._folder_filter:
            return False
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
        matched = [e for e in self._entries
                   if all(t in (e.name + " " + e.folder).lower() for t in tokens) and self._passes(e)]
        # 폴더별로 묶는다: 라이브러리 폴더 순서대로, 저장 폴더(미리 받기·녹화)는 마지막
        order = {f: i for i, f in enumerate(self._folders)}
        groups: dict[str, list[Entry]] = {}
        for e in matched:
            groups.setdefault(e.root, []).append(e)
        roots = sorted(groups, key=lambda r: order.get(r, len(order)))
        use_headers = len(roots) > 1 or (roots and not self._folder_filter and len(self._folders) > 1)
        for root in roots:
            if use_headers:
                self._add_header(root, len(groups[root]))
                if root in self._collapsed:
                    shown += len(groups[root])
                    continue
            for e in groups[root]:
                pending_e = self._add_entry_item(e)
                if pending_e:
                    pending.append(e.path)
                shown += 1
        total = len(self._entries)
        self.lbl_count.setText(f"{shown}개 표시 / 전체 {total}개" if shown != total else f"{total}개")
        if pending:
            self._start_thumbs(pending[:300])

    def _add_header(self, root: str, n: int) -> None:
        saved = root not in self._folders
        name = "저장 폴더 (미리 받기·녹화·녹음)" if saved else (Path(root).name or root)
        arrow = "▸" if root in self._collapsed else "▾"
        item = QListWidgetItem(f"{arrow}  {name}   ·  {n}개")
        item.setData(self.HEADER_ROLE, root)
        item.setToolTip(root + ("" if saved else "\n클릭: 접기/펼치기 · 오른쪽 클릭: 라이브러리에서 빼기"))
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        f = QFont()
        f.setBold(True)
        item.setFont(f)
        item.setForeground(QColor("#fbbf24"))
        item.setBackground(QColor("#1f2026"))
        item.setSizeHint(QSize(0, 30))
        self.list.addItem(item)

    def _add_entry_item(self, e: Entry) -> bool:
        """파일 한 줄을 넣는다. 썸네일을 새로 만들어야 하면 True."""
        item = QListWidgetItem()
        item.setToolTip(e.path)
        item.setData(Qt.ItemDataRole.UserRole, e.path)
        item.setSizeHint(QSize(0, THUMB_H + 14))
        cached = self._thumbs.get(e.path)
        if cached:
            self._apply_thumb(item, e, *cached)
        else:
            self._apply_thumb(item, e, "", 0.0, False)
        self.list.addItem(item)
        self._items[e.path] = item
        return not cached

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

    def _on_clicked(self, item: QListWidgetItem) -> None:
        root = self.header_root(item)
        if root is not None:
            # 클릭 처리 도중에 목록을 비우고 다시 채우면 Qt 가 지워진 항목을 건드려 죽을 수 있어 한 박자 늦춘다
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, lambda r=root: self.toggle_collapsed(r))

    def _on_activated(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            self.play_requested.emit(str(path))

    def focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()
