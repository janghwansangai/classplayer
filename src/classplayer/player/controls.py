"""하단 컨트롤바: 재생/정지, 탐색 슬라이더, 시간, 볼륨, 속도, 자막, 캡처, 녹화, 360도, 소리만, 전체화면."""
from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QSlider, QStyle, QStyleOptionSlider, QToolButton,
                               QVBoxLayout,
                               QWidget)

from classplayer.ui import icons
from classplayer.ui.theme import AMBER_HOVER, ICON

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
    marks_edited = Signal(float, float)      # 녹화 구간을 탐색바에서 직접 바꿈 (비율)
    context_at = Signal(float, object)       # 오른쪽 클릭 위치(비율), 화면 좌표

    def __init__(self, orientation=Qt.Orientation.Horizontal, parent=None) -> None:
        super().__init__(orientation, parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        self._marks: tuple[float, float] | None = None   # 녹화 구간 (0..1 비율)
        self._mark_drag: str | None = None                # "a" | "b" | "new"
        self._mark_anchor = 0.0
        self.edit_marks = False      # 녹화 구간 편집 허용 (컨트롤바가 켬)

    def set_marks(self, a: float | None, b: float | None) -> None:
        """녹화할 구간을 홈 위에 빨간 띠로 표시한다. 둘 다 None 이면 지운다."""
        self._marks = None if a is None or b is None else (max(0.0, min(a, b)), min(1.0, max(a, b)))
        self.update()

    def _groove(self):
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        return self.style().subControlRect(QStyle.ComplexControl.CC_Slider, opt,
                                           QStyle.SubControl.SC_SliderGroove, self)

    def _frac_at(self, x: float) -> float:
        g = self._groove()
        return min(1.0, max(0.0, (x - g.left()) / max(1, g.width())))

    def _mark_x(self, frac: float) -> float:
        g = self._groove()
        return g.left() + frac * g.width()

    def contextMenuEvent(self, e) -> None:  # noqa: N802
        if self.edit_marks:
            self.context_at.emit(self._frac_at(e.pos().x()), e.globalPos())
            e.accept()
            return
        super().contextMenuEvent(e)

    def paintEvent(self, e) -> None:
        super().paintEvent(e)
        if not self._marks:
            return
        groove = self._groove()
        a, b = self._marks
        x0 = groove.left() + a * groove.width()
        x1 = groove.left() + b * groove.width()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cy = groove.center().y()
        p.fillRect(QRectF(x0, cy - 4, max(2.0, x1 - x0), 8), QColor(239, 68, 68, 150))
        p.setPen(QColor("#ef4444"))
        for x in (x0, x1):
            p.drawLine(int(x), cy - 8, int(x), cy + 8)
        p.end()

    def mouseMoveEvent(self, e) -> None:
        frac = min(1.0, max(0.0, e.position().x() / max(1, self.width())))
        self.hovered.emit(frac, int(e.position().x()))
        if self._mark_drag:
            f = self._frac_at(e.position().x())
            a, b = self._marks or (f, f)
            if self._mark_drag == "a":
                a = f
            elif self._mark_drag == "b":
                b = f
            else:
                a, b = sorted((self._mark_anchor, f))
            self._marks = (min(a, b), max(a, b))
            if self._mark_drag in ("a", "b") and a > b:      # 서로 넘어가면 잡은 쪽을 바꾼다
                self._mark_drag = "b" if self._mark_drag == "a" else "a"
            self.update()
            self.marks_edited.emit(*self._marks)
            return
        if self.edit_marks and self._marks:
            x = e.position().x()
            near = any(abs(x - self._mark_x(m)) <= 6 for m in self._marks)
            self.setCursor(Qt.CursorShape.SizeHorCursor if near else Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if self._mark_drag:
            self._mark_drag = None
            if self._marks:
                self.marks_edited.emit(*self._marks)
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def leaveEvent(self, e) -> None:
        self.left.emit()
        super().leaveEvent(e)

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self.edit_marks:
            x = e.position().x()
            if self._marks:
                da, db = abs(x - self._mark_x(self._marks[0])), abs(x - self._mark_x(self._marks[1]))
                if min(da, db) <= 6:
                    self._mark_drag = "a" if da <= db else "b"
                    e.accept()
                    return
            if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self._mark_drag = "new"
                self._mark_anchor = self._frac_at(x)
                self._marks = (self._mark_anchor, self._mark_anchor)
                self.update()
                e.accept()
                return
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
    record_clicked = Signal()
    repeat_clicked = Signal()
    prev_clicked = Signal()
    source_clicked = Signal()          # 출처 배지 → 그 목록 보여 주기
    next_clicked = Signal()
    audio_only_toggled = Signal(bool)
    range_edited = Signal(float, float)        # 탐색바에서 정한 녹화 구간 (초)
    range_context = Signal(float, object)      # 오른쪽 클릭한 시각(초), 화면 좌표
    preview_hover = Signal(float, int)   # 초, 슬라이더 안 x
    preview_leave = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._duration = 0.0
        self._paused = True
        st = self.style()

        def tool(name: str, tip: str, checkable: bool = False, active: str | None = None) -> QToolButton:
            b = QToolButton(self)
            b.setIcon(icons.icon(name, ICON, active or AMBER_HOVER))
            b.setIconSize(QSize(20, 20))
            b.setToolTip(tip)
            b.setCheckable(checkable)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setAutoRaise(True)
            return b

        self.btn_play = QToolButton(self)
        self.btn_play.setObjectName("play")
        self.btn_play.setToolTip("재생/일시정지 (Space)")
        self.btn_play.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_play.setIconSize(QSize(20, 20))
        self.btn_play.setIcon(icons.icon("play", "#1b1206"))
        self.btn_play.clicked.connect(self.play_clicked)
        # 시안 A: 지금 재생의 출처 배지 (재생목록 = 앰버, 라이브러리 = 파랑). 누르면 그 목록의 재생 중인 줄로
        self.src_badge = QToolButton(self)
        self.src_badge.setObjectName("srcbadge")
        self.src_badge.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.src_badge.setIconSize(QSize(14, 14))
        self.src_badge.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.src_badge.setCursor(Qt.CursorShape.PointingHandCursor)
        self.src_badge.clicked.connect(self.source_clicked)
        self.src_badge.hide()
        self.btn_prev = tool("prev", "")
        self.btn_next = tool("next", "")
        self.btn_prev.clicked.connect(self.prev_clicked)
        self.btn_next.clicked.connect(self.next_clicked)
        self.set_nav("")
        self.btn_back = tool("back10", "10초 뒤로 (←)")
        self.btn_fwd = tool("fwd10", "10초 앞으로 (→)")
        self.btn_back.clicked.connect(lambda: self._rel(-10))
        self.btn_fwd.clicked.connect(lambda: self._rel(10))

        self.slider = ClickSlider()

        self.slider.setRange(0, SLIDER_MAX)
        self.slider.sliderMoved.connect(self._on_slider_moved)
        self.slider.sliderReleased.connect(self._on_slider_released)
        self.slider.clicked_value.connect(self._on_slider_clicked)
        self.slider.hovered.connect(lambda frac, x: self.preview_hover.emit(frac * self._duration, x))
        self.slider.left.connect(self.preview_leave)
        self.slider.edit_marks = True
        self.slider.setToolTip("Shift+드래그: 녹화 구간 정하기 · 오른쪽 클릭: 여기를 녹화 시작/끝으로")
        self.slider.marks_edited.connect(lambda a, b: self.range_edited.emit(a * self._duration, b * self._duration))
        self.slider.context_at.connect(lambda f, pos: self.range_context.emit(f * self._duration, pos))

        self.setObjectName("controls")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.lbl_time = QLabel("00:00 / 00:00")
        self.lbl_time.setObjectName("time")
        self.lbl_time.setMinimumWidth(104)
        self.lbl_time.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_time.setTextFormat(Qt.TextFormat.RichText)

        self.btn_mute = tool("volume", "음소거 (M)")
        self.btn_mute.clicked.connect(self.mute_clicked)
        self.vol = ClickSlider()
        self.vol.setRange(0, 150)
        self.vol.setValue(100)
        self.vol.setFixedWidth(96)
        self.vol.setObjectName("volume")
        self.vol.setToolTip("볼륨 (↑/↓, 휠)")
        self.vol.valueChanged.connect(self.volume_changed)

        self.speed = QComboBox()
        self.speed.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for s in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0):
            self.speed.addItem(f"{s:g}×", s)
        self.speed.setCurrentIndex(2)
        self.speed.setToolTip("재생 속도 ([ / ])")
        self.speed.setFixedWidth(76)
        self.speed.currentIndexChanged.connect(lambda i: self.speed_changed.emit(float(self.speed.itemData(i))))

        self.btn_repeat = tool("repeat", "")
        self.btn_repeat.clicked.connect(self.repeat_clicked)
        self.set_repeat_mode("off")
        self.btn_subs = tool("subs", "자막 전환 (S) · 지금: 꺼짐", checkable=True)
        self.btn_subs.clicked.connect(self.subs_clicked)
        self.btn_shot = tool("camera", "현재 화면을 이미지로 저장")
        self.btn_shot.clicked.connect(self.screenshot_clicked)
        self.btn_rec = tool("record", "녹화·녹음: 전체 또는 구간을 파일로 저장 (Ctrl+R)")
        self.btn_rec.setObjectName("record")
        self.btn_rec.clicked.connect(self.record_clicked)
        self.btn_vr = tool("globe", "360도 모드 (3)", checkable=True)
        self.btn_vr.toggled.connect(self.vr_toggled)
        self.btn_vr_reset = tool("reset", "시점 초기화 (R)")
        self.btn_vr_reset.clicked.connect(self.vr_reset_clicked)
        self.btn_vr_reset.setVisible(False)
        self.btn_audio = tool("audio", "소리만 재생 (Ctrl+B)", checkable=True)
        self.btn_audio.toggled.connect(self.audio_only_toggled)
        self.btn_full = tool("full", "전체화면 (F, 더블클릭)")
        self.btn_full.clicked.connect(self.fullscreen_clicked)

        # 두 줄: 위는 화면 폭 전체의 탐색 막대, 아래는 버튼 (버튼이 늘어도 탐색 막대가 짧아지지 않게, 2026-11-17)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 4, 12, 4)
        outer.setSpacing(0)
        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(self.src_badge)         # 출처 배지는 탐색 막대 왼쪽 (버튼 줄이 꽉 차서)
        top.addWidget(self.slider, 1)
        outer.addLayout(top)
        lay = QHBoxLayout()
        lay.setSpacing(6)
        outer.addLayout(lay)
        for w in (self.btn_prev, self.btn_back, self.btn_play, self.btn_fwd, self.btn_next):
            lay.addWidget(w)
        lay.addSpacing(8)
        lay.addWidget(self.lbl_time)
        lay.addStretch(1)
        lay.addWidget(self.btn_mute)
        lay.addWidget(self.vol)
        lay.addSpacing(6)
        lay.addWidget(self.speed)
        for w in (self.btn_repeat, self.btn_subs, self.btn_shot, self.btn_rec, self.btn_vr, self.btn_vr_reset, self.btn_audio,
                  self.btn_full):
            lay.addWidget(w)

    # ------------------------------------------------------------------
    relative_seek = Signal(float)

    def _rel(self, secs: float) -> None:
        self.relative_seek.emit(secs)

    def _on_slider_moved(self, value: int) -> None:
        t = value / SLIDER_MAX * self._duration
        self._show_time(t)
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
        self._show_time(t)

    def _show_time(self, t: float) -> None:
        self.position = t
        self.lbl_time.setText(f"<b style='color:#ffffff'>{fmt_time(t)}</b> / {fmt_time(self._duration)}")

    position = 0.0

    @property
    def duration(self) -> float:
        return self._duration

    def set_marks(self, a: float | None, b: float | None) -> None:
        """녹화 구간(초)을 탐색바에 표시."""
        if a is None or b is None or self._duration <= 0:
            self.slider.set_marks(None, None)
        else:
            self.slider.set_marks(a / self._duration, b / self._duration)

    def set_subs(self, on: bool) -> None:
        self.btn_subs.blockSignals(True)
        self.btn_subs.setChecked(on)
        self.btn_subs.blockSignals(False)
        self.btn_subs.setToolTip("자막 전환 (S) · 지금: " + ("켜짐" if on else "꺼짐"))

    def set_recording(self, on: bool, text: str = "") -> None:
        """녹화 중이면 녹화 버튼을 빨갛게 눌린 상태로 보여 준다."""
        self.btn_rec.setCheckable(True)
        self.btn_rec.blockSignals(True)
        self.btn_rec.setChecked(on)
        self.btn_rec.blockSignals(False)
        self.btn_rec.setToolTip(text if on else "녹화·녹음: 전체 또는 구간을 파일로 저장 (Ctrl+R)")

    def set_source_badge(self, kind: str, text: str, tip: str) -> None:
        """kind: "playlist" | "library" | "" (숨김)."""
        if not kind:
            self.src_badge.hide()
            return
        color = AMBER_HOVER if kind == "playlist" else "#93c5fd"
        self.src_badge.setIcon(icons.icon("list" if kind == "playlist" else "grid", color))
        self.src_badge.setText(self.src_badge.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, 230))
        self.src_badge.setToolTip(tip)
        self.src_badge.setProperty("kind", kind)
        self.src_badge.style().unpolish(self.src_badge)
        self.src_badge.style().polish(self.src_badge)
        self.src_badge.show()

    def set_nav(self, source: str) -> None:
        """이전·다음 버튼: 재생목록이면 목록 순서, 라이브러리면 보이는 목록 순서, 아니면 꺼짐."""
        on = source in ("playlist", "library")
        where = {"playlist": "재생목록", "library": "라이브러리 목록"}.get(source, "")
        for b, word, key in ((self.btn_prev, "이전", "P"), (self.btn_next, "다음", "N")):
            b.setEnabled(on)
            b.setToolTip(f"{where}의 {word} 영상 ({key})" if on else
                         f"{word} 영상 — 재생목록이나 라이브러리에서 재생할 때 쓸 수 있어요")

    REPEAT_TIPS = {
        "off": "반복 안 함 — 누르면 '한 곡 반복' (⌘T)",
        "one": "한 곡 반복 중: 지금 영상·음악을 끝나면 처음부터 다시 — 누르면 '목록 반복' (⌘T)",
        "list": "목록 반복 중: 재생목록이 끝나면 처음부터 다시 — 누르면 '반복 안 함' (⌘T)",
    }

    def set_repeat_mode(self, mode: str) -> None:
        """반복 버튼 모양: 안 함(회색) / 한 곡(앰버 '1') / 목록(앰버)."""
        on = mode in ("one", "list")
        self.btn_repeat.setIcon(icons.icon("repeat1" if mode == "one" else "repeat", AMBER_HOVER if on else ICON))
        from classplayer.bootstrap import keys
        self.btn_repeat.setToolTip(keys(self.REPEAT_TIPS.get(mode, "")))

    def set_video_tools_enabled(self, on: bool) -> None:
        """음악·소리만 재생일 때 화면 캡처·360도 버튼을 끈다."""
        for b, tip in ((self.btn_shot, "현재 화면을 이미지로 저장"), (self.btn_vr, "360도 모드 (3)")):
            b.setEnabled(on)
            b.setToolTip(tip if on else tip.split(" (")[0] + " — 음악·소리만 재생 중에는 쓸 수 없습니다")
        if not on:
            self.btn_vr_reset.setVisible(False)

    def set_audio_only(self, on: bool) -> None:
        self.btn_audio.blockSignals(True)
        self.btn_audio.setChecked(on)
        self.btn_audio.blockSignals(False)

    def set_fullscreen(self, on: bool) -> None:
        self.btn_full.setIcon(icons.icon("exitfull" if on else "full", ICON))

    def set_duration(self, d: float) -> None:
        self._duration = max(0.0, d)
        self.slider.setEnabled(self._duration > 0)

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        self.btn_play.setIcon(icons.icon("play" if paused else "pause", "#1b1206"))

    def set_volume(self, v: int) -> None:
        self.vol.blockSignals(True)
        self.vol.setValue(int(v))
        self.vol.blockSignals(False)

    def set_muted(self, muted: bool) -> None:
        self.btn_mute.setIcon(icons.icon("mute" if muted else "volume", "#ef4444" if muted else ICON))

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
