"""ClassPlayer 메인 창."""
from __future__ import annotations

import datetime as _dt
import os
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QDragEnterEvent, QDropEvent, QKeySequence, QSurfaceFormat
from PySide6.QtWidgets import (QAbstractSpinBox, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QDockWidget, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
                               QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QTextEdit,
                               QToolButton, QVBoxLayout, QWidget)

from classplayer import bootstrap
from classplayer.bootstrap import APP_NAME, VIDEO_EXTENSIONS
from classplayer.library.library import LibraryPanel
from classplayer.player.controls import ControlBar
from classplayer.player.mpv_widget import MpvWidget
from classplayer.settings import Settings
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
        self._probe.result.connect(self._on_probe_result)

        # --- 플레이어
        self.player = MpvWidget(
            ytdl_path=self.ytdlp, js_runtime=self.js_runtime,
            stream_max_height=self.settings.stream_max_height,
            vr_hq=self.settings.vr_hq, vr_max_magnification=self.settings.vr_max_magnification,
        )
        self.player.set_volume(self.settings.volume)
        self.controls = ControlBar()
        self.controls.set_volume(self.settings.volume)

        # --- 링크 입력줄
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("유튜브 링크를 붙여넣거나 파일 경로를 입력하고 Enter")
        self.url_edit.setClearButtonEnabled(True)
        self.url_edit.returnPressed.connect(self.play_from_url_bar)
        btn_play = QPushButton("재생")
        btn_play.clicked.connect(self.play_from_url_bar)
        btn_dl = QPushButton("다운로드…")
        btn_dl.clicked.connect(self.open_download_dialog)
        btn_open = QPushButton("파일 열기…")
        btn_open.clicked.connect(self.open_file_dialog)
        for b in (btn_play, btn_dl, btn_open):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        urlrow = QHBoxLayout()
        urlrow.setContentsMargins(8, 6, 8, 2)
        urlrow.addWidget(self.url_edit, 1)
        urlrow.addWidget(btn_play)
        urlrow.addWidget(btn_dl)
        urlrow.addWidget(btn_open)
        self.url_bar = QWidget()
        self.url_bar.setLayout(urlrow)

        central = QWidget()
        v = QVBoxLayout(central)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self.url_bar)
        v.addWidget(self.player, 1)
        v.addWidget(self.controls)
        self.setCentralWidget(central)

        # --- 도크: 라이브러리, 다운로드
        self.library = LibraryPanel(self.settings.library_folders, [self.settings.download_dir])
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

        c.play_clicked.connect(p.toggle_pause)
        c.relative_seek.connect(p.seek_relative)
        c.seek_requested.connect(lambda t: p.seek_absolute(t, exact=True))
        c.volume_changed.connect(self._set_volume)
        c.mute_clicked.connect(p.toggle_mute)
        c.speed_changed.connect(p.set_speed)
        c.subs_clicked.connect(p.cycle_subtitles)
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

        m_view = mb.addMenu("보기")
        act(m_view, "전체화면", self.toggle_fullscreen, "Ctrl+F")
        self.a_vr = act(m_view, "360도 모드", lambda on: self.set_vr(on), checkable=True)
        act(m_view, "시점 초기화", self.player.vr_reset)
        m_proj = m_view.addMenu("360도 투영 방식")
        g = QActionGroup(self)
        self.a_proj = {}
        for key, label in (("equirect", "등장방형 (일반 360도 파일)"), ("eac", "유튜브 EAC 큐브맵")):
            a = act(m_proj, label, lambda _c, k=key: self._set_projection(k), checkable=True, checked=(key == "equirect"))
            g.addAction(a)
            self.a_proj[key] = a
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
        self.set_vr(False)
        self.settings.add_recent(target)
        if self._current_is_youtube:
            self.lbl_title.setText("유튜브 정보 가져오는 중…")
            self.statusBar().showMessage("유튜브에서 영상 주소를 가져오는 중…")
            self.resolver.resolve(target)
        else:
            self.resolver.cancel()
            self.lbl_title.setText("여는 중…")
            self.player.play(target)
            if not is_url(target):
                self._probe_local(target)
        self.player.setFocus()

    def _on_stream_resolved(self, rs) -> None:
        if rs.url != self._current:
            return
        self.statusBar().clearMessage()
        self.player.play_stream(rs.video_url, rs.audio_url, title=rs.title, user_agent=rs.user_agent,
                                subtitles=rs.subtitles)
        self._on_title(rs.title)
        vid = rs.video_id or youtube_video_id(rs.url)
        if vid and self.a_sponsor.isChecked():
            self.skipper.load(vid)

    def _on_stream_failed(self, url: str, message: str) -> None:
        if url != self._current:
            return
        # 앱 내부 풀기에 실패하면 mpv 내장 훅으로 한 번 더 시도한다
        self.statusBar().showMessage(f"직접 풀기 실패({message}), mpv 내장 방식으로 다시 시도합니다", 8000)
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
        if getattr(info, "is_spherical", False):
            self.set_vr(True, projection=info.projection, stereo=info.stereo)  # type: ignore[union-attr]
            self.statusBar().showMessage("360도 영상으로 감지되어 360도 모드를 켰습니다 (3 키로 끄기)", 6000)

    def _on_file_loaded(self, path: str) -> None:
        self._update_info()
        self.player.add_pending_subtitles()

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

    def _set_projection(self, key: str) -> None:
        self.player.set_vr_enabled(self.player.vr_enabled, projection=key)
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
        d = Path(self.settings.download_dir)
        d.mkdir(parents=True, exist_ok=True)
        name = d / f"캡처_{_dt.datetime.now():%Y%m%d_%H%M%S}.png"
        self.player.screenshot(str(name))
        self.statusBar().showMessage(f"캡처 저장: {name}", 4000)

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
            self.menuBar().hide()
            self.statusBar().hide()
            self.showFullScreen()
            self._fullscreen = True
            self._hide_timer.start()
        else:
            self._hide_timer.stop()
            self.showNormal()
            self.menuBar().show()
            self.statusBar().show()
            self.url_bar.show()
            self.controls.show()
            self.dock_library.setVisible(self._dock_state.get("lib", True))
            self.dock_downloads.setVisible(self._dock_state.get("dl", False))
            self._fullscreen = False
            self.player.setCursor(Qt.CursorShape.OpenHandCursor if self.player.vr_enabled else Qt.CursorShape.ArrowCursor)

    def _auto_hide_controls(self) -> None:
        if self._fullscreen:
            self.controls.hide()
            self.player.setCursor(Qt.CursorShape.BlankCursor)

    def _on_mouse_moved(self) -> None:
        if self._fullscreen:
            if not self.controls.isVisible():
                self.controls.show()
            self.player.setCursor(Qt.CursorShape.OpenHandCursor if self.player.vr_enabled else Qt.CursorShape.ArrowCursor)
            self._hide_timer.start()

    # ------------------------------------------------------------------ 단축키 (전역, 입력창 제외)
    def eventFilter(self, obj: QObject, e: QEvent) -> bool:
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
        self.statusBar().showMessage(f"재생 속도 {new:g}×", 2000)

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
