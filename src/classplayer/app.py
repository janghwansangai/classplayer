"""ClassPlayer 메인 창."""
from __future__ import annotations

import datetime as _dt
import os
import sys
import threading
from pathlib import Path

import math
import urllib.parse

from PySide6.QtCore import QEvent, QObject, QPoint, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (QAction, QActionGroup, QCloseEvent, QColor, QDesktopServices, QDragEnterEvent, QDropEvent,
                           QIcon, QKeySequence, QPainter, QPen, QSurfaceFormat)
from PySide6.QtWidgets import (QAbstractSpinBox, QButtonGroup, QFrame, QApplication, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QDockWidget, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QProgressDialog, QPushButton,
                               QRadioButton, QScrollArea, QTextEdit, QToolButton, QVBoxLayout, QWidget)

from classplayer import __version__, bootstrap
from classplayer.bootstrap import APP_NAME, AUDIO_EXTENSIONS, MEDIA_EXTENSIONS, VIDEO_EXTENSIONS
from classplayer.record import LocalRecordJob, LocalRecordRequest, make_url_record_job
from classplayer.ui import icons
from classplayer.ui.stitch_dialog import StitchDialog
from classplayer.ui.playlist_panel import PlaylistPanel
from classplayer.library.playlist import PlaylistStore
from classplayer.vr.stitch import StitchParams, auto_align, grab_frames, identify_camera
from classplayer.updater import RELEASES_PAGE, Updater, can_self_install, is_newer
from classplayer.library.library import LibraryPanel
from classplayer.player.controls import ControlBar
from classplayer.player.mpv_widget import MpvWidget
from classplayer.player.thumbnails import ThumbnailProvider
from classplayer.player.controls import fmt_time
from classplayer.settings import Settings
from classplayer.ui.theme import AMBER, QSS, apply_palette
from classplayer.youtube.common import is_url, is_youtube_url, start_seconds, youtube_video_id
from classplayer.youtube.download import PRESETS, DownloadJob, DownloadRequest
from classplayer.youtube.probe import probe_spherical
from classplayer.youtube.search import SearchResult, YouTubeSearcher, fmt_views
from classplayer.youtube.sponsorblock import SponsorSkipper
from classplayer.youtube.stream import StreamResolver

HELP_TEXT = """\
Space  재생/일시정지        ←/→  10초 이동 (Shift: 60초)
↑/↓    볼륨                M     음소거
F      전체화면 (Esc 해제)  더블클릭 전체화면
3      360도 모드 켜기/끄기  R     시점 초기화
360도 모드: 드래그로 둘러보기, 휠/핀치로 확대
S      자막 전환            A     오디오 트랙 전환
[ / ]  재생 속도            , / . 한 프레임 이동
⌘L     링크 입력창          ⌘F    라이브러리 검색
⌘O     파일 열기            ⌘D    다운로드
⌘R     녹화·녹음            I / O 녹화 구간 시작 / 끝을 지금 위치로
⌘B     소리만 재생            N / P 재생목록 다음 / 이전 영상
"""


# ====================================================================== 다운로드 UI
class DownloadDialog(QDialog):
    def __init__(self, url: str, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("유튜브 영상 다운로드")
        self.setMinimumWidth(560)
        self.settings = settings

        self.url = QLineEdit(url)
        self.url.setPlaceholderText("https://www.youtube.com/watch?v=...")
        self.preset = QComboBox()
        for p in PRESETS:
            self.preset.addItem(p.label, p.key)
        idx = self.preset.findData(settings.download_preset)
        self.preset.setCurrentIndex(max(0, idx))
        self.subs = QCheckBox("자막 함께 저장 (한국어, 영어, 자동 생성 포함)")
        self.subs.setChecked(settings.download_subtitles)
        self.sponsor = QCheckBox("협찬/자체 홍보 구간 잘라내기 (SponsorBlock)")
        self.sponsor.setChecked(settings.download_remove_sponsors)
        self.playlist = QCheckBox("재생목록 링크면 전체 받기")
        self.folder = QLineEdit(settings.download_dir)
        browse = QPushButton("찾아보기…")
        browse.clicked.connect(self._browse)
        fl = QHBoxLayout()
        fl.addWidget(self.folder, 1)
        fl.addWidget(browse)

        form = QFormLayout()
        form.addRow("링크", self.url)
        form.addRow("화질/형식", self.preset)
        form.addRow("", self.subs)
        form.addRow("", self.sponsor)
        form.addRow("", self.playlist)
        form.addRow("저장 폴더", fl)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("다운로드 시작")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(QLabel("<small>1080p MP4는 어떤 기기에서도 열립니다. 360도 영상이나 4K 이상이 필요하면 '최고 화질'을 고르세요.</small>"))
        lay.addWidget(buttons)

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "저장 폴더", self.folder.text())
        if d:
            self.folder.setText(d)

    def request(self, ffmpeg: str | None, js_runtime: str | None) -> DownloadRequest:
        self.settings.download_preset = self.preset.currentData()
        self.settings.download_subtitles = self.subs.isChecked()
        self.settings.download_remove_sponsors = self.sponsor.isChecked()
        self.settings.download_dir = self.folder.text().strip()
        return DownloadRequest(
            url=self.url.text().strip(), out_dir=self.folder.text().strip(), preset=self.preset.currentData(),
            subtitles=self.subs.isChecked(), remove_sponsors=self.sponsor.isChecked(),
            playlist=self.playlist.isChecked(), ffmpeg_path=ffmpeg, js_runtime=js_runtime,
        )


def reveal_in_folder(path: str) -> None:
    """파일이 있는 폴더를 열고 그 파일을 선택해 보여 준다 (Finder / 탐색기)."""
    import subprocess
    p = Path(path)
    try:
        if sys.platform == "darwin" and p.exists():
            subprocess.Popen(["open", "-R", str(p)])
            return
        if sys.platform.startswith("win") and p.exists():
            subprocess.Popen(["explorer", "/select,", str(p)])
            return
    except Exception:  # noqa: BLE001
        pass
    folder = p if p.is_dir() else p.parent
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))


class ElideLabel(QLabel):
    """창 폭보다 긴 글은 끝을 '…'로 줄여 보여 주는 라벨 (전체 글은 툴팁). 패널 폭을 밀어내지 않는다."""

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(parent)
        self._full = ""
        self.setMinimumWidth(10)
        from PySide6.QtWidgets import QSizePolicy
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802
        self._full = text or ""
        self.setToolTip(self._full)
        self._update()

    def full_text(self) -> str:
        return self._full

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._update()

    def _update(self) -> None:
        w = max(10, self.width())
        super().setText(self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, w))


class DownloadItem(QWidget):
    play_requested = Signal(str)

    def __init__(self, job: DownloadJob, parent=None) -> None:
        super().__init__(parent)
        self.job = job
        self.title = ElideLabel(getattr(job, "display", "") or job.req.url)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.status = ElideLabel("대기 중")
        self.status.setObjectName("muted")
        self.btn = QToolButton()
        self.btn.setText("취소")
        self.btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn.clicked.connect(self._on_btn)
        self.btn_folder = QToolButton()
        self.btn_folder.setText("폴더")
        self.btn_folder.setToolTip("저장한 파일을 폴더에서 보기")
        self.btn_folder.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_folder.clicked.connect(lambda: reveal_in_folder(self._path))
        self.btn_folder.hide()
        row = QHBoxLayout()
        row.addWidget(self.bar, 1)
        row.addWidget(self.btn)
        row.addWidget(self.btn_folder)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.addWidget(self.title)
        lay.addLayout(row)
        lay.addWidget(self.status)
        self.setStyleSheet("DownloadItem { border-bottom: 1px solid #2a2b31; }")
        job.title_known.connect(self.title.setText)
        job.progress.connect(self._on_progress)
        job.finished.connect(self._on_finished)
        job.failed.connect(self._on_failed)
        import time as _t
        self._t0 = _t.time()
        self._last_text = ""
        self.bar.setRange(0, 0)          # 진행률을 모를 때는 움직이는 막대
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self.status.setText("시작하는 중…")
        self._path = ""

    def _tick(self) -> None:
        import time as _t
        sec = int(_t.time() - self._t0)
        kind = "녹음" if "녹음" in (self.title.full_text() or "") else ("녹화" if "녹화" in (self.title.full_text() or "") else "받기")
        msg = self._last_text if self._last_text and "0%" not in self._last_text else f"{kind} 진행 중"
        self.status.setText(f"{msg} · {sec // 60}:{sec % 60:02d} 경과")

    def _on_progress(self, frac: float, text: str) -> None:
        self._last_text = text
        if frac > 0.001:
            if self.bar.maximum() == 0:
                self.bar.setRange(0, 1000)
            self.bar.setValue(int(frac * 1000))
        self._tick()

    def _on_finished(self, path: str) -> None:
        self._timer.stop()
        self.bar.setRange(0, 1000)
        self._path = path
        if path:
            self.bar.setValue(1000)
            self.status.setText("완료: " + Path(path).name)
            self.btn.setText("재생")
            self.btn_folder.show()
        else:
            self.btn.setText("닫기")

    def _on_failed(self, msg: str) -> None:
        self._timer.stop()
        self.bar.setRange(0, 1000)
        self.bar.setValue(0)
        self.status.setStyleSheet("color: #f87171;")
        self.status.setText("실패: " + msg)

    def _on_btn(self) -> None:
        if self.job.is_running():
            self.job.cancel()
            self.status.setText("취소하는 중...")
        elif self._path:
            self.play_requested.emit(self._path)
        else:
            self.setParent(None)
            self.deleteLater()


class DownloadPanel(QWidget):
    play_requested = Signal(str)
    job_finished = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._jobs: list = []
        self.setObjectName("downloads")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.inner = QWidget()
        self.inner_lay = QVBoxLayout(self.inner)
        self.inner_lay.setContentsMargins(0, 0, 0, 0)
        self.inner_lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(self.inner)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(500)
        self.log.setPlaceholderText("yt-dlp 메시지")
        self.log.setMaximumHeight(120)
        self.log.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.log.setMinimumWidth(10)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(scroll, 1)
        lay.addWidget(self.log)

    def add_job(self, job: DownloadJob) -> None:
        self._jobs.append(job)
        item = DownloadItem(job)
        item.play_requested.connect(self.play_requested)
        job.log.connect(self.log.appendPlainText)
        job.finished.connect(self.job_finished)
        self.inner_lay.insertWidget(self.inner_lay.count() - 1, item)
        job.start()

    def cancel_all(self) -> None:
        for j in self._jobs:
            if j.is_running():
                j.cancel()

    def has_running(self) -> bool:
        return any(j.is_running() for j in self._jobs)


class _ProbeNotifier(QObject):
    result = Signal(str, object)


class _StitchNotifier(QObject):
    camera = Signal(str, object, bool)          # 경로, CameraInfo, 두 스트림 여부
    aligned = Signal(str, object, float, float, bool)  # 경로, StitchParams, 처음 오차, 최종 오차, 사용자가 누름
    failed = Signal(str, str)


class _CopyNotifier(QObject):
    progress = Signal(int)
    finished = Signal(str, str)


class SeekPreview(QFrame):
    """슬라이더 위에 뜨는 썸네일 + 시간 말풍선."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet("QFrame { background: #101114; border: 1px solid #3a3b44; border-radius: 9px; }"
                           "QLabel { color: white; border: none; }")
        self.img = QLabel()
        self.img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.img.setFixedSize(224, 126)
        self.time = QLabel("00:00")
        self.time.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.time.setStyleSheet("font-size: 15px; font-weight: 600;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)
        lay.addWidget(self.img)
        lay.addWidget(self.time)

    def show_at(self, pm, t: float, anchor: QPoint, has_source: bool = True) -> None:
        self.time.setText(fmt_time(t))
        if pm is not None and not pm.isNull():
            self.img.setPixmap(pm.scaled(self.img.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation))
            self.img.show()
        elif has_source:
            self.img.clear()
            self.img.setText("미리보기 불러오는 중…")
            self.img.setStyleSheet("color: #8b8e99; font-size: 12px;")
            self.img.show()
        else:
            self.img.clear()
            self.img.hide()      # 미리보기 재료가 없으면 시간만
        self.adjustSize()
        x = anchor.x() - self.width() // 2
        y = anchor.y() - self.height() - 10
        self.move(x, y)
        if not self.isVisible():
            self.show()


class Toast(QFrame):
    """영상 위 상단 중앙에 잠깐 뜨는 안내. 버튼 하나를 붙일 수 있다."""

    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("Toast { background: #101114; border: 1px solid #3a3b44; border-radius: 10px; }")
        self.label = QLabel("")
        self.label.setStyleSheet("color: #e7e8ec; font-size: 13px; border: none; background: transparent;")
        self.btn = QPushButton("")
        self.btn.setObjectName("toastbtn")
        # 알림 창 스타일이 앱 테마를 덮어쓰므로 버튼 색을 직접 지정 (어두운 바탕에서도 잘 보이게)
        self.btn.setStyleSheet("QPushButton { background: #f59e0b; color: #1b1206; font-weight: 700; border: none;"
                               " border-radius: 8px; min-height: 28px; padding: 0 12px; }"
                               "QPushButton:hover { background: #fbbf24; }")
        self.btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn.hide()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(12)
        lay.addWidget(self.label)
        lay.addWidget(self.btn)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        self._cb = None
        self.btn.clicked.connect(self._on_btn)
        self.hide()

    def _on_btn(self) -> None:
        if self._cb:
            self._cb()
        self.hide()

    def show_message(self, text: str, msec: int = 3500, button: str = "", callback=None) -> None:
        self.label.setText(text)
        self._cb = callback
        if button:
            self.btn.setText(button)
            self.btn.show()
        else:
            self.btn.hide()
        self.adjustSize()
        par = self.parentWidget()
        if par:
            self.move((par.width() - self.width()) // 2, 24)
        self.show()
        self.raise_()
        self._timer.start(msec)


class ClipboardBanner(QFrame):
    """클립보드에 유튜브 링크가 있을 때 위쪽에 뜨는 한 줄 안내."""

    play_clicked = Signal(str)
    download_clicked = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.url = ""
        self.setObjectName("banner")
        self.label = QLabel("")
        b_play = QPushButton("바로 재생")
        b_play.setObjectName("primary")
        b_dl = QPushButton("다운로드")
        b_close = QToolButton()
        b_close.setText("✕")
        b_close.setAutoRaise(True)
        for b in (b_play, b_dl, b_close):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b_play.clicked.connect(lambda: self.play_clicked.emit(self.url))
        b_dl.clicked.connect(lambda: self.download_clicked.emit(self.url))
        b_close.clicked.connect(self.hide)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 4, 6, 4)
        lay.addWidget(self.label, 1)
        lay.addWidget(b_play)
        lay.addWidget(b_dl)
        lay.addWidget(b_close)
        self.hide()

    def offer(self, url: str) -> None:
        self.url = url
        short = url if len(url) < 70 else url[:67] + "…"
        self.label.setText(f"복사해 둔 유튜브 링크가 있습니다: {short}")
        self.show()


class SearchPanel(QFrame):
    """링크 입력칸 아래에 뜨는 유튜브 검색 결과. 누르면 재생, 오른쪽 클릭으로 미리 받기·링크 복사."""

    play_requested = Signal(str, str)        # url, 제목
    download_requested = Signal(str)
    playlist_add_requested = Signal(str, str)   # url, 제목
    closed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("searchpanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.head = QLabel("")
        self.head.setObjectName("muted")
        b_close = QToolButton()
        b_close.setText("✕")
        b_close.setAutoRaise(True)
        b_close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b_close.setToolTip("닫기 (Esc)")
        b_close.clicked.connect(self.close_panel)
        top = QHBoxLayout()
        top.setContentsMargins(4, 0, 0, 0)
        top.addWidget(self.head, 1)
        top.addWidget(b_close)
        from PySide6.QtWidgets import QListWidget
        self.list = QListWidget()
        self.list.setObjectName("searchlist")
        self.list.setIconSize(QSize(128, 72))
        self.list.setSpacing(2)
        self.list.setUniformItemSizes(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.list.itemClicked.connect(self._activate)
        self.list.itemActivated.connect(self._activate)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        self.list.installEventFilter(self)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(6)
        lay.addLayout(top)
        lay.addWidget(self.list, 1)
        self._items: dict[str, object] = {}
        self.hide()

    def show_loading(self, query: str) -> None:
        self.head.setText(f"‘{query}’ 유튜브에서 찾는 중…")
        self.list.clear()
        self._items.clear()
        self.show()
        self.raise_()

    def show_error(self, query: str, msg: str) -> None:
        self.head.setText(f"‘{query}’ 검색에 실패했습니다: {msg}")

    def show_results(self, query: str, results: list) -> None:
        from PySide6.QtWidgets import QListWidgetItem
        self.list.clear()
        self._items.clear()
        self.head.setText(f"‘{query}’ 유튜브 검색 결과 {len(results)}개 · 누르면 재생, 오른쪽 클릭은 미리 받기"
                          if results else f"‘{query}’ 검색 결과가 없습니다")
        ph = icons.pixmap("play", "#5b5e69", 22)
        for r in results:
            meta = " · ".join(x for x in (r.channel, "실시간" if r.is_live else (fmt_time(r.duration) if r.duration else ""),
                                          fmt_views(r.views)) if x)
            it = QListWidgetItem(QIcon(ph), f"{r.title}\n{meta}")
            it.setData(Qt.ItemDataRole.UserRole, r)
            it.setToolTip(r.title)
            it.setSizeHint(QSize(0, 80))
            self.list.addItem(it)
            self._items[r.video_id] = it
        if results:
            self.list.setCurrentRow(0)
        self.show()
        self.raise_()

    def set_thumb(self, vid: str, data: bytes) -> None:
        it = self._items.get(vid)
        if it is None:
            return
        from PySide6.QtGui import QPixmap
        pm = QPixmap()
        if pm.loadFromData(data):
            it.setIcon(QIcon(pm.scaled(256, 144, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                       Qt.TransformationMode.SmoothTransformation)))

    def _activate(self, it) -> None:
        r = it.data(Qt.ItemDataRole.UserRole)
        if r is not None:
            self.close_panel()
            self.play_requested.emit(r.url, r.title)

    def _menu(self, pos) -> None:
        it = self.list.itemAt(pos)
        if it is None:
            return
        r = it.data(Qt.ItemDataRole.UserRole)
        from PySide6.QtWidgets import QMenu
        m = QMenu(self)
        a_play = m.addAction("재생")
        a_dl = m.addAction("미리 받기 (파일로 저장)…")
        a_pl = m.addAction("재생목록에 추가")
        a_copy = m.addAction("링크 복사")
        a = m.exec(self.list.viewport().mapToGlobal(pos))
        if a is a_play:
            self._activate(it)
        elif a is a_pl:
            self.playlist_add_requested.emit(r.url, r.title)
        elif a is a_dl:
            self.close_panel()
            self.download_requested.emit(r.url)
        elif a is a_copy:
            QApplication.clipboard().setText(r.url)

    def close_panel(self) -> None:
        if self.isVisible():
            self.hide()
            self.closed.emit()

    def eventFilter(self, obj, e) -> bool:  # noqa: N802
        if obj is self.list and e.type() == QEvent.Type.KeyPress and e.key() == Qt.Key.Key_Escape:
            self.close_panel()
            return True
        return super().eventFilter(obj, e)


class Compass(QWidget):
    """360도 모드에서 오른쪽 위에 뜨는 시점 나침반. 호박색 부채꼴이 지금 보는 방향과 시야각."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(76, 76)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.yaw = 0.0
        self.fov = math.radians(90)
        self.hide()

    def set_view(self, yaw: float, fov: float) -> None:
        self.yaw, self.fov = yaw, fov
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(1, 1, self.width() - 2, self.height() - 2)
        p.setPen(QPen(QColor(255, 255, 255, 40), 1))
        p.setBrush(QColor(16, 17, 20, 200))
        p.drawEllipse(r)
        inner = r.adjusted(12, 12, -12, -12)
        p.setPen(QPen(QColor(255, 255, 255, 70), 1, Qt.PenStyle.DashLine))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(inner)
        # Qt 각도: 3시 방향이 0, 반시계 + (1/16도 단위). 정면(yaw=0)은 12시.
        span = math.degrees(self.fov)
        center = 90 - math.degrees(self.yaw)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(245, 158, 11, 210))
        wedge = r.adjusted(18, 18, -18, -18)
        p.drawPie(wedge, int((center - span / 2) * 16), int(span * 16))
        p.setBrush(QColor("#fbbf24"))
        c = r.center()
        p.drawEllipse(c, 3, 3)
        p.setPen(QColor(255, 255, 255, 150))
        f = p.font()
        f.setPixelSize(9)
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(c.x() - 10, r.top() + 1, 20, 12), Qt.AlignmentFlag.AlignCenter, "앞")
        p.end()


class TimeField(QLineEdit):
    """시:분:초 입력칸 (00:00:00 틀)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setInputMask("99:99:99")
        self.setMinimumWidth(110)
        self.setMaximumWidth(130)

    def seconds(self) -> float:
        parts = [int(p) if p.strip().isdigit() else 0 for p in self.text().split(":")]
        while len(parts) < 3:
            parts.append(0)
        h, m, sec = parts[:3]
        return float(h * 3600 + m * 60 + sec)

    def set_seconds(self, sec: float) -> None:
        s = int(round(max(0.0, sec)))
        h, rem = divmod(s, 3600)
        m, ss = divmod(rem, 60)
        self.setText(f"{min(h, 99):02d}:{m:02d}:{ss:02d}")


class RecordDialog(QDialog):
    """녹화·녹음 창. 열어 둔 채로 영상을 움직이며 '지금 위치'로 시작·끝을 정할 수 있다."""

    marks_changed = Signal(object, object)       # 시작, 끝 (초) 또는 None
    start_requested = Signal(dict)

    def __init__(self, parent, *, title: str, duration: float, position: float, is_link: bool,
                 is_local_video: bool, out_dir: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("녹화 · 녹음")
        self.setModal(False)
        # 탐색바를 눌러도 메인 창 뒤로 숨지 않게 '도구 창'으로 띄운다
        self.setWindowFlag(Qt.WindowType.Tool, True)
        self.setMinimumWidth(500)
        self.duration = duration
        self._pos_fn = None

        head = QLabel(f"<b style='font-size:15px'>지금 보는 영상을 파일로 저장합니다</b><br>"
                      f"<span style='color:#8b8e99'>{title}</span>")
        head.setWordWrap(True)

        self.r_video = QRadioButton("영상 녹화")
        self.r_audio = QRadioButton("소리만 녹음 (MP3)")
        self.r_video.setChecked(True)
        g1 = QButtonGroup(self)
        g1.addButton(self.r_video)
        g1.addButton(self.r_audio)
        kind = QHBoxLayout()
        kind.addWidget(self.r_video)
        kind.addWidget(self.r_audio)
        kind.addStretch(1)

        self.r_all = QRadioButton("전체")
        self.r_part = QRadioButton("구간 정하기")
        self.r_part.setChecked(True)
        g2 = QButtonGroup(self)
        g2.addButton(self.r_all)
        g2.addButton(self.r_part)
        rng = QHBoxLayout()
        rng.addWidget(self.r_all)
        rng.addWidget(self.r_part)
        rng.addStretch(1)

        self.t_start = TimeField()
        self.t_end = TimeField()
        end0 = min(duration, position + 30) if duration > 0 else position + 30
        self.t_start.set_seconds(position)
        self.t_end.set_seconds(end0)
        for te in (self.t_start, self.t_end):
            te.textChanged.connect(self._changed)
        b_s = QPushButton("지금 위치")
        b_e = QPushButton("지금 위치")
        b_s.setToolTip("재생 중인 위치를 시작으로 (단축키 I)")
        b_e.setToolTip("재생 중인 위치를 끝으로 (단축키 O)")
        b_s.clicked.connect(lambda: self.set_start_now())
        b_e.clicked.connect(lambda: self.set_end_now())
        times = QFormLayout()
        row_s = QHBoxLayout()
        row_s.addWidget(self.t_start)
        row_s.addWidget(b_s)
        row_s.addStretch(1)
        row_e = QHBoxLayout()
        row_e.addWidget(self.t_end)
        row_e.addWidget(b_e)
        row_e.addStretch(1)
        times.addRow("시작", row_s)
        times.addRow("끝", row_e)
        self.lbl_len = QLabel("")
        self.lbl_len.setObjectName("muted")
        times.addRow("", self.lbl_len)
        self.part_box = QWidget()
        self.part_box.setLayout(times)

        self.accurate = QCheckBox("정확한 시각에 맞춰 자르기 (다시 인코딩, 시간이 더 걸림)")
        self.accurate.setToolTip("끄면 몇 초 만에 저장되지만 시작이 앞쪽 장면 전환 지점에 맞춰 1~2초 이를 수 있습니다")
        self.accurate.setVisible(is_local_video)
        note = QLabel(("유튜브·드라이브 영상은 해당 부분만 다시 받아서 저장합니다 (인터넷 필요)."
                       if is_link else "원본 파일에서 잘라 저장합니다. 인터넷이 필요 없습니다.")
                      + f"<br>저장 위치: <b>{out_dir}</b> · 라이브러리 <b>녹화·녹음</b> 목록에 나타납니다.")
        note.setWordWrap(True)
        note.setObjectName("muted")

        self.btn_go = QPushButton("녹화 시작")
        self.btn_go.setObjectName("primary")
        self.btn_go.clicked.connect(self._go)
        b_close = QPushButton("닫기")
        b_close.clicked.connect(self.close)
        btns = QHBoxLayout()
        btns.addStretch(1)
        btns.addWidget(b_close)
        btns.addWidget(self.btn_go)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(10)
        lay.addWidget(head)
        lay.addSpacing(4)
        lay.addLayout(kind)
        lay.addLayout(rng)
        lay.addWidget(self.part_box)
        lay.addWidget(self.accurate)
        lay.addWidget(note)
        lay.addLayout(btns)

        for rb in (self.r_all, self.r_part, self.r_video, self.r_audio):
            rb.toggled.connect(self._changed)
        if duration <= 0:
            self.r_all.setEnabled(False)
        self._changed()

    def set_position_source(self, fn) -> None:
        self._pos_fn = fn

    def set_range(self, a: float | None, b: float | None) -> None:
        """탐색바에서 정한 구간을 시간 칸에 반영."""
        self.r_part.setChecked(True)
        if a is not None:
            self.t_start.set_seconds(a)
        if b is not None:
            self.t_end.set_seconds(b)

    def set_start_now(self) -> None:
        if self._pos_fn:
            self.r_part.setChecked(True)
            self.t_start.set_seconds(self._pos_fn())
            if self.t_end.seconds() <= self.t_start.seconds():
                end = self._pos_fn() + 30
                self.t_end.set_seconds(min(end, self.duration) if self.duration > 0 else end)

    def set_end_now(self) -> None:
        if self._pos_fn:
            self.r_part.setChecked(True)
            self.t_end.set_seconds(self._pos_fn())

    def range(self) -> tuple[float | None, float | None]:
        if self.r_all.isChecked():
            return None, None
        a, b = self.t_start.seconds(), self.t_end.seconds()
        if self.duration > 0:
            b = min(b, self.duration)
        return a, b

    def _changed(self, *_a) -> None:
        if not hasattr(self, "btn_go"):      # 만드는 도중 시간칸 신호가 먼저 올 때
            return
        part = self.r_part.isChecked()
        self.part_box.setEnabled(part)
        a, b = self.range()
        audio = self.r_audio.isChecked()
        self.accurate.setEnabled(not audio)
        self.btn_go.setText("녹음 시작" if audio else "녹화 시작")
        if part:
            ok = b is not None and a is not None and b - a >= 0.5
            self.lbl_len.setText(f"길이 {fmt_time(b - a)}" if ok else "끝이 시작보다 뒤여야 합니다")
            self.btn_go.setEnabled(ok)
        else:
            self.btn_go.setEnabled(True)
        self.marks_changed.emit(a, b)

    def _go(self) -> None:
        a, b = self.range()
        self.start_requested.emit({"start": a, "end": b, "audio": self.r_audio.isChecked(),
                                   "accurate": self.accurate.isChecked() and self.accurate.isVisible()})

    def closeEvent(self, e) -> None:
        self.marks_changed.emit(None, None)
        super().closeEvent(e)


# ====================================================================== 메인 창
class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = Settings()
        self.ytdlp = bootstrap.find_tool("yt-dlp")
        self.ffmpeg = bootstrap.find_tool("ffmpeg")
        self.ffprobe = bootstrap.find_tool("ffprobe")
        self.js_runtime = next((r for r in ("deno", "node", "bun") if bootstrap.find_tool(r)), None)
        if self.ffmpeg:   # yt-dlp 구간 받기는 PATH 의 ffmpeg 만 본다
            bootstrap.add_to_path(str(Path(self.ffmpeg).parent))
        # 어린이용 영상 403 대응: PO 토큰 실행 파일이 있으면 yt-dlp 플러그인을 켠다 (yt-dlp 를 쓰기 전에)
        self.pot_cli = bootstrap.enable_po_token_plugin()

        self.setWindowTitle(APP_NAME)
        self.resize(1280, 820)
        self.setAcceptDrops(True)
        self._current: str = ""
        self._current_is_youtube = False
        self._fullscreen = False
        self._dock_state: dict[str, bool] = {}
        self._probe = _ProbeNotifier()
        self._copy_sig = _CopyNotifier()
        self._copy_sig.progress.connect(self._on_copy_progress)
        self._copy_sig.finished.connect(self._on_copy_finished)
        self._probe.result.connect(self._on_probe_result)
        self._stitch_sig = _StitchNotifier()
        self._stitch_sig.camera.connect(self._on_stitch_camera)
        self._stitch_sig.aligned.connect(self._on_stitch_aligned)
        self._stitch_sig.failed.connect(self._on_stitch_failed)
        self._stitch_cam = None
        self._stitch_dialog: StitchDialog | None = None
        self._stitch_busy = False

        # --- 플레이어
        self.player = MpvWidget(
            ytdl_path=self.ytdlp, js_runtime=self.js_runtime,
            stream_max_height=self.settings.stream_max_height,
            vr_hq=self.settings.vr_hq, vr_max_magnification=self.settings.vr_max_magnification,
        )
        self.player.set_volume(self.settings.volume)
        if self.settings.audio_device and self.settings.audio_device != "auto":
            try:
                self.player.mpv["audio-device"] = self.settings.audio_device
            except Exception:  # noqa: BLE001
                pass
        self.controls = ControlBar()
        self.controls.set_volume(self.settings.volume)

        # --- 링크 입력줄
        logo = QLabel()
        logo.setObjectName("logo")
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setPixmap(icons.pixmap("play", "#1b1206", 14))
        brand = QLabel("ClassPlayer")
        brand.setObjectName("brand")
        self.url_edit = QLineEdit()
        self.url_edit.setObjectName("url")
        self.url_edit.setPlaceholderText("유튜브 링크를 붙여넣거나, 노래·영상 제목을 쓰고 Enter (유튜브 검색)")
        self.url_edit.setClearButtonEnabled(True)
        self.url_edit.addAction(QIcon(icons.pixmap("link", "#8b8e99", 16)), QLineEdit.ActionPosition.LeadingPosition)
        self.url_edit.returnPressed.connect(self.play_from_url_bar)
        self.url_edit.textEdited.connect(self._on_url_typed)

        def topbtn(text: str, name: str, color: str = "#e7e8ec") -> QPushButton:
            b = QPushButton(text)
            b.setIcon(QIcon(icons.pixmap(name, color, 16)))
            b.setIconSize(QSize(16, 16))
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            return b

        btn_paste = topbtn("붙여넣고 재생", "play", "#1b1206")
        btn_paste.setObjectName("primary")
        btn_paste.setToolTip("복사한 링크를 바로 재생 (⌘V). 입력칸에 쓴 링크가 있으면 그것을 재생")
        btn_paste.clicked.connect(self.play_from_clipboard)
        btn_dl = topbtn("미리 받기", "download")
        btn_dl.setToolTip("수업 전에 유튜브 영상을 파일로 받아 두기 (⌘D)")
        btn_dl.clicked.connect(self.open_download_dialog)
        btn_open = topbtn("파일", "folder")
        btn_open.clicked.connect(self.open_file_dialog)
        urlrow = QHBoxLayout()
        urlrow.setContentsMargins(14, 9, 14, 9)
        urlrow.setSpacing(10)
        urlrow.addWidget(logo)
        urlrow.addWidget(brand)
        urlrow.addSpacing(6)
        urlrow.addWidget(self.url_edit, 1)
        urlrow.addWidget(btn_paste)
        urlrow.addWidget(btn_dl)
        urlrow.addWidget(btn_open)
        self.url_bar = QWidget()
        self.url_bar.setObjectName("urlbar")
        self.url_bar.setLayout(urlrow)

        central = QWidget()
        central.setObjectName("central")
        v = QVBoxLayout(central)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self.url_bar)
        self.banner = ClipboardBanner()
        self.banner.play_clicked.connect(self._banner_play)
        self.banner.download_clicked.connect(self._banner_download)
        v.addWidget(self.banner)
        self.stage = QWidget()            # 영상 + (전체화면 때) 떠 있는 컨트롤바
        self.stage.setStyleSheet("background: #000;")
        sv = QVBoxLayout(self.stage)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.addWidget(self.player)
        self.stage.installEventFilter(self)
        self.toast = Toast(self.stage)
        self.loading = QLabel("", self.stage)
        self.loading.setObjectName("loading")
        self.loading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.loading.hide()
        self.hud = QWidget(self.stage)
        self.hud.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        hl = QHBoxLayout(self.hud)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(6)
        self.hud_vr = QLabel("")
        self.hud_vr.setObjectName("hudamber")
        self.hud_codec = QLabel("")
        self.hud_codec.setObjectName("hud")
        self.hud_speed = QLabel("")
        self.hud_speed.setObjectName("hudamber")
        self.hud_subs = QLabel("자막 켜짐")
        self.hud_subs.setObjectName("hud")
        self.hud_rec = QLabel("")
        self.hud_rec.setObjectName("hudrec")
        self.hud_pl = QLabel("")
        self.hud_pl.setObjectName("hud")
        for w in (self.hud_rec, self.hud_pl, self.hud_vr, self.hud_subs, self.hud_codec, self.hud_speed):
            hl.addWidget(w)
            w.hide()
        self.hud.move(16, 14)
        self.hud.hide()
        self._hud_timer = QTimer(self)
        self._hud_timer.setSingleShot(True)
        self._hud_timer.setInterval(4000)
        self._hud_timer.timeout.connect(self._hide_hud_info)
        self.compass = Compass(self.stage)
        self._audio_only = False
        v.addWidget(self.stage, 1)
        v.addWidget(self.controls)
        self._central_layout = v
        self.setCentralWidget(central)

        # --- 도크: 라이브러리, 다운로드
        self.library = LibraryPanel(self.settings.library_folders, [self.settings.download_dir],
                                    ffmpeg=self.ffmpeg, ffprobe=self.ffprobe)
        self.library.play_requested.connect(self.open_target)
        self.library.open_saved_requested.connect(self.open_save_folder)
        self.library.folders_changed.connect(lambda f: setattr(self.settings, "library_folders", f))
        self.dock_library = QDockWidget("라이브러리", self)
        self.dock_library.setObjectName("dock_library")
        self.dock_library.setWidget(self.library)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.dock_library)

        # --- 재생목록 (라이브러리 옆 탭)
        self.playlists = PlaylistStore(parent=self)
        self.playlist_panel = PlaylistPanel(self.playlists)
        self.playlist_panel.play_requested.connect(self.play_playlist)
        self.playlist_panel.add_current_requested.connect(self._add_current_to_playlist)
        self.playlists.position_changed.connect(lambda _n, _i: self._refresh_playlist_hud())
        self.dock_playlist = QDockWidget("재생목록", self)
        self.dock_playlist.setObjectName("dock_playlist")
        self.dock_playlist.setWidget(self.playlist_panel)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.dock_playlist)
        self.tabifyDockWidget(self.dock_library, self.dock_playlist)
        self.dock_library.raise_()
        self._from_playlist = False
        self.library.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.library.list.customContextMenuRequested.connect(self._library_menu)

        self.downloads = DownloadPanel()
        self.downloads.play_requested.connect(self.open_target)
        self.downloads.job_finished.connect(lambda _p: self.library.rescan())
        self.dock_downloads = QDockWidget("다운로드", self)
        self.dock_downloads.setObjectName("dock_downloads")
        self.dock_downloads.setWidget(self.downloads)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock_downloads)
        self.dock_downloads.hide()

        # --- 상태 표시줄
        self.lbl_title = QLabel("")
        self.lbl_info = QLabel("")
        self.lbl_info.setObjectName("muted")
        self.lbl_vr = QLabel("")
        self.statusBar().addWidget(self.lbl_title, 1)
        self.statusBar().addPermanentWidget(self.lbl_vr)
        self.statusBar().addPermanentWidget(self.lbl_info)
        self._hwdec = ""
        self._codec = ""

        # --- SponsorBlock
        self.skipper = SponsorSkipper(self.settings.sponsorblock_categories)
        self.skipper.request_seek.connect(lambda t: self.player.seek_absolute(t, exact=True))
        self.skipper.skipped.connect(lambda label, secs: self.statusBar().showMessage(f"{label} 구간 {secs:.0f}초 건너뜀", 4000))
        self.skipper.segments_loaded.connect(
            lambda vid, n: self.statusBar().showMessage(f"SponsorBlock: 건너뛸 구간 {n}개", 3000) if n else None)

        # --- 유튜브 주소 풀기
        self.resolver = StreamResolver(self.js_runtime, self.settings.stream_max_height,
                                       js_runtime_path=bootstrap.find_tool(self.js_runtime) if self.js_runtime else None,
                                       pot_cli=self.pot_cli)
        self.resolver.warm_up()
        self.resolver.resolved.connect(self._on_stream_resolved)
        self.resolver.failed.connect(self._on_stream_failed)

        # --- 전체화면 컨트롤 자동 숨김
        self._hide_timer = QTimer(self)
        self._hide_timer.setInterval(2500)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._auto_hide_controls)

        # --- 탐색 미리보기
        self.thumbs = ThumbnailProvider(self.ffmpeg)
        self.preview = SeekPreview()
        self._preview_t = 0.0
        self.thumbs.ready.connect(self._refresh_preview)
        self.controls.preview_hover.connect(self._on_preview_hover)
        self.controls.preview_leave.connect(self.preview.hide)
        self.controls.seek_preview.connect(lambda t: self._on_preview_hover(t, None))

        # --- 클립보드 유튜브 링크 감지
        self._last_clip = ""
        QApplication.instance().applicationStateChanged.connect(self._on_app_state)
        QApplication.clipboard().dataChanged.connect(self._check_clipboard)

        # --- 유튜브 검색
        self.searcher = YouTubeSearcher(self)
        self.search_panel = SearchPanel(self)
        self.searcher.results.connect(self._on_search_results)
        self.searcher.failed.connect(lambda q, m: self.search_panel.show_error(q, m))
        self.searcher.thumb.connect(self.search_panel.set_thumb)
        self.search_panel.play_requested.connect(self._play_search_result)
        self.search_panel.download_requested.connect(self._download_search_result)
        self.search_panel.playlist_add_requested.connect(lambda u, t: self._add_to_playlist_menu(u, t))
        self.search_panel.closed.connect(lambda: self.url_edit.setFocus())
        self.url_edit.installEventFilter(self)

        # --- 녹화 · 업데이트
        self._rec_dialog: RecordDialog | None = None
        self.updater = Updater(self)
        self.updater.checked.connect(self._on_update_checked)
        self.updater.progress.connect(self._on_update_progress)
        self.updater.prepared.connect(self._on_update_prepared)
        self.updater.failed.connect(self._on_update_failed)
        self._update_silent = True
        self._update_dialog: QProgressDialog | None = None
        self._release = None
        QTimer.singleShot(8000, self._auto_check_update)

        self._connect_player()
        self._build_menus()
        QApplication.instance().installEventFilter(self)
        self.player.setFocus()

    # ------------------------------------------------------------------ 연결
    def _connect_player(self) -> None:
        p, c = self.player, self.controls
        p.position_changed.connect(c.set_position)
        p.position_changed.connect(self.skipper.on_position)
        p.duration_changed.connect(c.set_duration)
        p.pause_changed.connect(c.set_paused)
        p.volume_changed.connect(lambda v: c.set_volume(int(v)))
        p.mute_changed.connect(c.set_muted)
        p.media_title_changed.connect(self._on_title)
        p.file_loaded.connect(self._on_file_loaded)
        p.hwdec_changed.connect(self._on_hwdec)
        p.codec_changed.connect(self._on_codec)
        p.video_size_changed.connect(lambda _w, _h: self._update_info())
        p.log_message.connect(self._on_log)
        p.clicked.connect(self.player.toggle_pause)
        p.double_clicked.connect(self.toggle_fullscreen)
        p.mouse_moved.connect(self._on_mouse_moved)
        p.wheel_scrolled.connect(lambda d: self._nudge_volume(5 * d))
        p.vr_changed.connect(self._on_vr_changed)
        p.buffering_changed.connect(self._on_buffering)
        p.load_failed.connect(self._on_load_failed)
        p.eof_reached.connect(self._on_eof)
        p.subs_changed.connect(self._on_subs_changed)
        self.resolver.started.connect(self._on_resolve_started)

        c.play_clicked.connect(p.toggle_pause)
        c.relative_seek.connect(p.seek_relative)
        c.seek_requested.connect(lambda t: p.seek_absolute(t, exact=True))
        c.volume_changed.connect(self._set_volume)
        c.mute_clicked.connect(p.toggle_mute)
        c.speed_changed.connect(p.set_speed)
        c.subs_clicked.connect(self.cycle_subtitles)
        c.vr_toggled.connect(self.set_vr)
        c.vr_reset_clicked.connect(p.vr_reset)
        c.fullscreen_clicked.connect(self.toggle_fullscreen)
        c.screenshot_clicked.connect(self.take_screenshot)
        c.record_clicked.connect(self.open_record_dialog)
        c.range_edited.connect(self._on_range_edited)
        c.range_context.connect(self._on_range_context)
        c.audio_only_toggled.connect(lambda on: self.a_audio_only.setChecked(on))
        c.speed_changed.connect(self._show_speed_hud)

    def _build_menus(self) -> None:
        mb = self.menuBar()

        def act(menu, text, slot, shortcut=None, checkable=False, checked=False):
            a = QAction(text, self)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            a.setCheckable(checkable)
            a.setChecked(checked)
            a.triggered.connect(slot)
            menu.addAction(a)
            return a

        m_file = mb.addMenu("파일")
        act(m_file, "파일 열기…", self.open_file_dialog, "Ctrl+O")
        act(m_file, "링크 입력창으로", lambda: (self.url_edit.setFocus(), self.url_edit.selectAll()), "Ctrl+L")
        act(m_file, "유튜브 다운로드…", self.open_download_dialog, "Ctrl+D")
        act(m_file, "복사한 링크 붙여넣고 재생", self.play_from_clipboard, "Ctrl+V")
        self.m_recent = m_file.addMenu("최근 항목")
        self.m_recent.aboutToShow.connect(self._fill_recent)
        m_file.addSeparator()
        act(m_file, "저장 폴더 열기 (녹화·녹음·미리 받기)", self.open_save_folder, "Ctrl+Shift+O")
        act(m_file, "캡처 폴더 열기", lambda: self._open_folder(Path(self.settings.capture_dir)))
        act(m_file, "라이브러리 폴더 추가…", self.library.add_folder_dialog)
        act(m_file, "종료", self.close, "Ctrl+Q")

        m_play = mb.addMenu("재생")
        act(m_play, "재생/일시정지", self.player.toggle_pause)
        act(m_play, "정지", self.player.stop)
        act(m_play, "10초 뒤로", lambda: self.player.seek_relative(-10))
        act(m_play, "10초 앞으로", lambda: self.player.seek_relative(10))
        act(m_play, "자막 전환", self.player.cycle_subtitles)
        act(m_play, "오디오 트랙 전환", self.player.cycle_audio)
        act(m_play, "화면 캡처", self.take_screenshot)
        self.a_sponsor = act(m_play, "협찬 구간 자동 건너뛰기 (SponsorBlock)", self._toggle_sponsor, checkable=True, checked=True)
        self.a_audio_only = act(m_play, "소리만 재생 (영상 끄기)", self.toggle_audio_only, "Ctrl+B", checkable=True)
        m_play.addSeparator()
        act(m_play, "다음 영상 (재생목록, N)", self.playlist_next)
        act(m_play, "이전 영상 (재생목록, P)", self.playlist_prev)
        act(m_play, "재생목록 보기", self.show_playlist, "Ctrl+Shift+L")
        act(m_play, "지금 영상을 재생목록에 추가", lambda: self._add_to_playlist_menu(self._current, self.lbl_title.text() if is_url(self._current) else "") if self._current else self.notify("먼저 영상을 열어 주세요"))
        m_play.addSeparator()
        act(m_play, "녹화 · 녹음…", self.open_record_dialog, "Ctrl+R")
        act(m_play, "녹화 구간 시작 = 지금 위치 (I)", lambda: self._mark_now(True))
        act(m_play, "녹화 구간 끝 = 지금 위치 (O)", lambda: self._mark_now(False))
        act(m_play, "녹화·녹음 목록 보기", self.show_recordings)

        m_view = mb.addMenu("보기")
        act(m_view, "전체화면", self.toggle_fullscreen, "Ctrl+F")
        self.a_vr = act(m_view, "360도 모드", lambda on: self.set_vr(on), checkable=True)
        act(m_view, "시점 초기화", self.player.vr_reset)
        m_proj = m_view.addMenu("360도 투영 방식")
        g = QActionGroup(self)
        self.a_proj = {}
        for key, label in (("equirect", "등장방형 (일반 360도 파일)"), ("eac", "유튜브 EAC 큐브맵"),
                           ("dfisheye", "듀얼 피쉬아이 (Insta360 원본 insv)")):
            a = act(m_proj, label, lambda _c, k=key: self._set_projection(k), checkable=True, checked=(key == "equirect"))
            g.addAction(a)
            self.a_proj[key] = a
        act(m_view, "360 이음새 맞추기…", self.open_stitch_dialog, "Ctrl+Shift+3")
        m_fe = m_view.addMenu("듀얼 피쉬아이 설정")
        g_fe = QActionGroup(self)
        for fov in (180.0, 190.0, 200.0, 210.0):
            a = act(m_fe, f"렌즈 화각 {fov:g}°", lambda _c, f=fov: self._set_fisheye(fov=f), checkable=True, checked=(fov == 190.0))
            g_fe.addAction(a)
        act(m_fe, "앞/뒤 렌즈 좌우 바꾸기", lambda on: self._set_fisheye(swap=on), checkable=True)
        m_stereo = m_view.addMenu("입체(3D) 배치")
        g2 = QActionGroup(self)
        self.a_stereo = {}
        for key, label in (("mono", "없음 (모노)"), ("tb", "상하 분할"), ("sbs", "좌우 분할")):
            a = act(m_stereo, label, lambda _c, k=key: self._set_stereo(k), checkable=True, checked=(key == "mono"))
            g2.addAction(a)
            self.a_stereo[key] = a
        m_view.addSeparator()
        self.a_hq = act(m_view, "360도 고품질 표본화 (바이큐빅)", self._toggle_hq, checkable=True, checked=self.settings.vr_hq)
        m_mag = m_view.addMenu("360도 확대 한계 (화질 보호)")
        g3 = QActionGroup(self)
        cur = self.settings.vr_max_magnification
        for val, label in ((2.0, "2배까지 (가장 선명)"), (3.0, "3배까지 (기본)"), (4.0, "4배까지"), (100.0, "제한 없음")):
            a = act(m_mag, label, lambda _c, v=val: self._set_max_mag(v), checkable=True, checked=abs(cur - val) < 0.01)
            g3.addAction(a)
        m_view.addSeparator()
        m_view.addAction(self.dock_library.toggleViewAction())
        m_view.addAction(self.dock_downloads.toggleViewAction())
        self.dock_library.toggleViewAction().setShortcut(QKeySequence("Ctrl+1"))
        self.dock_downloads.toggleViewAction().setShortcut(QKeySequence("Ctrl+2"))
        act(m_view, "라이브러리 검색", self._focus_search, "Ctrl+Shift+F")

        m_set = mb.addMenu("설정")
        m_q = m_set.addMenu("유튜브 스트리밍 최대 화질")
        g4 = QActionGroup(self)
        for h, label in ((720, "720p"), (1080, "1080p (기본)"), (1440, "1440p"), (2160, "4K"), (4320, "8K")):
            a = act(m_q, label, lambda _c, hh=h: self._set_stream_height(hh), checkable=True,
                    checked=(h == self.settings.stream_max_height))
            g4.addAction(a)
        act(m_set, "다운로드 폴더 변경…", self._change_download_dir)
        act(m_set, "캡처 저장 폴더 변경…", self._change_capture_dir)
        self.m_audio = m_set.addMenu("소리 출력 장치")
        self.m_audio.aboutToShow.connect(self._fill_audio_devices)
        m_set.addSeparator()
        act(m_set, "기본 영상 플레이어로 등록…", self.register_default_player)

        m_help = mb.addMenu("도움말")
        act(m_help, "단축키", lambda: QMessageBox.information(self, "단축키", f"<pre>{HELP_TEXT}</pre>"))
        act(m_help, "환경 정보", self._show_env)
        m_help.addSeparator()
        act(m_help, "업데이트 확인…", lambda: self.check_update(silent=False))
        act(m_help, "오류 신고…", self.report_bug)

    # ------------------------------------------------------------------ 열기/재생
    def play_from_url_bar(self) -> None:
        text = self.url_edit.text().strip()
        if not text:
            return
        if self._is_search_text(text):
            self.search_youtube(text)
            return
        self.open_target(text)
        self.player.setFocus()

    # ------------------------------------------------------------------ 유튜브 검색
    @staticmethod
    def _is_search_text(text: str) -> bool:
        t = text.strip()
        return bool(t) and not is_url(t) and not Path(os.path.expanduser(t)).exists()

    def search_youtube(self, query: str) -> None:
        self.search_panel.show_loading(query)
        self._place_search_panel()
        self.searcher.search(query)

    def _on_search_results(self, query: str, results) -> None:
        self.search_panel.show_results(query, results)
        self._place_search_panel()
        self.search_panel.list.setFocus()

    def _place_search_panel(self) -> None:
        sp = self.search_panel
        if not sp.isVisible():
            return
        tl = self.url_edit.mapTo(self, QPoint(0, self.url_edit.height() + 6))
        w = max(560, self.url_edit.width())
        x = min(tl.x(), max(0, self.width() - w - 8))
        h = min(620, max(240, self.height() - tl.y() - 40))
        sp.setGeometry(x, tl.y(), min(w, self.width() - 16), h)
        sp.raise_()

    def _play_search_result(self, url: str, title: str) -> None:
        self.open_target(url)
        self.player.setFocus()

    def _download_search_result(self, url: str) -> None:
        self.url_edit.setText(url)
        self.open_download_dialog()

    def open_file_dialog(self) -> None:
        exts = " ".join(f"*{e}" for e in sorted(MEDIA_EXTENSIONS))
        path, _ = QFileDialog.getOpenFileName(self, "영상 파일 열기", "", f"영상 파일 ({exts});;모든 파일 (*)")
        if path:
            self.open_target(path)

    def open_target(self, target: str) -> None:
        target = target.strip()
        if not target:
            return
        if not is_url(target) and not Path(target).exists():
            QMessageBox.warning(self, APP_NAME, f"파일을 찾을 수 없습니다:\n{target}")
            return
        self._current = target
        self._current_is_youtube = is_youtube_url(target)
        self.url_edit.setText(target)
        self.skipper.clear()
        self.thumbs.clear()
        self.preview.hide()
        self.banner.hide()
        self._last_clip = QApplication.clipboard().text().strip()
        self.set_vr(False)
        self.settings.add_recent(target)
        self.search_panel.close_panel()
        if not self._from_playlist and self.playlists.active:
            self.playlists.stop()
        self._client_step = 0
        self._awaiting_resolve = False
        self._mpv_fallback = False
        self._refetching = False
        if self._current_is_youtube:
            self.lbl_title.setText("유튜브 정보 가져오는 중…")
            self.resolver.resolve(target)
        elif is_url(target):
            # 구글 드라이브, 비메오 등: yt-dlp 가 아는 주소면 앱 안에서 풀고, 모르면 mpv 가 직접 연다
            self.lbl_title.setText("영상 정보 가져오는 중…")
            self._show_loading("영상 주소를 확인하는 중…")
            self.resolver.resolve(target)
        else:
            self.resolver.cancel()
            if bootstrap.is_cloud_placeholder(target) and not self._ask_cloud_file(target):
                return
            self.lbl_title.setText("여는 중…")
            self._show_loading("여는 중…")
            self.player.play(target)
            self._probe_local(target)
        self.player.setFocus()

    # ------------------------------------------------------------------ 클라우드(구글 드라이브 등) 파일
    def _ask_cloud_file(self, path: str) -> bool:
        """아직 내려받지 않은 드라이브 파일: 먼저 내려받을지 묻는다. True면 그냥 재생."""
        size_gb = os.path.getsize(path) / (1 << 30)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("드라이브 파일")
        box.setText(f"이 파일은 아직 이 컴퓨터에 내려받지 않은 드라이브 파일입니다 ({size_gb:.1f} GB).")
        box.setInformativeText("그대로 재생하면 드라이브에서 받아 오면서 틀기 때문에 자주 멈추고 탐색이 느립니다.\n"
                               "수업 전에 미리 내려받아 두는 것을 권합니다 (다운로드 폴더에 복사됩니다).")
        b_dl = box.addButton("내려받고 재생", QMessageBox.ButtonRole.AcceptRole)
        b_play = box.addButton("그냥 재생", QMessageBox.ButtonRole.ActionRole)
        box.addButton("취소", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(b_dl)
        box.exec()
        clicked = box.clickedButton()
        if clicked is b_dl:
            self._copy_cloud_file(path)
            return False
        return clicked is b_play

    def _copy_cloud_file(self, src: str) -> None:
        dst_dir = Path(self.settings.download_dir)
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / Path(src).name
        if dst.exists() and dst.stat().st_size == os.path.getsize(src) and not bootstrap.is_cloud_placeholder(str(dst)):
            self.open_target(str(dst))
            return
        self._current = ""
        self.lbl_title.setText("드라이브에서 내려받는 중…")
        self._show_loading("드라이브에서 내려받는 중… 0%")
        total = max(1, os.path.getsize(src))
        tmp = dst.with_suffix(dst.suffix + ".part")

        def work() -> None:
            try:
                done = 0
                with open(src, "rb") as fi, open(tmp, "wb") as fo:
                    while True:
                        chunk = fi.read(8 << 20)
                        if not chunk:
                            break
                        fo.write(chunk)
                        done += len(chunk)
                        self._copy_sig.progress.emit(done * 100 // total)
                os.replace(tmp, dst)
                self._copy_sig.finished.emit(str(dst), "")
            except Exception as e:  # noqa: BLE001
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
                self._copy_sig.finished.emit("", str(e))

        threading.Thread(target=work, daemon=True).start()

    def _on_copy_progress(self, pct: int) -> None:
        self._show_loading(f"드라이브에서 내려받는 중… {pct}%")
        self.statusBar().showMessage(f"드라이브에서 내려받는 중… {pct}%")

    def _on_copy_finished(self, dst: str, err: str) -> None:
        self.loading.hide()
        if err or not dst:
            self.notify(f"내려받기 실패: {err}", 8000)
            return
        self.notify("내려받기 완료. 라이브러리 다운로드 폴더에 저장했습니다", 5000)
        self.library.rescan()
        self.open_target(dst)

    def _on_stream_resolved(self, rs) -> None:
        if rs.url != self._current:
            return
        self._awaiting_resolve = False
        how = "저장해 둔 주소" if self.resolver.last_from_cache else f"주소 확인 {self.resolver.last_seconds:.1f}초"
        self.statusBar().showMessage(f"유튜브: {how}", 6000)
        self._show_loading("영상을 불러오는 중…")
        self.player.play_stream(rs.video_url, rs.audio_url, title=rs.title, user_agent=rs.user_agent,
                                subtitles=rs.subtitles, start=start_seconds(rs.url))
        self._on_title(rs.title)
        if rs.storyboards:
            self.thumbs.load_storyboard(rs.storyboards)
        vid = rs.video_id or youtube_video_id(rs.url)
        if vid and self.a_sponsor.isChecked():
            self.skipper.load(vid)

    def _on_stream_failed(self, url: str, message: str) -> None:
        if url != self._current:
            return
        self._awaiting_resolve = False
        # 앱 내부 풀기에 실패하면 mpv 가 직접 연다 (일반 영상 주소, 내장 ytdl 훅)
        if self._current_is_youtube:
            self.notify(f"유튜브 주소를 풀지 못했습니다 ({message}). 다른 방식으로 다시 시도합니다", 8000)
        self._show_loading("영상을 여는 중…")
        self._mpv_fallback = True
        self.player.play(url)
        if self._current_is_youtube:
            self._retry_storyboard(url)
        vid = youtube_video_id(url)
        if vid and self.a_sponsor.isChecked():
            self.skipper.load(vid)

    def _retry_storyboard(self, url: str) -> None:
        """다른 방법(mpv)으로 열었을 때도 탐색 미리보기를 쓸 수 있게 뒤에서 다시 주소를 풀어 본다."""
        self.resolver.prefetch(url)
        tries = {"n": 0}

        def poll() -> None:
            if url != self._current:
                return
            rs = self.resolver.cached(url)
            if rs is not None and rs.storyboards:
                self.thumbs.load_storyboard(rs.storyboards)
                return
            tries["n"] += 1
            if tries["n"] < 30:
                QTimer.singleShot(2000, poll)

        QTimer.singleShot(2000, poll)

    def _on_load_failed(self, err: int) -> None:
        cur = self._current
        if not cur or getattr(self, "_awaiting_resolve", False):
            return      # 새 주소를 받는 중에 온 (이전 시도의) 실패 알림은 무시
        if self._current_is_youtube and self.resolver.last_from_cache and not getattr(self, "_refetching", False):
            # 저장해 둔 주소가 거절됨 (인터넷 연결이 바뀌었거나 만료) → 버리고 새로 받는다
            self._refetching = True
            self.resolver.forget(cur)
            self.resolver.last_from_cache = False
            self._show_loading("저장해 둔 주소가 만료되어 새로 확인하는 중…")
            self._awaiting_resolve = True
            self.resolver.resolve(cur)
            return
        step = getattr(self, "_client_step", 0)
        fallbacks = self.resolver.fallback_clients
        if self._current_is_youtube and step < len(fallbacks) and not getattr(self, "_mpv_fallback", False):
            # 어린이용 영상 등은 기본 방식 주소가 403 으로 막힌다 → 다른 방식으로 다시 받는다
            self._client_step = step + 1
            self.resolver.forget(cur)
            self._show_loading(f"다른 방법으로 다시 여는 중… ({step + 1}/{len(fallbacks)})")
            self._awaiting_resolve = True
            self.resolver.resolve(cur, clients=fallbacks[step])
            return
        self._refetching = False
        self.loading.hide()
        if self._current_is_youtube:
            self.notify("유튜브가 이 영상의 앱 재생을 막았습니다 (어린이용 영상 등)", 15000,
                        "유튜브에서 열기", lambda u=cur: QDesktopServices.openUrl(QUrl(u)))
        else:
            self.notify("영상을 열지 못했습니다. 인터넷 연결이나 파일을 확인해 주세요", 7000)
        if self.playlists.active:
            QTimer.singleShot(2500, self.playlist_next)      # 재생목록이면 다음 영상으로

    def _probe_local(self, path: str) -> None:
        ffprobe = self.ffprobe

        def work() -> None:
            self._probe.result.emit(path, probe_spherical(ffprobe, path))

        threading.Thread(target=work, daemon=True).start()

    def _on_probe_result(self, path: str, info: object) -> None:
        if path != self._current:
            return
        if getattr(info, "projection", "") == "dfisheye" and getattr(info, "is_spherical", False):
            dual = bool(getattr(info, "dual_stream", False))
            if dual:
                self._show_loading("두 렌즈 영상을 합치는 중…")
            ffprobe = self.ffprobe

            def work(p=path, d=dual) -> None:
                self._stitch_sig.camera.emit(p, identify_camera(p, ffprobe), d)

            threading.Thread(target=work, daemon=True).start()
        elif getattr(info, "dual_stream", False):
            self._show_loading("두 렌즈 영상을 합치는 중…")
            self.player.set_dual_stream(True)
        if getattr(info, "is_spherical", False):
            self.set_vr(True, projection=info.projection, stereo=info.stereo)  # type: ignore[union-attr]
            self.statusBar().showMessage("360도 영상으로 감지되어 360도 모드를 켰습니다 (3 키로 끄기)", 6000)
        elif path.lower().endswith((".insv", ".insp")) and getattr(info, "video_streams", 1) == 1:
            self.notify("이 insv 파일에는 렌즈 하나의 영상만 들어 있습니다. 짝이 되는 _10_ 파일과 함께 Insta360 Studio로 합쳐 주세요", 9000)

    # ------------------------------------------------------------------ 재생목록
    def play_playlist(self, name: str, idx: int = 0) -> None:
        item = self.playlists.start(name, idx)
        if item is None:
            self.notify("재생목록이 비어 있습니다. 영상을 추가해 주세요", 4000)
            return
        self._open_playlist_item(item)

    def _open_playlist_item(self, item) -> None:
        if not item.is_link and not Path(item.target).exists():
            self.notify(f"파일을 찾지 못해 건너뜁니다: {Path(item.target).name}", 4000)
            QTimer.singleShot(800, self.playlist_next)
            return
        self._from_playlist = True
        try:
            self.open_target(item.target)
        finally:
            self._from_playlist = False

    def playlist_next(self) -> None:
        if not self.playlists.active:
            self.notify("재생목록을 재생 중일 때 다음 영상으로 넘어갈 수 있습니다", 3000)
            return
        item = self.playlists.next()
        if item is None:
            self.playlists.stop()
            self.notify("재생목록을 끝까지 재생했습니다", 4000)
            return
        self._open_playlist_item(item)

    def playlist_prev(self) -> None:
        if not self.playlists.active:
            return
        item = self.playlists.prev()
        if item is not None:
            self._open_playlist_item(item)

    def _on_eof(self) -> None:
        if self.playlists.active and not self._fullscreen_seeking():
            QTimer.singleShot(600, self.playlist_next)

    def _fullscreen_seeking(self) -> bool:
        return self.controls.slider.isSliderDown()

    def _refresh_playlist_hud(self) -> None:
        name, i = self.playlists.active, self.playlists.index
        p = self.playlists.get(name) if name else None
        if p and i >= 0:
            self.hud_pl.setText(f"{name} {i + 1}/{len(p.items)}")
            self.hud_pl.show()
        else:
            self.hud_pl.hide()
        self._refresh_hud()

    def _add_current_to_playlist(self, name: str) -> None:
        if not self._current:
            self.notify("먼저 영상을 열어 주세요", 3000)
            return
        title = self.lbl_title.text() if is_url(self._current) else ""
        if self.playlists.add(name, self._current, title):
            self.notify(f"'{name}'에 추가했습니다", 2500)

    def _add_to_playlist_menu(self, target: str, title: str = "") -> None:
        """재생목록 고르기 메뉴를 띄워 추가한다 (목록이 하나면 바로)."""
        from PySide6.QtGui import QCursor
        from PySide6.QtWidgets import QMenu
        names = self.playlists.names()
        if len(names) == 1:
            name = names[0]
        else:
            m = QMenu(self)
            acts = {m.addAction(n): n for n in names}
            m.addSeparator()
            a_new = m.addAction("새 재생목록 만들기…")
            a = m.exec(QCursor.pos())
            if a is None:
                return
            if a is a_new:
                from PySide6.QtWidgets import QInputDialog
                n, ok = QInputDialog.getText(self, "새 재생목록", "목록 이름", text=self.playlists.unique_name("새 목록"))
                if not ok:
                    return
                name = self.playlists.create(n)
            else:
                name = acts[a]
        if self.playlists.add(name, target, title):
            self.playlist_panel.select_list(name)
            self.notify(f"'{name}'에 추가했습니다", 2500, "목록 보기", self.show_playlist)

    def show_playlist(self) -> None:
        self.dock_playlist.show()
        self.dock_playlist.raise_()

    def _library_menu(self, pos) -> None:
        from PySide6.QtWidgets import QMenu
        it = self.library.list.itemAt(pos)
        if it is None:
            return
        path = str(it.data(Qt.ItemDataRole.UserRole) or "")
        m = QMenu(self)
        a_play = m.addAction("재생")
        a_pl = m.addAction("재생목록에 추가")
        a_reveal = m.addAction("폴더에서 보기")
        a = m.exec(self.library.list.viewport().mapToGlobal(pos))
        if a is a_play:
            self.open_target(path)
        elif a is a_pl:
            self._add_to_playlist_menu(path)
        elif a is a_reveal:
            reveal_in_folder(path)

    # ------------------------------------------------------------------ 360 이음새 (두 렌즈)
    def _stitch_key(self, cam) -> str:
        return "stitch/" + cam.key

    def _on_stitch_camera(self, path: str, cam, dual: bool) -> None:
        if path != self._current:
            return
        self._stitch_cam = cam
        if dual:
            self.player.set_dual_stream(True, order=cam.dual_stream_order)
        saved = self.settings.q.value(self._stitch_key(cam), "")
        params = None
        if saved:
            try:
                params = StitchParams.from_json(str(saved))
            except Exception:  # noqa: BLE001
                params = None
        self._apply_stitch(params or cam.defaults.copy(), save=False)
        if self._stitch_dialog is not None:
            self._stitch_dialog.set_camera(cam.label)
        if params is None and not bootstrap.is_cloud_placeholder(path):
            # 이 카메라를 처음 본다: 뒤에서 자동으로 맞춘다
            QTimer.singleShot(1500, lambda p=path: self.auto_stitch(user=False, path=p))

    def _apply_stitch(self, params: StitchParams, save: bool = True) -> None:
        self.player.vr.stitch = params.copy()
        self.player.update()
        if self._stitch_dialog is not None:
            self._stitch_dialog.set_params(params)
        if save and self._stitch_cam is not None:
            self.settings.q.setValue(self._stitch_key(self._stitch_cam), params.to_json())

    def open_stitch_dialog(self) -> None:
        if self.player.vr.projection != "dfisheye" or not self.player.vr_enabled:
            self.notify("두 렌즈(듀얼 피쉬아이) 360 영상을 열면 이음새를 맞출 수 있습니다 (insv, lrv, OSV, LRF 등)", 5000)
            return
        if self._stitch_dialog is None:
            d = StitchDialog(self)
            d.params_changed.connect(lambda p: self._apply_stitch(p, save=True))
            d.auto_requested.connect(lambda: self.auto_stitch(user=True))
            d.reset_requested.connect(self._reset_stitch)
            self._stitch_dialog = d
        d = self._stitch_dialog
        d.set_camera(self._stitch_cam.label if self._stitch_cam else "알 수 없음 (일반 듀얼 피쉬아이)")
        d.set_params(self.player.vr.stitch)
        d.set_busy(self._stitch_busy)
        d.show()
        d.raise_()

    def _reset_stitch(self) -> None:
        cam = self._stitch_cam
        if cam is not None:
            self.settings.q.remove(self._stitch_key(cam))
        self._apply_stitch(cam.defaults.copy() if cam else StitchParams(), save=False)
        if self._stitch_dialog is not None:
            self._stitch_dialog.set_status("카메라 기본값으로 되돌렸습니다")

    def auto_stitch(self, user: bool = True, path: str | None = None) -> None:
        path = path or self._current
        if not path or is_url(path) or path != self._current or self._stitch_busy:
            return
        if not self.ffmpeg:
            return
        cam = self._stitch_cam
        start = self.player.vr.stitch.copy()
        dual = self.player.dual_stream
        order = cam.dual_stream_order if (cam and dual) else None
        dur = float(self.player.property("duration", 0.0) or 0.0)
        ffmpeg = self.ffmpeg
        self._stitch_busy = True
        if self._stitch_dialog is not None:
            self._stitch_dialog.set_busy(True)
            self._stitch_dialog.set_status("영상 세 장면을 비교하는 중… (몇 초 걸립니다)")
        if user:
            self.statusBar().showMessage("360 이음새를 자동으로 맞추는 중…", 5000)

        def work() -> None:
            try:
                frames = grab_frames(ffmpeg, path, dur, order, tile=384)
                if len(frames) < 1:
                    raise RuntimeError("장면을 꺼내지 못했습니다")
                res, first, final = auto_align(start, frames)
                res.blend = start.blend
                self._stitch_sig.aligned.emit(path, res, first, final, user)
            except Exception as e:  # noqa: BLE001
                self._stitch_sig.failed.emit(path, str(e))

        threading.Thread(target=work, daemon=True).start()

    def _on_stitch_aligned(self, path: str, params, first: float, final: float, user: bool) -> None:
        self._stitch_busy = False
        if self._stitch_dialog is not None:
            self._stitch_dialog.set_busy(False)
        if path != self._current:
            return
        better = (1 - final / first) * 100 if first > 0 else 0.0
        self._apply_stitch(params, save=True)
        msg = f"360 이음새를 자동으로 맞췄습니다 (어긋남 {better:.0f}% 줄임)" if better > 1 else "이미 잘 맞아 있어 바꾸지 않았습니다"
        if self._stitch_dialog is not None:
            self._stitch_dialog.set_status(msg + f" · 화각 {(params.fov_front + params.fov_back) / 2:.1f}°")
        self.notify(msg, 6000, "조절하기", self.open_stitch_dialog)

    def _on_stitch_failed(self, path: str, msg: str) -> None:
        self._stitch_busy = False
        if self._stitch_dialog is not None:
            self._stitch_dialog.set_busy(False)
            self._stitch_dialog.set_status("자동 맞추기 실패: " + msg)

    # ------------------------------------------------------------------ 영상 위 상태 표시(HUD)
    def _refresh_hud(self) -> None:
        vr_on = self.player.vr_enabled
        v = self.player.vr
        self.hud_vr.setText(f"360° · 시야 {math.degrees(v.fov):.0f}°" if vr_on else "")
        self.hud_vr.setVisible(vr_on)
        any_shown = any(not w.isHidden() for w in (self.hud_rec, self.hud_pl, self.hud_vr, self.hud_subs, self.hud_codec, self.hud_speed))
        self.hud.adjustSize()
        self.hud.setVisible(any_shown and (bool(self._current) or not self.hud_rec.isHidden()))
        self.hud.raise_()
        self.compass.setVisible(vr_on)
        if vr_on:
            self.compass.set_view(v.yaw, v.fov)
            self.compass.move(self.stage.width() - self.compass.width() - 16, 14)
            self.compass.raise_()

    def _show_hud_info(self) -> None:
        """파일을 연 직후 몇 초 동안 코덱·해상도·디코딩 방식을 보여 준다."""
        w, h = self.player.video_size
        codec = self._codec.split(" ")[0].upper() if self._codec else ""
        if not codec:
            return
        hw = "하드웨어" if self._hwdec and self._hwdec != "no" else "소프트웨어"
        self.hud_codec.setText(" · ".join(x for x in (codec, f"{w}×{h}" if w else "", hw) if x))
        self.hud_codec.show()
        self._refresh_hud()
        self._hud_timer.start()

    def _hide_hud_info(self) -> None:
        self.hud_codec.hide()
        self._refresh_hud()

    def _show_speed_hud(self, speed: float) -> None:
        self.hud_speed.setText(f"{speed:g}×")
        self.hud_speed.setVisible(abs(speed - 1.0) > 1e-3)
        self._refresh_hud()

    # ------------------------------------------------------------------ 유튜브 로딩
    def _on_resolve_started(self, url: str) -> None:
        if self.resolver.cached(url):
            self._show_loading("영상을 여는 중…")
        else:
            self._show_loading("유튜브에서 영상 주소를 확인하는 중… (1/2)\n보통 3~5초 걸립니다")

    def _on_url_typed(self, text: str) -> None:
        t = text.strip()
        prev = getattr(self, "_url_prev_text", "")
        self._url_prev_text = t
        if is_youtube_url(t):
            self.resolver.prefetch(t)
        # 입력칸에 링크를 붙여넣은 경우(한 번에 긴 글이 들어옴)는 바로 재생한다
        pasted = len(t) - len(prev) > 10 and t == QApplication.clipboard().text().strip()
        if pasted and is_url(t) and t != self._current:
            QTimer.singleShot(0, lambda u=t: self.open_target(u))

    # ------------------------------------------------------------------ 녹화 · 녹음
    def _record_source(self) -> tuple[str, bool]:
        """(원본, 링크 여부). 녹화할 수 없으면 ('', False)."""
        cur = self._current
        if not cur:
            return "", False
        if is_url(cur):
            return cur, True
        return (cur, False) if Path(cur).exists() else ("", False)

    def _on_subs_changed(self, on: bool) -> None:
        self.controls.set_subs(on)
        self.hud_subs.setVisible(on)
        self._refresh_hud()

    def _rec_running(self) -> bool:
        return any(j.is_running() for j in getattr(self, "_rec_jobs", []))

    def _update_rec_status(self) -> None:
        import time as _t
        jobs = [j for j in getattr(self, "_rec_jobs", []) if j.is_running()]
        if not jobs:
            self._rec_timer.stop()
            self.hud_rec.hide()
            self.controls.set_recording(False)
            self._refresh_hud()
            return
        sec = int(_t.time() - self._rec_t0)
        txt = f"● 녹화 중 {sec // 60}:{sec % 60:02d}"
        self.hud_rec.setText(txt)
        self.hud_rec.show()
        self.controls.set_recording(True, f"{txt} · 끝나면 다시 녹화할 수 있습니다")
        self._refresh_hud()

    def open_record_dialog(self) -> None:
        if self._rec_running():
            self.controls.set_recording(True)
            self.dock_downloads.show()
            self.notify("녹화가 진행 중입니다. 끝난 뒤 다시 녹화할 수 있습니다 (진행 상황은 오른쪽 '다운로드' 창)", 5000)
            return
        src, is_link = self._record_source()
        if not src:
            self.notify("먼저 영상을 열어 주세요. 열려 있는 영상의 전체나 일부를 파일로 저장합니다")
            return
        if self._rec_dialog is not None:
            self._rec_dialog.show()
            self._rec_dialog.raise_()
            self._rec_dialog.activateWindow()
            return
        title = self.lbl_title.text() or Path(src).stem
        dur = float(self.player.property("duration", 0.0) or 0.0)
        pos = float(self.player.property("time-pos", 0.0) or 0.0)
        local_video = not is_link and Path(src).suffix.lower() not in AUDIO_EXTENSIONS
        dlg = RecordDialog(self, title=title, duration=dur, position=pos, is_link=is_link,
                           is_local_video=local_video, out_dir=self.settings.download_dir)
        dlg.set_position_source(lambda: float(self.player.property("time-pos", 0.0) or 0.0))
        dlg.marks_changed.connect(self.controls.set_marks)
        dlg.start_requested.connect(lambda opts, s=src, link=is_link, t=title, d=dur: self._start_record(s, link, t, d, opts))
        dlg.finished.connect(lambda _r: setattr(self, "_rec_dialog", None))
        dlg.destroyed.connect(lambda *_: self.controls.set_marks(None, None))
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._rec_dialog = dlg
        dlg.show()
        dlg._changed()

    def _on_range_edited(self, a: float, b: float) -> None:
        if self._rec_dialog is None:
            self.open_record_dialog()
        if self._rec_dialog is not None:
            self._rec_dialog.set_range(a, b)

    def _on_range_context(self, t: float, pos) -> None:
        from PySide6.QtWidgets import QMenu
        if not self._current:
            return
        m = QMenu(self)
        a_s = m.addAction(f"여기({fmt_time(t)})를 녹화 시작으로")
        a_e = m.addAction(f"여기({fmt_time(t)})를 녹화 끝으로")
        m.addSeparator()
        a_go = m.addAction("이 위치로 이동")
        a = m.exec(pos)
        if a is a_go:
            self.player.seek_absolute(t)
            return
        if a not in (a_s, a_e):
            return
        if self._rec_dialog is None:
            self.open_record_dialog()
        d = self._rec_dialog
        if d is None:
            return
        if a is a_s:
            end = d.t_end.seconds()
            d.set_range(t, end if end > t else t + 30)
        else:
            start = d.t_start.seconds()
            d.set_range(start if start < t else max(0.0, t - 30), t)

    def _mark_now(self, start: bool) -> None:
        if self._rec_dialog is None:
            self.open_record_dialog()
        if self._rec_dialog is not None:
            (self._rec_dialog.set_start_now if start else self._rec_dialog.set_end_now)()
            self.notify("녹화 구간 시작을 지금 위치로 정했습니다" if start else "녹화 구간 끝을 지금 위치로 정했습니다", 2000)

    def _start_record(self, src: str, is_link: bool, title: str, duration: float, o: dict) -> None:
        if self._rec_running():
            self.notify("이미 녹화가 진행 중입니다", 4000)
            return
        out_dir = self.settings.download_dir
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        a, b, audio = o.get("start"), o.get("end"), bool(o.get("audio"))
        if is_link:
            if not self.ffmpeg:
                self.notify("ffmpeg 를 찾지 못해 링크 영상을 녹화할 수 없습니다", 6000)
                return
            job = make_url_record_job(src, title, out_dir, start=a, end=b, audio=audio, ffmpeg=self.ffmpeg,
                                      js_runtime=self.js_runtime, remove_sponsors=self.a_sponsor.isChecked(),
                                      sponsor_categories=tuple(self.settings.sponsorblock_categories),
                                      pot_cli=self.pot_cli)
        else:
            if not self.ffmpeg:
                self.notify("ffmpeg 를 찾지 못해 녹화할 수 없습니다", 6000)
                return
            job = LocalRecordJob(LocalRecordRequest(src=src, out_dir=out_dir, title=title or Path(src).stem,
                                                    start=a, end=b, audio=audio, accurate=bool(o.get("accurate")),
                                                    duration=duration, ffmpeg=self.ffmpeg))
        job.finished.connect(self._on_record_finished)
        job.failed.connect(lambda m: self.notify(f"녹화 실패: {m}", 9000) if m != "취소됨" else None)
        import time as _t
        self._rec_jobs = [j for j in getattr(self, "_rec_jobs", []) if j.is_running()] + [job]
        self._rec_t0 = _t.time()
        if not hasattr(self, "_rec_timer"):
            self._rec_timer = QTimer(self)
            self._rec_timer.setInterval(1000)
            self._rec_timer.timeout.connect(self._update_rec_status)
        self.downloads.add_job(job)
        self._rec_timer.start()
        self._update_rec_status()
        self.dock_downloads.show()
        kind = "녹음" if audio else "녹화"
        span = "전체" if a is None else f"{fmt_time(a)} ~ {fmt_time(b or 0)}"
        self.notify(f"{kind}을 시작했습니다 ({span}). 진행 상황은 오른쪽 '다운로드' 창에 보입니다", 4500)
        if self._rec_dialog is not None:
            self._rec_dialog.close()

    def _on_record_finished(self, path: str) -> None:
        QTimer.singleShot(200, self._update_rec_status)
        if not path:
            return
        self.library.rescan()
        self.notify(f"저장했습니다: {Path(path).name}", 8000, "폴더 열기", lambda p=path: reveal_in_folder(p))
        self._last_saved = path

    def open_save_folder(self) -> None:
        d = Path(self.settings.download_dir)
        d.mkdir(parents=True, exist_ok=True)
        last = getattr(self, "_last_saved", "")
        if last and Path(last).exists():
            reveal_in_folder(last)
        else:
            self._open_folder(d)

    def show_recordings(self) -> None:
        self.dock_library.show()
        self.library.set_filter("rec")

    # ------------------------------------------------------------------ 업데이트
    def _auto_check_update(self) -> None:
        import time as _time
        last = float(self.settings.q.value("last_update_check", 0) or 0)
        if _time.time() - last < 20 * 3600:
            return
        self.settings.q.setValue("last_update_check", _time.time())
        self.check_update(silent=True)

    def check_update(self, silent: bool = False) -> None:
        self._update_silent = silent
        if not silent:
            self.statusBar().showMessage("업데이트 확인 중…", 3000)
        self.updater.check()

    def _on_update_checked(self, info, err: str) -> None:
        if info is None:
            if not self._update_silent:
                QMessageBox.warning(self, "업데이트 확인", f"새 버전을 확인하지 못했습니다.\n인터넷 연결을 확인해 주세요.\n\n({err})")
            return
        if not is_newer(info.version):
            if not self._update_silent:
                QMessageBox.information(self, "업데이트 확인", f"지금 최신 버전을 쓰고 있습니다 (v{__version__}).")
            return
        self._release = info
        if self._update_silent:
            self.notify(f"새 버전 {info.version} 이 나왔습니다", 15000, "업데이트", self._ask_update)
        else:
            self._ask_update()

    def _ask_update(self) -> None:
        info = self._release
        if info is None:
            return
        notes = info.notes.strip()
        if len(notes) > 900:
            notes = notes[:900] + "…"
        box = QMessageBox(self)
        box.setWindowTitle("업데이트")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(f"<b>새 버전 {info.version}</b> 이 있습니다. (지금 v{__version__})")
        auto = can_self_install() and bool(info.asset_url)
        box.setInformativeText(("지금 받아서 설치하면 앱이 잠깐 닫혔다가 새 버전으로 다시 열립니다."
                                if auto else "다운로드 페이지를 엽니다. 받은 zip 을 풀어 기존 앱과 바꿔 주세요.")
                               + (f"\n\n바뀐 점:\n{notes}" if notes else ""))
        b_go = box.addButton("지금 업데이트" if auto else "다운로드 페이지 열기", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("나중에", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(b_go)
        box.exec()
        if box.clickedButton() is not b_go:
            return
        if not auto:
            QDesktopServices.openUrl(QUrl(info.page or RELEASES_PAGE))
            return
        dlg = QProgressDialog("새 버전 받는 중…", "취소", 0, 100, self)
        dlg.setWindowTitle("업데이트")
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.canceled.connect(self.updater.cancel)
        dlg.show()
        self._update_dialog = dlg
        self.updater.download_and_prepare(info)

    def _on_update_progress(self, pct: int, text: str) -> None:
        if self._update_dialog is not None:
            self._update_dialog.setValue(pct)
            self._update_dialog.setLabelText(text)

    def _on_update_prepared(self, new: str) -> None:
        if self._update_dialog is not None:
            self._update_dialog.close()
            self._update_dialog = None
        if self.downloads.has_running():
            r = QMessageBox.question(self, "업데이트", "받는 중이거나 녹화 중인 작업이 있습니다. 취소하고 업데이트할까요?")
            if r != QMessageBox.StandardButton.Yes:
                self.notify("업데이트 준비가 끝났습니다. 다음에 '업데이트 확인'을 누르면 설치합니다", 6000)
                return
        try:
            self.updater.launch_installer(new)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "업데이트", f"설치를 시작하지 못했습니다: {e}")
            return
        self.notify("새 버전으로 바꾸는 중입니다. 잠시 후 다시 열립니다", 3000)
        QTimer.singleShot(400, QApplication.instance().quit)

    def _on_update_failed(self, msg: str) -> None:
        if self._update_dialog is not None:
            self._update_dialog.close()
            self._update_dialog = None
        QMessageBox.warning(self, "업데이트", f"업데이트를 받지 못했습니다.\n{msg}")

    # ------------------------------------------------------------------ 오류 신고
    BUG_REPORT_URL = ("https://script.google.com/macros/s/AKfycbxsRD7vRlEP9oKlvlDDPww8cYaDadTTIp1KrqYWSVU5qE5_"
                      "gUh3bNUskBhHDlXH6FskvA/exec")

    def _screen_name(self) -> str:
        """오류 신고에 붙이는 현재 화면 이름. 파일 이름·링크 같은 개인 정보는 넣지 않는다."""
        if self._rec_dialog is not None:
            return "녹화 창"
        if not self._current:
            return "시작 화면"
        kind = "유튜브 재생" if self._current_is_youtube else ("링크 재생" if is_url(self._current) else "파일 재생")
        extra = [x for x, on in (("360도", self.player.vr_enabled), ("전체화면", self._fullscreen),
                                 ("소리만", self._audio_only)) if on]
        return " · ".join([kind] + extra)

    def report_bug(self) -> None:
        q = urllib.parse.urlencode({"app": "class-player", "v": __version__, "where": self._screen_name()})
        QDesktopServices.openUrl(QUrl(f"{self.BUG_REPORT_URL}?{q}"))
        self.statusBar().showMessage("브라우저에서 오류 신고 화면을 열었습니다", 4000)

    def _show_loading(self, text: str) -> None:
        self.loading.setText(text)
        self.loading.adjustSize()
        self.loading.move((self.stage.width() - self.loading.width()) // 2, (self.stage.height() - self.loading.height()) // 2)
        self.loading.show()
        self.loading.raise_()

    def _on_buffering(self, on: bool) -> None:
        if on and self._current:
            self._show_loading("버퍼링 중… 잠시 기다려 주세요")
        elif self.loading.isVisible() and not on:
            self.loading.hide()

    def notify(self, text: str, msec: int = 3500, button: str = "", callback=None) -> None:
        self.toast.show_message(text, msec, button, callback)
        self.statusBar().showMessage(text, msec)

    def _on_file_loaded(self, path: str) -> None:
        self.loading.hide()
        self._refetching = False
        self._update_info()
        QTimer.singleShot(600, self._show_hud_info)
        if self._audio_only:
            self.player.mpv["vid"] = "no"
        self.player.add_pending_subtitles()
        if self._current and not is_url(self._current):
            dur = float(self.player.property("duration", 0.0) or 0.0)
            self.thumbs.load_local(self._current, dur)

    # ------------------------------------------------------------------ 탐색 미리보기
    def _on_preview_hover(self, t: float, x) -> None:
        self._preview_t = t
        slider = self.controls.slider
        if x is None:   # 드래그 중: 현재 핸들 위치
            frac = slider.value() / max(1, slider.maximum())
            x = int(frac * slider.width())
        anchor = slider.mapToGlobal(QPoint(x, 0))
        self.preview.show_at(self.thumbs.thumbnail(t), t, anchor, self.thumbs.has_source())

    def _refresh_preview(self) -> None:
        if self.preview.isVisible():
            self._on_preview_hover(self._preview_t, None if self.controls.slider.isSliderDown() else
                                   self.preview.geometry().center().x() - self.controls.slider.mapToGlobal(QPoint(0, 0)).x())

    # ------------------------------------------------------------------ 클립보드
    def play_from_clipboard(self) -> None:
        text = QApplication.clipboard().text().strip()
        typed = self.url_edit.text().strip()
        if typed and self._is_search_text(typed) and self.url_edit.hasFocus() or \
                (typed and self._is_search_text(typed) and not is_url(text)):
            self.search_youtube(typed)       # 입력칸에 검색어를 쓴 경우
            return
        if is_url(text) or (text and Path(text).exists()):
            self.open_target(text)
        elif typed and typed != self._current and (is_url(typed) or Path(typed).exists()):
            self.open_target(typed)
        else:
            self.statusBar().showMessage("복사해 둔 링크가 없습니다. 유튜브에서 주소를 복사한 뒤 눌러 주세요.", 5000)

    def _on_app_state(self, state) -> None:
        if state == Qt.ApplicationState.ApplicationActive:
            self._check_clipboard()

    def _check_clipboard(self) -> None:
        text = QApplication.clipboard().text().strip()
        if text and text != self._last_clip and is_youtube_url(text) and text != self._current:
            self._last_clip = text
            self.banner.offer(text)
            self.resolver.prefetch(text)   # 누르기 전에 미리 주소를 풀어 둔다

    def _banner_play(self, url: str) -> None:
        self.banner.hide()
        self.open_target(url)

    def _banner_download(self, url: str) -> None:
        self.banner.hide()
        self.url_edit.setText(url)
        self.open_download_dialog()

    def _on_title(self, title: str) -> None:
        self.lbl_title.setText(title)
        self.setWindowTitle(f"{APP_NAME} — {title}" if title else APP_NAME)

    def _on_hwdec(self, hw: str) -> None:
        self._hwdec = hw
        self._update_info()

    def _on_codec(self, codec: str) -> None:
        self._codec = codec
        self._update_info()

    def _update_info(self) -> None:
        w, h = self.player.video_size
        codec = self._codec.split(" ")[0].upper() if self._codec else ""
        hw = self._hwdec
        hw_txt = "소프트웨어 디코딩" if (not hw or hw == "no") else f"하드웨어 디코딩 ({hw})"
        parts = [p for p in (codec, f"{w}×{h}" if w else "", hw_txt if codec else "") if p]
        self.lbl_info.setText(" · ".join(parts))

    def _on_log(self, level: str, component: str, msg: str) -> None:
        if level in ("error", "fatal"):
            self.statusBar().showMessage(f"[{component}] {msg}", 8000)
        print(f"[mpv {level}] {component}: {msg}", file=sys.stderr)

    def _fill_recent(self) -> None:
        self.m_recent.clear()
        for r in self.settings.recent[:15]:
            a = QAction(r if len(r) < 90 else "…" + r[-88:], self)
            a.triggered.connect(lambda _c, t=r: self.open_target(t))
            self.m_recent.addAction(a)

    # ------------------------------------------------------------------ 다운로드
    def open_download_dialog(self) -> None:
        url = self.url_edit.text().strip()
        if not is_url(url):
            url = ""
        dlg = DownloadDialog(url, self.settings, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        req = dlg.request(self.ffmpeg, self.js_runtime)
        req.pot_cli = self.pot_cli
        if not is_url(req.url):
            QMessageBox.warning(self, APP_NAME, "유튜브 링크를 입력하세요.")
            return
        self.library.set_extra_folders([req.out_dir])
        job = DownloadJob(req)
        self.downloads.add_job(job)
        self.dock_downloads.show()

    def _change_download_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "다운로드 폴더", self.settings.download_dir)
        if d:
            self.settings.download_dir = d
            self.library.set_extra_folders([d])

    # ------------------------------------------------------------------ 360도
    def set_vr(self, on: bool, projection: str | None = None, stereo: str | None = None) -> None:
        if on and projection is None and self._current_is_youtube and self.player.vr.projection == "equirect":
            projection = "eac"   # 유튜브 360도 영상은 EAC 큐브맵으로 온다
        self.player.set_vr_enabled(on, projection, stereo)
        on = self.player.vr_enabled
        self.controls.set_vr(on)
        self.a_vr.blockSignals(True)
        self.a_vr.setChecked(on)
        self.a_vr.blockSignals(False)
        for k, a in self.a_proj.items():
            a.setChecked(k == self.player.vr.projection)
        for k, a in self.a_stereo.items():
            a.setChecked(k == self.player.vr.stereo)
        self.lbl_vr.setText(self.player.vr.describe() if on else "")
        self._refresh_hud()
        if on:
            self.notify("360도 모드: 드래그로 둘러보고 휠로 확대합니다 (3 키로 끔)")
        elif self.sender() is not None or not self._current:
            pass

    def _set_projection(self, key: str) -> None:
        self.player.set_vr_enabled(self.player.vr_enabled, projection=key)
        self.lbl_vr.setText(self.player.vr.describe() if self.player.vr_enabled else "")

    def _set_fisheye(self, fov: float | None = None, swap: bool | None = None) -> None:
        if fov is not None:
            self.player.vr.fisheye_fov_deg = fov
        if swap is not None:
            self.player.vr.fisheye_swap = swap
        self.player.update()
        self.lbl_vr.setText(self.player.vr.describe() if self.player.vr_enabled else "")

    def _set_stereo(self, key: str) -> None:
        self.player.set_vr_enabled(self.player.vr_enabled, stereo=key)
        self.lbl_vr.setText(self.player.vr.describe() if self.player.vr_enabled else "")

    def _on_vr_changed(self, vr) -> None:
        if self.player.vr_enabled:
            self.lbl_vr.setText(vr.describe())
        self._refresh_hud()

    def _toggle_hq(self, on: bool) -> None:
        self.settings.vr_hq = on
        self.player.set_vr_options(hq=on)

    def _set_max_mag(self, v: float) -> None:
        self.settings.vr_max_magnification = v
        self.player.set_vr_options(max_magnification=v)

    def _toggle_sponsor(self, on: bool) -> None:
        self.skipper.enabled = on
        self.notify("협찬 구간 자동 건너뛰기 " + ("켬" if on else "끔"))

    def _set_stream_height(self, h: int) -> None:
        self.settings.stream_max_height = h
        self.player.mpv["ytdl-format"] = f"bestvideo[height<={h}]+bestaudio/best[height<={h}]/best"
        self.resolver.max_height = h

    # ------------------------------------------------------------------ 기타 동작
    def _set_volume(self, v: int) -> None:
        self.player.set_volume(v)
        self.settings.volume = v

    def _nudge_volume(self, delta: int) -> None:
        cur = float(self.player.property("volume", 100.0))
        self._set_volume(int(max(0, min(150, cur + delta))))

    def take_screenshot(self) -> None:
        if not self._current or self.player.video_size[0] == 0:
            self.notify("재생 중인 영상이 없어 캡처할 수 없습니다")
            return
        d = Path(self.settings.capture_dir)
        d.mkdir(parents=True, exist_ok=True)
        name = d / f"캡처_{_dt.datetime.now():%Y%m%d_%H%M%S}.png"
        if self.player.screenshot(str(name)):
            self.notify(f"캡처를 저장했습니다: {name.name}", 5000, "폴더 열기", lambda: self._open_folder(d))
        else:
            self.notify("캡처에 실패했습니다", 4000)

    def _open_folder(self, d: Path) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(d)))

    def _change_capture_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "캡처 저장 폴더", self.settings.capture_dir)
        if d:
            self.settings.capture_dir = d
            self.notify(f"캡처 저장 폴더: {d}")

    def cycle_subtitles(self) -> None:
        tracks = [t for t in (self.player.property("track-list", []) or []) if t.get("type") == "sub"]
        if not tracks:
            self.notify("이 영상에는 자막이 없습니다")
            return
        self.player.cycle_subtitles()
        cur = self.player.property("sid", False)
        if cur in (False, None, "no"):
            self.notify("자막 끔")
        else:
            t = next((x for x in tracks if x.get("id") == cur), {})
            self.notify(f"자막: {t.get('title') or t.get('lang') or cur} ({tracks.index(t) + 1 if t in tracks else '?'}/{len(tracks)})")

    def toggle_audio_only(self, on: bool) -> None:
        self._audio_only = on
        self.controls.set_audio_only(on)
        try:
            self.player.mpv["vid"] = "no" if on else "auto"
        except Exception:  # noqa: BLE001
            pass
        self.notify("소리만 재생합니다 (영상 끔). 창을 최소화해도 계속 들립니다" if on else "영상을 다시 켰습니다")

    def _fill_audio_devices(self) -> None:
        self.m_audio.clear()
        devices = self.player.property("audio-device-list", []) or []
        cur = self.settings.audio_device
        g = QActionGroup(self)
        for d in devices:
            name = str(d.get("name", ""))
            desc = str(d.get("description", name))
            a = QAction(("자동 (시스템 기본)" if name == "auto" else desc), self)
            a.setCheckable(True)
            a.setChecked(name == cur)
            a.triggered.connect(lambda _c, n=name, ds=desc: self._set_audio_device(n, ds))
            g.addAction(a)
            self.m_audio.addAction(a)
        if not devices:
            self.m_audio.addAction("장치 목록을 가져올 수 없습니다").setEnabled(False)

    def _set_audio_device(self, name: str, desc: str) -> None:
        self.settings.audio_device = name
        try:
            self.player.mpv["audio-device"] = name
        except Exception as e:  # noqa: BLE001
            self.notify(f"소리 출력 장치를 바꾸지 못했습니다: {e}", 6000)
            return
        self.notify(f"소리 출력: {desc}")

    def register_default_player(self) -> None:
        if sys.platform == "win32":
            from classplayer.platform_win import register_default_player
            try:
                msg = register_default_player()
            except Exception as e:  # noqa: BLE001
                msg = f"등록에 실패했습니다: {e}"
            QMessageBox.information(self, "기본 플레이어로 등록", msg)
        else:
            QMessageBox.information(self, "기본 플레이어로 등록",
                                    "맥에서는 Finder에서 영상 파일을 선택하고 ⌘I(정보 가져오기) > '다음으로 열기'에서 "
                                    "ClassPlayer를 고른 뒤 '모두 변경…'을 누르세요. 그 확장자의 모든 파일이 ClassPlayer로 열립니다.")

    def _focus_search(self) -> None:
        self.dock_library.show()
        self.library.focus_search()

    def _show_env(self) -> None:
        hw = self._hwdec or "(재생 중이 아님)"
        txt = (f"yt-dlp: {self.ytdlp or '없음'}\nffmpeg: {self.ffmpeg or '없음'}\nffprobe: {self.ffprobe or '없음'}\n"
               f"JS 런타임: {self.js_runtime or '없음 (유튜브 일부 화질이 빠질 수 있음)'}\n"
               f"libmpv: {self.player.property('mpv-version', '')}\n하드웨어 디코딩: {hw}")
        QMessageBox.information(self, "환경 정보", f"<pre>{txt}</pre>")

    # ------------------------------------------------------------------ 전체화면
    def toggle_fullscreen(self) -> None:
        if not self._fullscreen:
            self._dock_state = {"lib": self.dock_library.isVisible(), "dl": self.dock_downloads.isVisible()}
            self.dock_library.hide()
            self.dock_downloads.hide()
            self.url_bar.hide()
            self.banner.hide()
            self.menuBar().hide()
            self.statusBar().hide()
            self._set_controls_overlay(True)
            self.showFullScreen()
            self._fullscreen = True
            self.controls.set_fullscreen(True)
            self._hide_timer.start()
        else:
            self._hide_timer.stop()
            self._set_controls_overlay(False)
            self.showNormal()
            self.controls.set_fullscreen(False)
            self.menuBar().show()
            self.statusBar().show()
            self.url_bar.show()
            self.controls.show()
            self.dock_library.setVisible(self._dock_state.get("lib", True))
            self.dock_downloads.setVisible(self._dock_state.get("dl", False))
            self._fullscreen = False
            self.player.setCursor(Qt.CursorShape.OpenHandCursor if self.player.vr_enabled else Qt.CursorShape.ArrowCursor)

    def _set_controls_overlay(self, on: bool) -> None:
        """전체화면용 컨트롤바 모양 전환.

        맥에서 OpenGL 영상 위젯 위에 자식 위젯을 겹쳐 올리면 글자·색이 흐릿하게 합성되는 문제가 있어
        (배속 글자가 안 보임), 전체화면에서도 영상 아래 어두운 띠로 두고 자동 숨김 때만 사라지게 한다.
        CLASSPLAYER_FS_OVERLAY=1 이면 예전처럼 영상 위에 겹친다.
        """
        c = self.controls
        c.setProperty("overlay", on)
        if os.environ.get("CLASSPLAYER_FS_OVERLAY") == "1":
            if on:
                self._central_layout.removeWidget(c)
                c.setParent(self.stage)
            else:
                c.setParent(self.centralWidget())
                self._central_layout.addWidget(c)
        c.style().unpolish(c)
        c.style().polish(c)
        c.show()
        if on:
            c.raise_()
            self._place_overlay()

    def _place_overlay(self) -> None:
        if self.controls.parent() is self.stage:
            w = min(self.stage.width() - 40, 1100)
            h = self.controls.sizeHint().height()
            self.controls.setGeometry((self.stage.width() - w) // 2, self.stage.height() - h - 24, w, h)

    def _auto_hide_controls(self) -> None:
        if self._fullscreen and not self.controls.slider.isSliderDown() and not self.controls.underMouse() \
                and QApplication.activePopupWidget() is None:
            self.controls.hide()
            self.preview.hide()
            self.player.setCursor(Qt.CursorShape.BlankCursor)

    def _on_mouse_moved(self) -> None:
        if self._fullscreen:
            if not self.controls.isVisible():
                self.controls.show()
            self.player.setCursor(Qt.CursorShape.OpenHandCursor if self.player.vr_enabled else Qt.CursorShape.ArrowCursor)
            self._hide_timer.start()

    # ------------------------------------------------------------------ 단축키 (전역, 입력창 제외)
    def eventFilter(self, obj: QObject, e: QEvent) -> bool:
        if obj is getattr(self, "stage", None) and e.type() in (QEvent.Type.Resize, QEvent.Type.Move):
            self._place_overlay()
            if self.loading.isVisible():
                self.loading.move((self.stage.width() - self.loading.width()) // 2,
                                  (self.stage.height() - self.loading.height()) // 2)
            if self.toast.isVisible():
                self.toast.move((self.stage.width() - self.toast.width()) // 2, 24)
            self._refresh_hud()
            return False
        if e.type() == QEvent.Type.KeyPress and QApplication.activeWindow() is self:
            fw = QApplication.focusWidget()
            if isinstance(fw, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)) or \
                    (isinstance(fw, QComboBox) and fw.isEditable()):
                if e.key() == Qt.Key.Key_Escape and fw is self.url_edit:
                    if self.search_panel.isVisible():
                        self.search_panel.close_panel()
                    self.player.setFocus()
                    return True
                if e.key() == Qt.Key.Key_Down and fw is self.url_edit and self.search_panel.isVisible():
                    self.search_panel.list.setFocus()
                    return True
                return False
            if self._handle_key(e):
                return True
        return super().eventFilter(obj, e)

    def _handle_key(self, e) -> bool:
        k, mods = e.key(), e.modifiers()
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        if mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier | Qt.KeyboardModifier.AltModifier):
            return False
        p = self.player
        K = Qt.Key
        if k in (K.Key_Space, K.Key_K):
            p.toggle_pause()
        elif k == K.Key_Left:
            p.seek_relative(-60 if shift else -10)
        elif k == K.Key_Right:
            p.seek_relative(60 if shift else 10)
        elif k == K.Key_J:
            p.seek_relative(-10)
        elif k == K.Key_L:
            p.seek_relative(10)
        elif k == K.Key_Up:
            self._nudge_volume(5)
        elif k == K.Key_Down:
            self._nudge_volume(-5)
        elif k == K.Key_M:
            p.toggle_mute()
        elif k == K.Key_F:
            self.toggle_fullscreen()
        elif k == K.Key_Escape:
            if self._fullscreen:
                self.toggle_fullscreen()
            else:
                return False
        elif k == K.Key_3 or k == K.Key_V:
            self.set_vr(not p.vr_enabled)
        elif k == K.Key_R:
            p.vr_reset()
        elif k == K.Key_N:
            self.playlist_next()
        elif k == K.Key_P:
            self.playlist_prev()
        elif k == K.Key_I:
            self._mark_now(True)
        elif k == K.Key_O:
            self._mark_now(False)
        elif k == K.Key_S:
            p.cycle_subtitles()
        elif k == K.Key_A:
            p.cycle_audio()
        elif k == K.Key_BracketLeft:
            self._change_speed(-0.25)
        elif k == K.Key_BracketRight:
            self._change_speed(0.25)
        elif k == K.Key_Comma:
            p.frame_step(back=True)
        elif k == K.Key_Period:
            p.frame_step()
        elif k == K.Key_Plus or k == K.Key_Equal:
            if p.vr_enabled:
                p.vr_zoom(0.85)
        elif k == K.Key_Minus:
            if p.vr_enabled:
                p.vr_zoom(1.0 / 0.85)
        else:
            return False
        return True

    def _change_speed(self, delta: float) -> None:
        cur = float(self.player.property("speed", 1.0))
        new = max(0.25, min(4.0, round((cur + delta) * 4) / 4))
        self.player.set_speed(new)
        self.controls.set_speed(new)
        self.notify(f"재생 속도 {new:g}×", 1500)

    # ------------------------------------------------------------------ 드래그 앤 드롭 / 종료
    def dragEnterEvent(self, e: QDragEnterEvent) -> None:
        if e.mimeData().hasUrls() or e.mimeData().hasText():
            e.acceptProposedAction()

    def dropEvent(self, e: QDropEvent) -> None:
        md = e.mimeData()
        if md.hasUrls():
            for u in md.urls():
                target = u.toLocalFile() if u.isLocalFile() else u.toString()
                if target:
                    self.open_target(target)
                    break
        elif md.hasText():
            self.open_target(md.text())

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        if hasattr(self, "search_panel"):
            self._place_search_panel()

    def closeEvent(self, e: QCloseEvent) -> None:
        self.downloads.cancel_all()
        self.settings.library_folders = self.library.folders
        self.player.shutdown()
        super().closeEvent(e)


def main() -> None:
    bootstrap.setup_library_paths()
    fmt = QSurfaceFormat()
    fmt.setVersion(3, 3)
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    fmt.setSwapInterval(1)
    QSurfaceFormat.setDefaultFormat(fmt)
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("ClassPlayer")
    app.setStyle("Fusion")   # 맥/윈도우에서 같은 모양으로 그리기
    apply_palette(app)
    app.setStyleSheet(QSS)
    icon_path = (bootstrap.bundled_dir() or Path(__file__).resolve().parent) / "assets" / "icon.png"
    if icon_path.exists():
        from PySide6.QtGui import QIcon
        app.setWindowIcon(QIcon(str(icon_path)))
    win = MainWindow()
    win.show()
    args = [a for a in sys.argv[1:] if not a.startswith("-psn_")]   # macOS 가 붙이는 프로세스 인자 제외
    if args:
        win.open_target(args[0])
    selftest = os.environ.get("CLASSPLAYER_SELFTEST")
    if selftest:
        _run_selftest(app, win, selftest)
    sys.exit(app.exec())


def _run_selftest(app: QApplication, win: "MainWindow", target: str) -> None:
    """패키징 검증용: 파일을 열고 6초 뒤 상태를 JSON 으로 출력하고 종료한다."""
    import json

    win.open_target(target)

    def report() -> None:
        p = win.player
        info = {k: p.property(k) for k in ("hwdec-current", "video-codec", "video-params/w", "video-params/h",
                                              "time-pos", "frame-drop-count", "mpv-version")}
        info["ffmpeg"] = win.ffmpeg
        info["ffprobe"] = win.ffprobe
        info["js_runtime"] = win.js_runtime
        info["bundled"] = bool(bootstrap.bundled_dir())
        info["libmpv"] = bootstrap.loaded_libmpv_path()
        info["pot_cli"] = win.pot_cli
        info["vr"] = {"on": p.vr_enabled, "projection": p.vr.projection, "dual_stream": p.dual_stream,
                      "camera": win._stitch_cam.label if win._stitch_cam else None,
                      "fov": round((p.vr.stitch.fov_front + p.vr.stitch.fov_back) / 2, 2)}
        import urllib.request
        try:
            urllib.request.urlopen("https://api.github.com/", timeout=10)
            info["https_plain"] = "ok"
        except Exception as e:  # noqa: BLE001
            info["https_plain"] = f"error: {str(e)[:80]}"
        info["ssl"] = type(bootstrap.ssl_context()).__module__
        try:
            from classplayer.updater import fetch_latest
            info["https_app"] = "ok " + fetch_latest().version
        except Exception as e:  # noqa: BLE001
            info["https_app"] = f"error: {str(e)[:80]}"
        try:
            import curl_cffi  # noqa: F401
            info["curl_cffi"] = True
        except Exception as e:  # noqa: BLE001
            info["curl_cffi"] = f"error: {e}"
        try:
            from yt_dlp.downloader.external import FFmpegFD
            info["ytdlp_ffmpeg"] = bool(FFmpegFD.available())
        except Exception as e:  # noqa: BLE001
            info["ytdlp_ffmpeg"] = f"error: {e}"
        print("SELFTEST " + json.dumps(info, ensure_ascii=False), flush=True)
        ok = (info.get("time-pos") or 0) > 2
        win.close()
        app.exit(0 if ok else 1)

    QTimer.singleShot(6000, report)


if __name__ == "__main__":
    main()
