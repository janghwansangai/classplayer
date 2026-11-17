"""가장자리 조작판: 다른 프로그램을 쓰는 동안에도 화면 가장자리에서 재생을 조작한다.

평소에는 화면 위(또는 왼쪽·오른쪽) 가장자리에 얇은 앰버 막대만 보인다. 마우스를 그 가장자리 끝으로 밀면
작은 조작판이 나오고, 마우스가 벗어나면 잠시 뒤 다시 숨는다. 마우스 위치는 0.12초마다 직접 읽어서
(다른 프로그램이 앞에 있어도) 운영체제와 상관없이 같은 방식으로 동작한다.

- 맥: Qt::Tool 창은 다른 앱이 앞에 오면 숨는 것이 기본 → WA_MacAlwaysShowToolWindow 로 계속 보이게
- 클릭해도 다른 프로그램의 입력 포커스를 빼앗지 않게 WindowDoesNotAcceptFocus·WA_ShowWithoutActivating
- ClassPlayer 가 전체화면일 때는 숨는다(수업 화면 위에 띄우지 않음)
"""
from __future__ import annotations

import time

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QPainter
from PySide6.QtWidgets import (QBoxLayout, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QMenu, QSlider,
                               QToolButton, QVBoxLayout, QWidget)

from classplayer.bootstrap import keys
from classplayer.player.controls import ClickSlider, fmt_time
from classplayer.ui import icons
from classplayer.ui.theme import AMBER, AMBER_HOVER, ICON

EDGES = (("top", "화면 위"), ("left", "화면 왼쪽"), ("right", "화면 오른쪽"))
HANDLE_LEN, HANDLE_THICK = 120, 5
TRIGGER_SPAN = 170          # 막대 가운데에서 이만큼 안쪽 가장자리에 마우스가 닿으면 나온다
HIDE_DELAY = 0.7            # 벗어난 뒤 숨기까지 (초)


def _tool_window(w: QWidget) -> None:
    w.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                     | Qt.WindowType.WindowDoesNotAcceptFocus)
    w.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    w.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)


class EdgeHandle(QWidget):
    """가장자리에 늘 떠 있는 얇은 막대. 누르거나 그 가장자리로 마우스를 밀면 조작판이 나온다."""

    def __init__(self, panel: "EdgePanel") -> None:
        super().__init__(None)
        _tool_window(self)
        self.panel = panel
        self.setToolTip("ClassPlayer 조작판 — 마우스를 이 화면 가장자리 끝으로 밀거나 누르면 나옵니다")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QColor(AMBER)
        c.setAlpha(190)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        r = self.rect().adjusted(0, 0, 0, 0)
        p.drawRoundedRect(r, min(r.width(), r.height()) / 2, min(r.width(), r.height()) / 2)

    def mousePressEvent(self, _e) -> None:  # noqa: N802
        self.panel.slide_in()


class EdgePanel(QWidget):
    """조작판 본체. win(MainWindow)의 메서드로 재생을 조작하고, 상태는 set_* 로 받는다."""

    def __init__(self, win, edge: str = "top") -> None:
        super().__init__(None)
        _tool_window(self)
        self.win = win
        self.edge = edge
        self.enabled = True
        self.suspended = False          # 전체화면 등으로 잠시 숨김
        self.shown = False
        self._left_at = 0.0
        self._duration = 0.0
        self._pos = 0.0
        self._seeking = False
        self.handle = EdgeHandle(self)

        self.frame = QFrame(self)
        self.frame.setObjectName("edgepanel")
        self.frame.setStyleSheet(
            "#edgepanel { background: rgba(23,24,28,245); border: 1px solid #3a3b44; border-radius: 14px; }"
            "#edgepanel QLabel { background: transparent; }"
            "#edgepanel QToolButton { border: none; border-radius: 8px; padding: 4px; }"
            "#edgepanel QToolButton:hover { background: #2c2d35; }"
            f"#edgepanel QToolButton#edgeplay {{ background: {AMBER}; border-radius: 17px; }}"
            f"#edgepanel QToolButton#edgeplay:hover {{ background: {AMBER_HOVER}; }}")
        sh = QGraphicsDropShadowEffect(self.frame)
        sh.setBlurRadius(24)
        sh.setOffset(0, 6)
        sh.setColor(QColor(0, 0, 0, 160))
        self.frame.setGraphicsEffect(sh)

        def tb(name: str, tip: str, slot, size: int = 18) -> QToolButton:
            b = QToolButton(self.frame)
            b.setIcon(icons.icon(name, ICON))
            b.setIconSize(QSize(size, size))
            b.setToolTip(keys(tip))
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(slot)
            return b

        self.title = QLabel("열린 영상 없음", self.frame)
        self.title.setStyleSheet("color:#e7e8ec; font-weight:700; font-size:12.5px;")
        self.time = QLabel("", self.frame)
        self.time.setStyleSheet("color:#8b8e99; font-size:11.5px;")
        self.seek = ClickSlider(Qt.Orientation.Horizontal, self.frame)
        self.seek.setRange(0, 1000)
        self.seek.setToolTip("누르거나 끌어서 원하는 곳으로 이동")
        self.seek.sliderPressed.connect(lambda: setattr(self, "_seeking", True))
        self.seek.sliderReleased.connect(self._seek_release)
        self.seek.clicked_value.connect(self._seek_to)

        self.b_prev = tb("back10", "재생목록 이전 영상 (P)", lambda: win.playlist_prev())
        self.b_prev.setIcon(icons.icon("play", ICON))
        self.b_prev.setText("⏮")
        self.b_back = tb("back10", "10초 뒤로 (←)", lambda: win.player.seek_relative(-10))
        self.b_play = tb("play", "재생 / 일시정지 (Space)", lambda: win.player.toggle_pause(), 20)
        self.b_play.setObjectName("edgeplay")
        self.b_play.setFixedSize(36, 36)
        self.b_fwd = tb("fwd10", "10초 앞으로 (→)", lambda: win.player.seek_relative(10))
        self.b_next = tb("fwd10", "재생목록 다음 영상 (N)", lambda: win.playlist_next())
        for b, txt in ((self.b_prev, "⏮"), (self.b_next, "⏭")):
            b.setIcon(icons.icon("play", ICON))
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            b.setText(txt)
            b.setStyleSheet("font-size:15px; color:#d9dbe3;")
        self.b_mute = tb("volume", "소리 끄기 / 켜기 (M)", lambda: win.player.toggle_mute())
        self.vol = QSlider(Qt.Orientation.Horizontal, self.frame)
        self.vol.setRange(0, 150)
        self.vol.setToolTip("소리 크기 (↑ / ↓)")
        self.vol.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.vol.valueChanged.connect(self._on_vol)
        self.b_repeat = tb("repeat", "반복 바꾸기: 안 함 → 한 곡 → 목록 (⌘T)", lambda: win.cycle_repeat())
        self.b_audio = tb("audio", "소리만 재생 켜기 / 끄기 (⌘B)", lambda: win._on_audio_button(not win._audio_only))
        self.b_open = tb("window", "ClassPlayer 창을 앞으로 가져오기", self._bring_app)
        self.b_more = tb("plus", "조작판 위치 바꾸기·숨기기", self._menu)
        self.b_more.setIcon(icons.icon("minus", ICON))
        self.b_more.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.b_more.setText("⋯")
        self.b_more.setStyleSheet("font-size:16px; color:#d9dbe3;")

        self._build_layout()
        self._timer = QTimer(self)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._poll)
        self._anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.finished.connect(self._anim_done)
        self._slide = QPropertyAnimation(self, b"pos", self)
        self._slide.setDuration(160)
        self._slide.setEasingCurve(QEasingCurve.Type.OutCubic)

    # ------------------------------------------------------------------ 모양
    def _build_layout(self) -> None:
        old = self.frame.layout()
        if old is not None:
            while old.count():
                old.takeAt(0)
            QWidget().setLayout(old)          # 옛 배치 버리기
        vertical = self.edge in ("left", "right")
        btns = [self.b_prev, self.b_back, self.b_play, self.b_fwd, self.b_next, self.b_mute, self.vol,
                self.b_repeat, self.b_audio, self.b_open, self.b_more]
        if vertical:
            lay = QVBoxLayout(self.frame)
            lay.setContentsMargins(8, 12, 8, 12)
            lay.setSpacing(6)
            self.vol.setOrientation(Qt.Orientation.Vertical)
            self.vol.setFixedSize(22, 90)
            self.title.hide()
            self.seek.hide()
            self.time.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(self.time, 0, Qt.AlignmentFlag.AlignHCenter)
            for w in btns:
                lay.addWidget(w, 0, Qt.AlignmentFlag.AlignHCenter)
            self.frame.setFixedSize(72, 560)
        else:
            outer = QVBoxLayout(self.frame)
            outer.setContentsMargins(14, 8, 14, 8)
            outer.setSpacing(4)
            top = QHBoxLayout()
            top.setSpacing(8)
            top.addWidget(self.title, 1)
            top.addWidget(self.time)
            row = QBoxLayout(QBoxLayout.Direction.LeftToRight)
            row.setSpacing(4)
            self.vol.setOrientation(Qt.Orientation.Horizontal)
            self.vol.setFixedSize(84, 22)
            for w in btns:
                row.addWidget(w)
            self.title.show()
            self.seek.show()
            outer.addLayout(top)
            outer.addWidget(self.seek)
            outer.addLayout(row)
            self.frame.setFixedSize(520, 108)
        m = 14                                  # 그림자 자리
        self.frame.move(m, m)
        self.setFixedSize(self.frame.width() + 2 * m, self.frame.height() + 2 * m)

    def _screen_geo(self) -> tuple[QRect, QRect]:
        scr = None
        try:
            scr = self.win.screen()
        except Exception:  # noqa: BLE001
            scr = None
        scr = scr or QGuiApplication.primaryScreen()
        return scr.geometry(), scr.availableGeometry()

    def _positions(self) -> tuple[QPoint, QPoint, QRect]:
        """(보일 때 위치, 숨을 때 시작 위치, 막대 사각형)"""
        _sg, ag = self._screen_geo()
        w, h = self.width(), self.height()
        if self.edge == "left":
            y = ag.center().y() - h // 2
            return QPoint(ag.left() - 6, y), QPoint(ag.left() - 30, y), \
                QRect(ag.left(), ag.center().y() - HANDLE_LEN // 2, HANDLE_THICK, HANDLE_LEN)
        if self.edge == "right":
            y = ag.center().y() - h // 2
            return QPoint(ag.right() - w + 7, y), QPoint(ag.right() - w + 31, y), \
                QRect(ag.right() - HANDLE_THICK + 1, ag.center().y() - HANDLE_LEN // 2, HANDLE_THICK, HANDLE_LEN)
        x = ag.center().x() - w // 2
        return QPoint(x, ag.top() - 6), QPoint(x, ag.top() - 30), \
            QRect(ag.center().x() - HANDLE_LEN // 2, ag.top(), HANDLE_LEN, HANDLE_THICK)

    def place(self) -> None:
        shown, _hidden, hr = self._positions()
        self.handle.setGeometry(hr)
        if self.shown:
            self.move(shown)

    # ------------------------------------------------------------------ 켜기·숨기기
    def start(self) -> None:
        self.place()
        self._update_visibility()
        self._timer.start()

    def set_enabled(self, on: bool) -> None:
        self.enabled = on
        self._update_visibility()

    def set_suspended(self, on: bool) -> None:
        self.suspended = on
        self._update_visibility()

    def set_edge(self, edge: str) -> None:
        if edge == self.edge:
            return
        was = self.shown
        self.shown = False
        self.hide()
        self.edge = edge
        self._build_layout()
        self.place()
        self._update_visibility()
        if was:
            self.slide_in()

    def _update_visibility(self) -> None:
        active = self.enabled and not self.suspended
        if not active:
            self.handle.hide()
            self.shown = False
            self.hide()
        else:
            self.place()
            if not self.shown:
                self.handle.show()
                self.handle.raise_()

    def slide_in(self) -> None:
        if not (self.enabled and not self.suspended) or self.shown:
            return
        self.shown = True
        self._left_at = 0.0
        self.set_position(self._pos)            # 보이자마자 지금 시간·위치로
        shown, hidden, _hr = self._positions()
        self.handle.hide()
        self.move(hidden)
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self._slide.stop()
        self._slide.setStartValue(hidden)
        self._slide.setEndValue(shown)
        self._slide.start()
        self._anim.stop()
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()

    def slide_out(self) -> None:
        if not self.shown:
            return
        self.shown = False
        shown, hidden, _hr = self._positions()
        self._slide.stop()
        self._slide.setStartValue(self.pos())
        self._slide.setEndValue(hidden)
        self._slide.start()
        self._anim.stop()
        self._anim.setStartValue(self.windowOpacity())
        self._anim.setEndValue(0.0)
        self._anim.start()

    def _anim_done(self) -> None:
        if not self.shown:
            self.hide()
            if self.enabled and not self.suspended:
                self.handle.show()
                self.handle.raise_()

    def check_cursor(self, pos: QPoint, now: float | None = None) -> None:
        """마우스 위치로 나오기·숨기 결정 (시험에서 위치를 넣어 부를 수 있게 분리)."""
        if not self.enabled or self.suspended:
            return
        now = time.monotonic() if now is None else now
        if not self.shown:
            sg, _ag = self._screen_geo()
            _s, _h, hr = self._positions()
            if self.edge == "top":
                hit = pos.y() <= sg.top() + 1 and abs(pos.x() - hr.center().x()) <= TRIGGER_SPAN
            elif self.edge == "left":
                hit = pos.x() <= sg.left() + 1 and abs(pos.y() - hr.center().y()) <= TRIGGER_SPAN
            else:
                hit = pos.x() >= sg.right() - 1 and abs(pos.y() - hr.center().y()) <= TRIGGER_SPAN
            if hit or hr.adjusted(-4, -4, 4, 4).contains(pos):
                self.slide_in()
            return
        inside = self.frameGeometry().adjusted(-24, -24, 24, 24).contains(pos)
        busy = self._seeking or self.vol.isSliderDown() or QGuiApplication.mouseButtons() != Qt.MouseButton.NoButton \
            or self._menu_open
        if inside or busy:
            self._left_at = 0.0
        elif not self._left_at:
            self._left_at = now
        elif now - self._left_at >= HIDE_DELAY:
            self.slide_out()

    _menu_open = False

    def _poll(self) -> None:
        self.check_cursor(QCursor.pos())

    # ------------------------------------------------------------------ 조작
    def _bring_app(self) -> None:
        w = self.win
        if w.isMinimized():
            w.showNormal()
        w.show()
        w.raise_()
        w.activateWindow()

    def _menu(self) -> None:
        m = QMenu(self)
        acts = {}
        for key, label in EDGES:
            a = m.addAction(("✓ " if key == self.edge else "   ") + f"{label}에 두기")
            acts[a] = key
        m.addSeparator()
        a_off = m.addAction("조작판 끄기 (보기 메뉴에서 다시 켜기)")
        self._menu_open = True
        try:
            a = m.exec(QCursor.pos())
        finally:
            self._menu_open = False
        if a in acts:
            self.win.set_edge_panel(edge=acts[a])
        elif a is a_off:
            self.win.set_edge_panel(enabled=False)

    def _on_vol(self, v: int) -> None:
        if not self.vol.signalsBlocked():
            self.win._set_volume(v)

    def _seek_to(self, v: int) -> None:
        if self._duration > 0:
            self.win.player.seek_absolute(self._duration * v / 1000.0, exact=False)

    def _seek_release(self) -> None:
        self._seeking = False
        self._seek_to(self.seek.value())

    # ------------------------------------------------------------------ 상태 받기
    def set_title(self, t: str) -> None:
        t = t or "열린 영상 없음"
        self.title.setText(self.title.fontMetrics().elidedText(t, Qt.TextElideMode.ElideRight, 300))
        self.title.setToolTip(t)
        self.b_play.setToolTip(keys(f"재생 / 일시정지 (Space)\n{t}"))

    def set_paused(self, paused: bool) -> None:
        self.b_play.setIcon(icons.icon("play" if paused else "pause", "#1b1206"))

    def set_position(self, pos: float) -> None:
        self._pos = pos
        if not self.shown:
            return
        d = self._duration
        sep = "\n" if self.edge in ("left", "right") else " / "      # 세로 조작판은 두 줄로
        self.time.setText(f"{fmt_time(pos)}{sep}{fmt_time(d)}" if d > 0 else fmt_time(pos))
        if d > 0 and not self._seeking and not self.seek.isSliderDown():
            self.seek.blockSignals(True)
            self.seek.setValue(int(pos / d * 1000))
            self.seek.blockSignals(False)

    def set_duration(self, d: float) -> None:
        self._duration = max(0.0, d)
        self.seek.setEnabled(self._duration > 0)

    def set_volume(self, v: int) -> None:
        self.vol.blockSignals(True)
        self.vol.setValue(int(v))
        self.vol.blockSignals(False)

    def set_muted(self, muted: bool) -> None:
        self.b_mute.setIcon(icons.icon("mute" if muted else "volume", ICON))

    def set_repeat(self, mode: str) -> None:
        on = mode in ("one", "list")
        self.b_repeat.setIcon(icons.icon("repeat1" if mode == "one" else "repeat", AMBER_HOVER if on else ICON))

    def set_audio_only(self, on: bool) -> None:
        self.b_audio.setIcon(icons.icon("audio", AMBER_HOVER if on else ICON))

    def set_playlist_active(self, on: bool) -> None:
        self.b_prev.setEnabled(on)
        self.b_next.setEnabled(on)

    def close_all(self) -> None:
        self._timer.stop()
        self.handle.close()
        self.close()
