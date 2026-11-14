"""360 이음새 맞추기 창: 두 렌즈 영상이 이어지는 부분의 어긋남을 직접 또는 자동으로 맞춘다."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                               QPushButton, QSlider, QVBoxLayout)

from classplayer.vr.stitch import StitchParams


class _Row:
    """슬라이더 + 값 표시 한 줄. 슬라이더는 정수라 scale 배로 저장한다."""

    def __init__(self, grid: QGridLayout, row: int, label: str, lo: float, hi: float, step: float,
                 unit: str, on_change, tip: str = "") -> None:
        self.scale = 1.0 / step
        self.unit = unit
        self.lbl = QLabel(label)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(int(round(lo * self.scale)), int(round(hi * self.scale)))
        self.slider.setSingleStep(1)
        self.slider.setPageStep(int(round(1.0 * self.scale)) or 1)
        self.val = QLabel("")
        self.val.setMinimumWidth(64)
        self.val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if tip:
            for w in (self.lbl, self.slider):
                w.setToolTip(tip)
        grid.addWidget(self.lbl, row, 0)
        grid.addWidget(self.slider, row, 1)
        grid.addWidget(self.val, row, 2)
        self.slider.valueChanged.connect(lambda _v: (self._show(), on_change()))

    def _show(self) -> None:
        self.val.setText(f"{self.value():.2f}".rstrip("0").rstrip(".") + self.unit)

    def value(self) -> float:
        return self.slider.value() / self.scale

    def set(self, v: float) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(int(round(v * self.scale)))
        self.slider.blockSignals(False)
        self._show()


class StitchDialog(QDialog):
    params_changed = Signal(object)      # StitchParams
    auto_requested = Signal()
    reset_requested = Signal()

    LAYOUTS = (("frontback", "앞·뒤 렌즈 (손에 드는 카메라: Insta360 X 시리즈 등)"),
               ("updown", "위·아래 렌즈 (드론: DJI Avata 360 등)"))

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("360 이음새 맞추기")
        self.setModal(False)
        self.setWindowFlag(Qt.WindowType.Tool, True)
        self.setMinimumWidth(780)
        self._p = StitchParams()
        self._loading = False

        self.cam = QLabel("")
        self.cam.setWordWrap(True)
        intro = QLabel("두 렌즈 영상이 만나는 곳(이음새)이 어긋나 보이면 '자동 맞추기'를 누르세요. "
                       "영상 몇 장면을 비교해 가장 잘 맞는 값을 찾고, 이 카메라에 기억합니다. "
                       "아주 가까운 물체는 두 렌즈의 위치 차이 때문에 완전히 맞지 않을 수 있습니다.")
        intro.setWordWrap(True)
        intro.setObjectName("muted")

        self.btn_auto = QPushButton("자동 맞추기")
        self.btn_auto.setObjectName("primary")
        self.btn_auto.clicked.connect(self.auto_requested)
        self.btn_reset = QPushButton("원래대로")
        self.btn_reset.setToolTip("카메라 기본값으로 되돌립니다")
        self.btn_reset.clicked.connect(self.reset_requested)
        self.status = QLabel("")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        top = QHBoxLayout()
        top.addWidget(self.btn_auto)
        top.addWidget(self.btn_reset)
        top.addStretch(1)

        g1 = QGroupBox("렌즈")
        gl1 = QGridLayout(g1)
        self.r_fov = _Row(gl1, 0, "렌즈 화각", 170, 235, 0.1, "°", self._changed,
                          "원이 담는 각도. 이음새에서 물체가 늘어나거나 겹쳐 보이면 조절")
        self.r_dfov = _Row(gl1, 1, "앞뒤 화각 차이", -6, 6, 0.05, "°", self._changed)
        g2 = QGroupBox("뒤 렌즈 틀어짐")
        gl2 = QGridLayout(g2)
        self.r_by = _Row(gl2, 0, "좌우", -10, 10, 0.05, "°", self._changed)
        self.r_bp = _Row(gl2, 1, "위아래", -10, 10, 0.05, "°", self._changed)
        self.r_br = _Row(gl2, 2, "기울기", -180, 180, 0.05, "°", self._changed)
        g3 = QGroupBox("이음새")
        gl3 = QGridLayout(g3)
        self.r_blend = _Row(gl3, 0, "부드럽게 섞기", 0, 12, 0.5, "°", self._changed,
                            "이음새 양쪽을 이 각도만큼 서서히 섞어 경계선을 감춘다")
        g4 = QGroupBox("방향·수평")
        v4 = QVBoxLayout(g4)
        self.layout_combo = QComboBox()
        for key, label in self.LAYOUTS:
            self.layout_combo.addItem(label, key)
        self.layout_combo.setToolTip("카메라 배치")
        self.layout_combo.currentIndexChanged.connect(lambda _i: self._changed())
        v4.addWidget(self.layout_combo)          # 따로 한 줄 (격자에 넣으면 아래 줄과 겹쳐 보였다)
        gl4 = QGridLayout()
        v4.addLayout(gl4)
        self.r_groll = _Row(gl4, 0, "수평 기울기", -180, 180, 0.5, "°", self._changed, "화면 전체가 기울어 보이면 조절")
        self.r_gpitch = _Row(gl4, 1, "앞뒤 기울기", -45, 45, 0.5, "°", self._changed)
        self.swap = QCheckBox("앞/뒤 렌즈 좌우 바꾸기")
        self.swap.toggled.connect(lambda _c: self._changed())
        v4.addWidget(self.swap)

        b_close = QPushButton("닫기")
        b_close.clicked.connect(self.close)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(b_close)

        # 묶음 네 개를 2×2 로 놓아 창이 화면보다 길어지지 않게 한다 (세로로 쌓으면 노트북 화면에서 눌려 겹쳤다)
        groups = QGridLayout()
        groups.setHorizontalSpacing(12)
        groups.setVerticalSpacing(10)
        groups.addWidget(g1, 0, 0)
        groups.addWidget(g2, 0, 1)
        groups.addWidget(g3, 1, 0)
        groups.addWidget(g4, 1, 1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(10)
        lay.addWidget(self.cam)
        lay.addWidget(intro)
        lay.addLayout(top)
        lay.addWidget(self.status)
        lay.addLayout(groups)
        lay.addStretch(1)
        lay.addLayout(bottom)

    # ------------------------------------------------------------------
    def set_camera(self, label: str) -> None:
        self.cam.setText(f"<b>카메라</b>: {label}")

    def set_status(self, text: str) -> None:
        self.status.setText(text)

    def set_busy(self, busy: bool) -> None:
        self.btn_auto.setEnabled(not busy)
        self.btn_auto.setText("맞추는 중…" if busy else "자동 맞추기")

    def set_params(self, p: StitchParams) -> None:
        self._loading = True
        self._p = p.copy()
        fov = (p.fov_front + p.fov_back) / 2
        self.r_fov.set(fov)
        self.r_dfov.set(p.fov_back - p.fov_front)
        self.r_by.set(p.back_yaw)
        self.r_bp.set(p.back_pitch)
        self.r_br.set(p.back_roll)
        self.r_blend.set(p.blend)
        updown = abs(p.g_pitch) >= 45
        self.layout_combo.setCurrentIndex(1 if updown else 0)
        self.r_gpitch.set(p.g_pitch - (90.0 if p.g_pitch >= 45 else -90.0 if p.g_pitch <= -45 else 0.0))
        self.r_groll.set(p.g_roll)
        self.swap.setChecked(p.swap)
        self._loading = False

    def params(self) -> StitchParams:
        p = self._p.copy()
        fov, d = self.r_fov.value(), self.r_dfov.value()
        p.fov_front, p.fov_back = fov - d / 2, fov + d / 2
        p.back_yaw, p.back_pitch, p.back_roll = self.r_by.value(), self.r_bp.value(), self.r_br.value()
        p.blend = self.r_blend.value()
        base = 90.0 if self.layout_combo.currentData() == "updown" else 0.0
        p.g_pitch = base + self.r_gpitch.value()
        p.g_roll = self.r_groll.value()
        p.swap = self.swap.isChecked()
        return p

    def _changed(self) -> None:
        if self._loading:
            return
        self._p = self.params()
        self.params_changed.emit(self._p)
