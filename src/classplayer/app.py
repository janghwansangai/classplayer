"""ClassPlayer 메인 창."""
from __future__ import annotations

import datetime as _dt
import os
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QDragEnterEvent, QDropEvent, QKeySequence, QSurfaceFormat
from PySide6.QtWidgets import (QAbstractSpinBox, QFrame, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QDockWidget, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
                               QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QTextEdit,
                               QToolButton, QVBoxLayout, QWidget)

from classplayer import bootstrap
from classplayer.bootstrap import APP_NAME, VIDEO_EXTENSIONS
from classplayer.library.library import LibraryPanel
from classplayer.player.controls import ControlBar
from classplayer.player.mpv_widget import MpvWidget
from classplayer.player.thumbnails import ThumbnailProvider
from classplayer.player.controls import fmt_time
from classplayer.settings import Settings
from classplayer.ui.theme import QSS
from classplayer.youtube.common import is_url, is_youtube_url, youtube_video_id
from classplayer.youtube.download import PRESETS, DownloadJob, DownloadRequest
from classplayer.youtube.probe import probe_spherical
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


class DownloadItem(QWidget):
    play_requested = Signal(str)

    def __init__(self, job: DownloadJob, parent=None) -> None:
        super().__init__(parent)
        self.job = job
        self.title = QLabel(job.req.url)
        self.title.setWordWrap(True)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.status = QLabel("대기 중")
        self.status.setStyleSheet("color: gray;")
        self.btn = QToolButton()
        self.btn.setText("취소")
        self.btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn.clicked.connect(self._on_btn)
        row = QHBoxLayout()
        row.addWidget(self.bar, 1)
        row.addWidget(self.btn)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.addWidget(self.title)
        lay.addLayout(row)
        lay.addWidget(self.status)
        self.setStyleSheet("DownloadItem { border-bottom: 1px solid palette(mid); }")
        job.title_known.connect(self.title.setText)
        job.progress.connect(self._on_progress)
        job.finished.connect(self._on_finished)
        job.failed.connect(lambda m: self.status.setText("실패: " + m))
        self._path = ""

    def _on_progress(self, frac: float, text: str) -> None:
        self.bar.setValue(int(frac * 1000))
        self.status.setText(text)

    def _on_finished(self, path: str) -> None:
        self._path = path
        if path:
            self.bar.setValue(1000)
            self.status.setText("완료: " + Path(path).name)
            self.btn.setText("재생")
        else:
            self.btn.setText("닫기")

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
        self._jobs: list[DownloadJob] = []
        self.inner = QWidget()
        self.inner_lay = QVBoxLayout(self.inner)
        self.inner_lay.setContentsMargins(0, 0, 0, 0)
        self.inner_lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.inner)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(500)
        self.log.setPlaceholderText("yt-dlp 메시지")
        self.log.setMaximumHeight(120)
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


class _ProbeNotifier(QObject):
    result = Signal(str, object)


class _CopyNotifier(QObject):
    progress = Signal(int)
    finished = Signal(str, str)


class SeekPreview(QFrame):
    """슬라이더 위에 뜨는 썸네일 + 시간 말풍선."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet("QFrame { background: rgba(20,20,20,230); border-radius: 8px; }"
                           "QLabel { color: white; }")
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

    def show_at(self, pm, t: float, anchor: QPoint) -> None:
        self.time.setText(fmt_time(t))
        if pm is not None and not pm.isNull():
            self.img.setPixmap(pm.scaled(self.img.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation))
            self.img.show()
        else:
            self.img.clear()
            self.img.setText("…")
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
        self.setStyleSheet("Toast { background: rgba(17,24,39,0.92); border-radius: 10px; }")
        self.label = QLabel("")
        self.label.setStyleSheet("color: white; font-size: 14px;")
        self.btn = QPushButton("")
        self.btn.setObjectName("toastbtn")
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
        self.setStyleSheet("ClipboardBanner { background: #fff6d6; border-bottom: 1px solid #e6d9a8; }")
        self.label = QLabel("")
        self.label.setStyleSheet("color: #5a4a00;")
        b_play = QPushButton("바로 재생")
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


# ====================================================================== 메인 창
class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = Settings()
        self.ytdlp = bootstrap.find_tool("yt-dlp")
        self.ffmpeg = bootstrap.find_tool("ffmpeg")
        self.ffprobe = bootstrap.find_tool("ffprobe")
        self.js_runtime = next((r for r in ("deno", "node", "bun") if bootstrap.find_tool(r)), None)

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
        logo = QLabel("▶")
        logo.setObjectName("logo")
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.url_edit = QLineEdit()
        self.url_edit.setObjectName("url")
        self.url_edit.setPlaceholderText("유튜브 링크를 붙여넣거나 파일을 끌어다 놓으세요")
        self.url_edit.setClearButtonEnabled(True)
        self.url_edit.returnPressed.connect(self.play_from_url_bar)
        btn_paste = QPushButton("붙여넣고 재생")
        btn_paste.setObjectName("primary")
        btn_paste.setToolTip("복사한 링크를 바로 재생 (⌘V)")
        btn_paste.clicked.connect(self.play_from_clipboard)
        btn_play = QPushButton("재생")
        btn_play.clicked.connect(self.play_from_url_bar)
        btn_dl = QPushButton("다운로드…")
        btn_dl.clicked.connect(self.open_download_dialog)
        btn_open = QPushButton("파일")
        btn_open.clicked.connect(self.open_file_dialog)
        for b in (btn_paste, btn_play, btn_dl, btn_open):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        urlrow = QHBoxLayout()
        urlrow.setContentsMargins(14, 10, 14, 10)
        urlrow.setSpacing(10)
        urlrow.addWidget(logo)
        urlrow.addWidget(self.url_edit, 1)
        urlrow.addWidget(btn_paste)
        urlrow.addWidget(btn_play)
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
        self._audio_only = False
        v.addWidget(self.stage, 1)
        v.addWidget(self.controls)
        self._central_layout = v
        self.setCentralWidget(central)

        # --- 도크: 라이브러리, 다운로드
        self.library = LibraryPanel(self.settings.library_folders, [self.settings.download_dir],
                                    ffmpeg=self.ffmpeg, ffprobe=self.ffprobe)
        self.library.play_requested.connect(self.open_target)
        self.library.folders_changed.connect(lambda f: setattr(self.settings, "library_folders", f))
        self.dock_library = QDockWidget("라이브러리", self)
        self.dock_library.setObjectName("dock_library")
        self.dock_library.setWidget(self.library)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.dock_library)

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
        self.lbl_info.setStyleSheet("color: gray;")
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
        self.resolver = StreamResolver(self.js_runtime, self.settings.stream_max_height)
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
        self.resolver.started.connect(lambda _u: self._show_loading("유튜브에서 영상 정보를 가져오는 중…\n보통 5~15초 걸립니다"))

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

    # ------------------------------------------------------------------ 열기/재생
    def play_from_url_bar(self) -> None:
        text = self.url_edit.text().strip()
        if text:
            self.open_target(text)
        self.player.setFocus()

    def open_file_dialog(self) -> None:
        exts = " ".join(f"*{e}" for e in sorted(VIDEO_EXTENSIONS))
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
        self.statusBar().clearMessage()
        self._show_loading("영상을 불러오는 중…")
        self.player.play_stream(rs.video_url, rs.audio_url, title=rs.title, user_agent=rs.user_agent,
                                subtitles=rs.subtitles)
        self._on_title(rs.title)
        if rs.storyboards:
            self.thumbs.load_storyboard(rs.storyboards)
        vid = rs.video_id or youtube_video_id(rs.url)
        if vid and self.a_sponsor.isChecked():
            self.skipper.load(vid)

    def _on_stream_failed(self, url: str, message: str) -> None:
        if url != self._current:
            return
        # 앱 내부 풀기에 실패하면 mpv 가 직접 연다 (일반 영상 주소, 내장 ytdl 훅)
        if self._current_is_youtube:
            self.notify(f"유튜브 주소를 풀지 못했습니다 ({message}). 다른 방식으로 다시 시도합니다", 8000)
        self._show_loading("영상을 여는 중…")
        self.player.play(url)
        vid = youtube_video_id(url)
        if vid and self.a_sponsor.isChecked():
            self.skipper.load(vid)

    def _probe_local(self, path: str) -> None:
        ffprobe = self.ffprobe

        def work() -> None:
            self._probe.result.emit(path, probe_spherical(ffprobe, path))

        threading.Thread(target=work, daemon=True).start()

    def _on_probe_result(self, path: str, info: object) -> None:
        if path != self._current:
            return
        if getattr(info, "dual_stream", False):
            self._show_loading("두 렌즈 영상을 합치는 중…")
            self.player.set_dual_stream(True)
        if getattr(info, "is_spherical", False):
            self.set_vr(True, projection=info.projection, stereo=info.stereo)  # type: ignore[union-attr]
            self.statusBar().showMessage("360도 영상으로 감지되어 360도 모드를 켰습니다 (3 키로 끄기)", 6000)
        elif path.lower().endswith((".insv", ".insp")) and getattr(info, "video_streams", 1) == 1:
            self.notify("이 insv 파일에는 렌즈 하나의 영상만 들어 있습니다. 짝이 되는 _10_ 파일과 함께 Insta360 Studio로 합쳐 주세요", 9000)

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
        self._update_info()
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
        self.preview.show_at(self.thumbs.thumbnail(t), t, anchor)

    def _refresh_preview(self) -> None:
        if self.preview.isVisible():
            self._on_preview_hover(self._preview_t, None if self.controls.slider.isSliderDown() else
                                   self.preview.geometry().center().x() - self.controls.slider.mapToGlobal(QPoint(0, 0)).x())

    # ------------------------------------------------------------------ 클립보드
    def play_from_clipboard(self) -> None:
        text = QApplication.clipboard().text().strip()
        if is_url(text) or Path(text).exists():
            self.open_target(text)
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
            self._hide_timer.start()
        else:
            self._hide_timer.stop()
            self._set_controls_overlay(False)
            self.showNormal()
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
            return False
        if e.type() == QEvent.Type.KeyPress and QApplication.activeWindow() is self:
            fw = QApplication.focusWidget()
            if isinstance(fw, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)) or \
                    (isinstance(fw, QComboBox) and fw.isEditable()):
                if e.key() == Qt.Key.Key_Escape and fw is self.url_edit:
                    self.player.setFocus()
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
        print("SELFTEST " + json.dumps(info, ensure_ascii=False), flush=True)
        ok = (info.get("time-pos") or 0) > 2
        win.close()
        app.exit(0 if ok else 1)

    QTimer.singleShot(6000, report)


if __name__ == "__main__":
    main()
