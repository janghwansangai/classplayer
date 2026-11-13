"""하단 컨트롤바: 재생/정지, 탐색 슬라이더, 시간, 볼륨, 속도, 자막, 360도, 전체화면."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QSlider, QStyle, QToolButton, QWidget)

SLIDER_MAX = 100_000


def fmt_time(seconds: float) -> str:
    s = int(max(0, seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


class ClickSlider(QSlider):
    """클릭한 지점으로 바로 이동하고, 마우스를 올리면 위치를 알려주는 슬라이더."""

    clicked_value = Signal(int)
    hovered = Signal(float, int)   # 비율(0..1), 슬라이더 안 x 좌표
    left = Signal()

    def __init__(self, orientation=Qt.Orientation.Horizontal, parent=None) -> None:
        super().__init__(orientation, parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)

    def mouseMoveEvent(self, e) -> None:
        frac = min(1.0, max(0.0, e.position().x() / max(1, self.width())))
        self.hovered.emit(frac, int(e.position().x()))
        super().mouseMoveEvent(e)

    def leaveEvent(self, e) -> None:
        self.left.emit()
        super().leaveEvent(e)

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            frac = e.position().x() / max(1, self.width())
            value = int(self.minimum() + frac * (self.maximum() - self.minimum()))
            self.setValue(value)
            self.clicked_value.emit(value)
            e.accept()
            # 이어서 드래그할 수 있도록 기본 처리도 호출
        super().mousePressEvent(e)


class ControlBar(QWidget):
    play_clicked = Signal()
    seek_requested = Signal(float)     # 초 (확정)
    seek_preview = Signal(float)       # 초 (드래그 중)
    volume_changed = Signal(int)
    mute_clicked = Signal()
    speed_changed = Signal(float)
    subs_clicked = Signal()
    vr_toggled = Signal(bool)
    vr_reset_clicked = Signal()
    fullscreen_clicked = Signal()
    screenshot_clicked = Signal()
    preview_hover = Signal(float, int)   # 초, 슬라이더 안 x
    preview_leave = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._duration = 0.0
        self._paused = True
        st = self.style()

        def tool(text: str, tip: str, icon=None, checkable=False) -> QToolButton:
            b = QToolButton(self)
            if icon is not None:
                b.setIcon(st.standardIcon(icon))
            else:
                b.setText(text)
            b.setToolTip(tip)
            b.setCheckable(checkable)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setAutoRaise(True)
            return b

        self.btn_play = tool("▶", "재생/일시정지 (Space)")
        self.btn_play.setObjectName("play")
        self.btn_play.clicked.connect(self.play_clicked)
        self.btn_back = tool("", "10초 뒤로 (←)", QStyle.StandardPixmap.SP_MediaSeekBackward)
        self.btn_fwd = tool("", "10초 앞으로 (→)", QStyle.StandardPixmap.SP_MediaSeekForward)
        self.btn_back.clicked.connect(lambda: self.seek_requested.emit(-10.0) if False else self._rel(-10))
        self.btn_fwd.clicked.connect(lambda: self._rel(10))

        self.slider = ClickSlider()
        self.slider.setRange(0, SLIDER_MAX)
        self.slider.sliderMoved.connect(self._on_slider_moved)
        self.slider.sliderReleased.connect(self._on_slider_released)
        self.slider.clicked_value.connect(self._on_slider_clicked)
        self.slider.hovered.connect(lambda frac, x: self.preview_hover.emit(frac * self._duration, x))
        self.slider.left.connect(self.preview_leave)

        self.setObjectName("controls")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.lbl_time = QLabel("00:00 / 00:00")
        self.lbl_time.setObjectName("time")
        self.lbl_time.setMinimumWidth(110)
        self.lbl_time.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.btn_mute = tool("", "음소거 (M)", QStyle.StandardPixmap.SP_MediaVolume)
        self.btn_mute.clicked.connect(self.mute_clicked)
        self.vol = ClickSlider()
        self.vol.setRange(0, 150)
        self.vol.setValue(100)
        self.vol.setFixedWidth(110)
        self.vol.setObjectName("volume")
        self.vol.setToolTip("볼륨 (↑/↓, 휠)")
        self.vol.valueChanged.connect(self.volume_changed)

        self.speed = QComboBox()
        self.speed.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for s in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0):
            self.speed.addItem(f"{s:g}×", s)
        self.speed.setCurrentIndex(2)
        self.speed.setToolTip("재생 속도 ([ / ])")
        self.speed.currentIndexChanged.connect(lambda i: self.speed_changed.emit(float(self.speed.itemData(i))))

        self.btn_subs = tool("자막", "자막 전환 (S)")
        self.btn_subs.clicked.connect(self.subs_clicked)
        self.btn_shot = tool("캡처", "현재 화면을 이미지로 저장")
        self.btn_shot.clicked.connect(self.screenshot_clicked)
        self.btn_vr = tool("360°", "360도 모드 (3)", checkable=True)
        self.btn_vr.toggled.connect(self.vr_toggled)
        self.btn_vr_reset = tool("⟲", "시점 초기화 (R)")
        self.btn_vr_reset.clicked.connect(self.vr_reset_clicked)
        self.btn_vr_reset.setVisible(False)
        self.btn_full = tool("", "전체화면 (F, 더블클릭)", QStyle.StandardPixmap.SP_TitleBarMaxButton)
        self.btn_full.clicked.connect(self.fullscreen_clicked)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 8, 14, 8)
        lay.setSpacing(8)
        for w in (self.btn_back, self.btn_play, self.btn_fwd):
            lay.addWidget(w)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.lbl_time)
        lay.addWidget(self.btn_mute)
        lay.addWidget(self.vol)
        lay.addWidget(self.speed)
        lay.addWidget(self.btn_subs)
        lay.addWidget(self.btn_shot)
        lay.addWidget(self.btn_vr)
        lay.addWidget(self.btn_vr_reset)
        lay.addWidget(self.btn_full)

    # ------------------------------------------------------------------
    relative_seek = Signal(float)

    def _rel(self, secs: float) -> None:
        self.relative_seek.emit(secs)

    def _on_slider_moved(self, value: int) -> None:
        t = value / SLIDER_MAX * self._duration
        self.lbl_time.setText(f"{fmt_time(t)} / {fmt_time(self._duration)}")
        self.seek_preview.emit(t)

    def _on_slider_released(self) -> None:
        self.seek_requested.emit(self.slider.value() / SLIDER_MAX * self._duration)

    def _on_slider_clicked(self, value: int) -> None:
        self.seek_requested.emit(value / SLIDER_MAX * self._duration)

    # ------------------------------------------------------------------ 상태 반영
    def set_position(self, t: float) -> None:
        if self.slider.isSliderDown():
            return
        if self._duration > 0:
            self.slider.setValue(int(t / self._duration * SLIDER_MAX))
        self.lbl_time.setText(f"{fmt_time(t)} / {fmt_time(self._duration)}")

    def set_duration(self, d: float) -> None:
        self._duration = max(0.0, d)
        self.slider.setEnabled(self._duration > 0)

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        self.btn_play.setText("▶" if paused else "❚❚")

    def set_volume(self, v: int) -> None:
        self.vol.blockSignals(True)
        self.vol.setValue(int(v))
        self.vol.blockSignals(False)

    def set_muted(self, muted: bool) -> None:
        icon = QStyle.StandardPixmap.SP_MediaVolumeMuted if muted else QStyle.StandardPixmap.SP_MediaVolume
        self.btn_mute.setIcon(self.style().standardIcon(icon))

    def set_vr(self, on: bool) -> None:
        self.btn_vr.blockSignals(True)
        self.btn_vr.setChecked(on)
        self.btn_vr.blockSignals(False)
        self.btn_vr_reset.setVisible(on)

    def set_speed(self, s: float) -> None:
        for i in range(self.speed.count()):
            if abs(float(self.speed.itemData(i)) - s) < 1e-6:
                self.speed.blockSignals(True)
                self.speed.setCurrentIndex(i)
                self.speed.blockSignals(False)
                return
