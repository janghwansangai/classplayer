"""영상 라이브러리: 지정한 폴더를 훑어 목록을 만들고 검색한다."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, QRectF, QSize, QStandardPaths, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QScrollArea, QToolButton, QVBoxLayout, QWidget)

from classplayer.bootstrap import AUDIO_EXTENSIONS, MEDIA_EXTENSIONS, NO_WINDOW, is_cloud_file
from classplayer.ui import icons
from classplayer.ui.now_playing import LIBRARY_COLOR, PLAY_ROLE, NowPlayingDelegate

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
        if is_cloud_file(path):
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


FOLDER_QSS = """
#folderrow { border: 1px solid transparent; border-radius: 8px; }
#folderrow:hover { background: #1f2026; }
#folderrow[selected="true"] { background: #3b2f12; border-color: #5a4512; }
#folderrow QLabel { background: transparent; }
#folderico { background: #26272e; border-radius: 7px; color: #d9dbe3; }
#folderico[save="true"] { background: #3b2f12; color: #fbbf24; }
QPushButton#mini { min-height: 22px; max-height: 22px; padding: 0 8px; border-radius: 6px; background: #26272e;
                   font-size: 11.5px; color: #d9dbe3; border: none; }
QPushButton#mini:hover { background: #30313a; }
QPushButton#mini[danger="true"] { color: #f87171; }
QPushButton#outline { background: transparent; border: 1px solid #f59e0b; color: #fbbf24; border-radius: 8px;
                      padding: 2px 10px; min-height: 24px; max-height: 26px; font-weight: 600; }
QPushButton#outline:hover { background: #3b2f12; }
QToolButton#foldhead { border: none; background: transparent; font-weight: 700; color: #e7e8ec; padding: 2px 0; }
"""

FILE_MANAGER = "Finder" if sys.platform == "darwin" else "탐색기"


def short_path(path: str) -> str:
    """홈 폴더는 ~ 로 줄여 보여 준다."""
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home) else path


class _ElideLabel(QLabel):
    """폭에 맞춰 가운데를 … 로 줄이는 한 줄 글 (긴 경로용)."""

    def __init__(self, text: str = "", parent=None, mode=Qt.TextElideMode.ElideMiddle) -> None:
        super().__init__(parent)
        self._full = text
        self._mode = mode
        self.setMinimumWidth(10)
        self.setText(text)

    def set_full(self, text: str) -> None:
        self._full = text
        self._update()

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._update()

    def _update(self) -> None:
        shown = self.fontMetrics().elidedText(self._full, self._mode, max(10, self.width()))
        super().setText(shown)
        # 줄여 보일 때는 마우스를 올리면 전체 글 (긴 폴더 이름·경로)
        self.setToolTip(self._full if shown != self._full else "")


class _FolderRow(QFrame):
    """'내 폴더' 한 줄: 아이콘 · 이름·영상 수 · 경로 · (열기 / 빼기 또는 바꾸기)."""

    clicked = Signal(str)

    def __init__(self, root: str, title: str, sub: str, icon: str, tip: str, actions: list, save: bool = False,
                 parent=None) -> None:
        super().__init__(parent)
        self.root = root
        self.setObjectName("folderrow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tip)
        ico = QLabel()
        ico.setObjectName("folderico")
        ico.setProperty("save", save)
        ico.setFixedSize(26, 26)
        ico.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ico.setPixmap(icons.pixmap(icon, "#fbbf24" if save else "#d9dbe3", 15))
        name = _ElideLabel(title, mode=Qt.TextElideMode.ElideRight)   # 긴 폴더 이름이 줄 폭을 밀어내지 않게
        name.setStyleSheet("font-weight:600; font-size:13px;")
        path = _ElideLabel(sub)
        path.setStyleSheet("color:#8b8e99; font-size:11px;")
        body = QVBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(name)
        body.addWidget(path)
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 5, 6, 5)
        row.setSpacing(8)
        row.addWidget(ico)
        row.addLayout(body, 1)
        for text, tip_a, slot, danger in actions:
            b = QPushButton(text)
            b.setObjectName("mini")
            b.setProperty("danger", danger)
            b.setToolTip(tip_a)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(slot)
            row.addWidget(b)

    def set_selected(self, on: bool) -> None:
        self.setProperty("selected", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.root)
        super().mousePressEvent(e)


class LibraryPanel(QWidget):
    play_requested = Signal(str)
    folders_changed = Signal(list)
    open_saved_requested = Signal()
    change_saved_requested = Signal()        # 저장 폴더 '바꾸기'

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
        self._now_playing = ""            # 라이브러리에서 재생 중인 파일 (파랑으로 표시)
        self._collapsed: set[str] = set()  # 접어 둔 폴더 묶음
        self._thumbs = LibraryThumbs(ffmpeg, ffprobe)
        self._thumb_gen = 0
        self._notifier = _ScanNotifier()
        self._notifier.done.connect(self._on_scanned)
        self._notifier.thumb.connect(self._on_thumb)

        self.search = QLineEdit()
        self.search.setToolTip("파일 이름이나 폴더 이름으로 찾기 (⌘⇧F) · 띄어 쓴 낱말은 모두 들어간 것만")
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
        self.list.setToolTip("두 번 누르면 재생 · 오른쪽 클릭: 재생목록에 추가·폴더에서 보기 · 폴더 제목 줄을 누르면 접기·펼치기")
        self.list.setIconSize(QSize(THUMB_W, THUMB_H))
        self.list.setSpacing(1)
        self.list.setUniformItemSizes(False)     # 폴더 제목 줄은 높이가 다르다
        # 긴 제목·폴더 이름은 줄을 바꿔 보여 준다: 제목 최대 2줄 + 폴더·크기 최대 2줄 (NowPlayingDelegate),
        # 전체 경로는 마우스를 올리면
        self.list.setItemDelegate(NowPlayingDelegate(self.list))
        self.list.itemClicked.connect(self._on_clicked)
        self.list.itemActivated.connect(self._on_activated)
        self.list.itemDoubleClicked.connect(self._on_activated)

        # ---- 시안 A: '내 폴더' — 넣은 폴더를 한 줄씩, 각 줄 옆에 그 폴더에 대한 버튼 (2026-11-17 사용자 선택)
        self._folders_open = True
        self.fold_btn = QToolButton()
        self.fold_btn.setObjectName("foldhead")
        self.fold_btn.setToolTip("내 폴더 목록 접기·펼치기 (영상 목록을 넓게 보고 싶을 때 접으세요)")
        self.fold_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.fold_btn.clicked.connect(self._toggle_folders)
        self.btn_add = QPushButton("＋ 폴더 추가")
        self.btn_add.setObjectName("outline")
        self.btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_add.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_add.setToolTip("컴퓨터의 폴더를 골라 그 안의 영상·음악을 라이브러리에 보여 줍니다 "
                                "(하위 폴더 포함 · 파일을 옮기거나 복사하지 않아요)")
        self.btn_add.clicked.connect(self.add_folder_dialog)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.addWidget(self.fold_btn)
        head.addStretch(1)
        head.addWidget(self.btn_add)
        self.rows_box = QWidget()
        self.rows_lay = QVBoxLayout(self.rows_box)
        self.rows_lay.setContentsMargins(0, 0, 0, 0)
        self.rows_lay.setSpacing(2)
        self.rows_scroll = QScrollArea()
        self.rows_scroll.setWidgetResizable(True)
        self.rows_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.rows_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.rows_scroll.setWidget(self.rows_box)
        self._rows: list[_FolderRow] = []
        folder_area = QWidget()
        folder_area.setStyleSheet(FOLDER_QSS)
        fa = QVBoxLayout(folder_area)
        fa.setContentsMargins(0, 0, 0, 0)
        fa.setSpacing(4)
        fa.addLayout(head)
        fa.addWidget(self.rows_scroll)
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background:#2a2b31;")
        self.lbl_count = QLabel("")
        self.lbl_count.setObjectName("muted")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 4, 10, 8)
        lay.setSpacing(8)
        lay.addWidget(folder_area)
        lay.addWidget(sep)
        lay.addWidget(self.search)
        lay.addLayout(chips)
        lay.addWidget(self.list, 1)
        lay.addWidget(self.lbl_count)
        # 새로 고침 버튼 대신: 폴더 안 파일이 바뀌면(맨 위 단계) 잠시 뒤 다시 훑고, 앱으로 돌아올 때도 다시 훑는다
        self._last_scan = 0.0
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(lambda _p: self._rescan_timer.start())
        self._rescan_timer = QTimer(self)
        self._rescan_timer.setSingleShot(True)
        self._rescan_timer.setInterval(1500)
        self._rescan_timer.timeout.connect(self.rescan)
        self._refresh_folders()
        self.rescan()

    # ------------------------------------------------------------------
    @property
    def folders(self) -> list[str]:
        return list(self._folders)

    def set_extra_folders(self, extra: list[str]) -> None:
        self._extra = list(extra)
        self._refresh_folders()
        self.rescan()

    def set_filter(self, key: str) -> None:
        self._filter = key
        if key in self.chips:
            self.chips[key].setChecked(True)
        self._apply_filter()

    def _refresh_folders(self) -> None:
        """'내 폴더' 줄들을 다시 만든다 (모든 폴더 보기 · 넣은 폴더들 · 저장 폴더)."""
        counts: dict[str, int] = {}
        for e in self._entries:
            counts[e.root] = counts.get(e.root, 0) + 1
        if self._folder_filter and self._folder_filter not in self._folders and self._folder_filter not in self._extra:
            self._folder_filter = ""
        while self.rows_lay.count():
            it = self.rows_lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self._rows = []

        def add(row: _FolderRow) -> None:
            row.clicked.connect(self._select_folder)
            row.set_selected(row.root == self._folder_filter)
            self.rows_lay.addWidget(row)
            self._rows.append(row)

        def cnt(root: str) -> str:
            return f" · {counts[root]}개" if root in counts else ""

        total = len(self._entries)
        add(_FolderRow("", "모든 폴더 보기", f"영상·음악 {total}개" if self._entries else "폴더의 영상·음악을 모두 보기",
                       "grid", "아래 목록에 모든 폴더의 영상·음악을 함께 보여 줍니다", []))
        for f in self._folders:
            add(_FolderRow(
                f, f"{Path(f).name or f}{cnt(f)}", short_path(f), "folder",
                f"{f}\n누르면 이 폴더의 영상만 아래에 보여요 (하위 폴더 포함)",
                [("열기", f"{FILE_MANAGER}에서 이 폴더 열기", lambda _c=False, r=f: self._open_dir(r), False),
                 ("빼기", "라이브러리 목록에서 이 폴더 빼기 (컴퓨터의 파일은 지우지 않아요)",
                  lambda _c=False, r=f: self.remove_folder(r), True)]))
        if not self._folders:
            hint = QLabel("＋ 폴더 추가로 수업 자료 폴더를 넣으면 그 안의 영상·음악이 여기 정리돼요")
            hint.setWordWrap(True)
            hint.setStyleSheet("color:#8b8e99; font-size:12px; padding:4px 8px;")
            self.rows_lay.addWidget(hint)
        for d in dict.fromkeys(self._extra):
            if d in self._folders:
                continue
            add(_FolderRow(
                d, f"저장 폴더{cnt(d)}", "미리 받기·녹화·녹음 · " + short_path(d), "download",
                f"{d}\n미리 받기·녹화·녹음한 파일이 저장되는 곳 · 누르면 저장한 것만 아래에 보여요",
                [("열기", f"{FILE_MANAGER}에서 저장 폴더 열기", lambda _c=False: self.open_saved_requested.emit(), False),
                 ("바꾸기", "미리 받기·녹화·녹음을 저장할 폴더 바꾸기", lambda _c=False: self.change_saved_requested.emit(),
                  False)], save=True))
        self.rows_lay.addStretch(1)
        n = len(self._folders)
        self.fold_btn.setText(f"{'▾' if self._folders_open else '▸'}  내 폴더  {n}")
        self.rows_scroll.setVisible(self._folders_open)
        rows_h = sum(r.sizeHint().height() + 2 for r in self._rows) + (40 if not self._folders else 0)
        self.rows_scroll.setFixedHeight(min(rows_h + 4, 250))

    def _toggle_folders(self) -> None:
        self._folders_open = not self._folders_open
        self._refresh_folders()

    def _select_folder(self, root: str) -> None:
        self._folder_filter = root
        for r in self._rows:
            r.set_selected(r.root == root)
        self._apply_filter()

    @staticmethod
    def _open_dir(path: str) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def add_folder_dialog(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "라이브러리에 보여 줄 폴더 고르기 (파일은 옮기지 않아요)")
        if d:
            self.add_folder(d)

    def add_folder(self, folder: str) -> None:
        if folder and folder not in self._folders:
            self._folders.append(folder)
            self._refresh_folders()
            self.folders_changed.emit(self.folders)
            self.rescan()

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
        self._refresh_folders()
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

    def maybe_rescan(self, min_gap: float = 20.0) -> None:
        """앱으로 돌아왔을 때 등: 마지막으로 훑은 지 min_gap 초가 지났으면 다시 훑는다."""
        if time.monotonic() - self._last_scan >= min_gap:
            self.rescan()

    def _watch(self, dirs: list[str]) -> None:
        old = self._watcher.directories()
        if old:
            self._watcher.removePaths(old)
        ok = [d for d in dirs if os.path.isdir(d)]
        if ok:
            self._watcher.addPaths(ok)

    def rescan(self) -> None:
        folders = list(dict.fromkeys(self._folders))
        saved = [d for d in dict.fromkeys(self._extra) if d not in folders]
        self._last_scan = time.monotonic()
        self._watch(folders + saved)
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
        self._refresh_folders()
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
        playing = e.path == self._now_playing
        meta = " · ".join(x for x in ("▶ 재생 중" if playing else "", kind, e.folder, _human_size(e.size)) if x)
        item.setText(f"{Path(e.name).stem}\n{meta}")
        f = item.font()
        f.setBold(playing)
        item.setFont(f)
        item.setData(PLAY_ROLE, LIBRARY_COLOR if playing else None)
        if playing:
            item.setForeground(QColor("#93c5fd"))
        else:
            from PySide6.QtGui import QBrush
            item.setForeground(QBrush())

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

    def set_now_playing(self, path: str) -> None:
        """라이브러리에서 재생 중인 파일 표시 (출처가 라이브러리가 아니면 "")."""
        old, self._now_playing = self._now_playing, path
        for p in {old, path}:
            item = self._items.get(p)
            e = next((x for x in self._entries if x.path == p), None) if item else None
            if item and e:
                c = self._thumbs.get(p)
                self._apply_thumb(item, e, *(c or ("", 0.0, self._sph.get(p, False))))

    def folder_label(self, path: str) -> str:
        """파일이 속한 라이브러리 폴더 이름 (저장 폴더면 '저장 폴더')."""
        e = next((x for x in self._entries if x.path == path), None)
        root = e.root if e else str(Path(path).parent)
        if root in self._extra and root not in self._folders:
            return "저장 폴더"
        return Path(root).name or root

    def reveal(self, path: str) -> None:
        """그 파일의 줄이 보이게: 폴더가 접혀 있으면 펴고, 걸러져 안 보이면 거르기를 풀고 스크롤."""
        e = next((x for x in self._entries if x.path == path), None)
        if e is None:
            return
        changed = False
        if e.root in self._collapsed:
            self._collapsed.discard(e.root)
            changed = True
        if path not in self._items:
            self.search.blockSignals(True)
            self.search.clear()
            self.search.blockSignals(False)
            self._filter = "all"
            self.chips["all"].setChecked(True)
            self._folder_filter = ""
            self._refresh_folders()
            changed = True
        if changed:
            self._apply_filter()
        item = self._items.get(path)
        if item:
            self.list.setCurrentItem(item)
            self.list.scrollToItem(item, QListWidget.ScrollHint.PositionAtCenter)

    def neighbor(self, path: str, delta: int) -> str | None:
        """라이브러리에서 이전·다음 파일 (지금 보이는 목록 순서, 접힌 폴더·제목 줄은 건너뜀)."""
        order = [str(self.list.item(i).data(Qt.ItemDataRole.UserRole)) for i in range(self.list.count())
                 if self.list.item(i).data(Qt.ItemDataRole.UserRole)]
        if path not in order:                       # 검색·칩으로 안 보이면 전체 순서에서
            order = [e.path for e in self._entries]
        if path not in order:
            return None
        j = order.index(path) + delta
        return order[j] if 0 <= j < len(order) else None

    def focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()
