"""360도 시점 상태(요, 피치, 시야각)와 화질 보호용 확대 한계 계산."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

FOV_MIN = math.radians(15.0)
FOV_MAX = math.radians(140.0)
PITCH_LIMIT = math.radians(89.0)

PROJECTIONS = ("equirect", "eac")
STEREO_MODES = ("mono", "tb", "sbs")


@dataclass
class VrView:
    yaw: float = 0.0            # 라디안, 양수 = 오른쪽
    pitch: float = 0.0          # 라디안, 양수 = 위
    fov: float = math.radians(90.0)   # 가로 시야각
    projection: str = "equirect"
    stereo: str = "mono"
    hq: bool = True
    max_magnification: float = 3.0
    # 아래는 렌더러가 매 프레임 채워 넣는 값
    magnification: float = field(default=1.0, compare=False)

    def reset(self) -> None:
        self.yaw = 0.0
        self.pitch = 0.0
        self.fov = math.radians(90.0)

    def rotate(self, dyaw: float, dpitch: float) -> None:
        self.yaw = (self.yaw + dyaw + math.pi) % (2 * math.pi) - math.pi
        self.pitch = max(-PITCH_LIMIT, min(PITCH_LIMIT, self.pitch + dpitch))

    def effective_full_width(self, tex_w: int) -> float:
        """360도 전체가 가로 몇 원본 픽셀에 해당하는지."""
        w = float(tex_w)
        if self.stereo == "sbs":
            w *= 0.5
        if self.projection == "eac":
            # 큐브맵 한 면(90도)이 폭의 1/3 → 360도는 폭의 4/3에 해당
            w *= 4.0 / 3.0
        return w

    def min_fov_for(self, view_w_px: int, tex_w: int) -> float:
        """화질 보호: 확대 배율이 max_magnification을 넘지 않는 최소 시야각."""
        if tex_w <= 0 or view_w_px <= 0:
            return FOV_MIN
        full = self.effective_full_width(tex_w)
        guard = 2 * math.pi * view_w_px / (full * self.max_magnification)
        return max(FOV_MIN, min(FOV_MAX, guard))

    def zoom(self, factor: float, view_w_px: int, tex_w: int) -> None:
        """factor < 1 이면 확대."""
        lo = self.min_fov_for(view_w_px, tex_w)
        self.fov = max(lo, min(FOV_MAX, self.fov * factor))

    def compute_magnification(self, view_w_px: int, tex_w: int) -> float:
        if tex_w <= 0 or view_w_px <= 0:
            return 1.0
        full = self.effective_full_width(tex_w)
        src_px = full * self.fov / (2 * math.pi)
        self.magnification = view_w_px / src_px if src_px > 0 else 1.0
        return self.magnification

    def describe(self) -> str:
        return (f"360° 시야각 {math.degrees(self.fov):.0f}° · "
                f"확대 {self.magnification:.1f}× (원본 1픽셀 기준) · "
                f"{'EAC' if self.projection == 'eac' else '등장방형'}"
                + ("" if self.stereo == "mono" else f" · {self.stereo.upper()}"))
