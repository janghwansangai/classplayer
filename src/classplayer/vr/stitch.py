"""듀얼 피쉬아이(두 렌즈) 360도 영상 이어 붙이기: 렌즈 보정값, 카메라 식별, 자동 맞추기.

화면에 그리는 계산은 셰이더(vr/shaders.py 의 dfisheyeColor)가 하고, 여기 있는 lens_uv() 는 그와 똑같은
계산을 파이썬으로 한 것이다. '자동 맞추기'는 이 함수로 두 렌즈가 겹치는 띠의 밝기를 비교해,
어긋남이 가장 작아지는 렌즈 화각과 뒤 렌즈 틀어짐(좌우·위아래·기울기)을 찾는다.

좌표 약속
- 세계 방향 d: x 오른쪽, y 위, z 뒤 (정면은 -z)
- 카메라 좌표 v: x 오른쪽, y 아래, z 앞 렌즈 광축. v = (c.x, -c.y, -c.z), c = G·d (G = 수평·방향 보정)
- 앞 렌즈 로컬 = v, 뒤 렌즈 로컬 = B·(-v.x, v.y, -v.z)  (B = 뒤 렌즈 보정 회전)
- 렌즈 이미지: 등거리 투영, 반지름 rr = R · φ / (화각/2), 칸 안 좌표 = C + rr·(cosθ, sinθ)
- 텍스처는 두 칸이 나란한 2:1. 기본은 오른쪽 칸 = 앞 렌즈 (swap 이면 반대)
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field

_INSTA_MAGIC = b"8db42d694ccc418790edff439fe026bf"


# ---------------------------------------------------------------------------- 매개변수
@dataclass
class StitchParams:
    fov_front: float = 190.0          # 렌즈 화각(도) — 반지름 R 에 대응하는 전체 각
    fov_back: float = 190.0
    c_front: tuple[float, float] = (0.5, 0.5)   # 칸 안 원의 중심 (0..1)
    c_back: tuple[float, float] = (0.5, 0.5)
    r_front: float = 0.5               # 칸 폭 대비 원 반지름
    r_back: float = 0.5
    back_yaw: float = 0.0              # 뒤 렌즈 틀어짐 (도)
    back_pitch: float = 0.0
    back_roll: float = 0.0
    g_yaw: float = 0.0                 # 전체 방향·수평 보정 (도)
    g_pitch: float = 0.0
    g_roll: float = 0.0
    blend: float = 4.0                 # 이음새를 섞는 폭 (±도)
    swap: bool = False

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, s: str) -> "StitchParams":
        d = json.loads(s)
        p = cls()
        for k, v in d.items():
            if hasattr(p, k):
                setattr(p, k, tuple(v) if isinstance(getattr(p, k), tuple) else v)
        return p

    def copy(self) -> "StitchParams":
        return StitchParams.from_json(self.to_json())


# ---------------------------------------------------------------------------- 회전 행렬
Mat = tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]


def _mul(a: Mat, b: Mat) -> Mat:
    return tuple(tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)) for i in range(3))  # type: ignore[return-value]


def rot_matrix(yaw: float, pitch: float, roll: float) -> Mat:
    """yaw: y축, pitch: x축, roll: z축 회전(도). R = Ry · Rx · Rz"""
    y, p, r = math.radians(yaw), math.radians(pitch), math.radians(roll)
    cy, sy, cp, sp, cr, sr = math.cos(y), math.sin(y), math.cos(p), math.sin(p), math.cos(r), math.sin(r)
    ry = ((cy, 0.0, sy), (0.0, 1.0, 0.0), (-sy, 0.0, cy))
    rx = ((1.0, 0.0, 0.0), (0.0, cp, -sp), (0.0, sp, cp))
    rz = ((cr, -sr, 0.0), (sr, cr, 0.0), (0.0, 0.0, 1.0))
    return _mul(_mul(ry, rx), rz)


def _apply(m: Mat, v: tuple[float, float, float]) -> tuple[float, float, float]:
    return (m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
            m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
            m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2])


def flat(m: Mat) -> list[float]:
    """행 우선 9개 (glUniformMatrix3fv 에 transpose=True 로 넘긴다)."""
    return [m[i][j] for i in range(3) for j in range(3)]


# ---------------------------------------------------------------------------- 셰이더와 같은 계산
def _lens(l: tuple[float, float, float], c: tuple[float, float], r: float, fov: float, tile: int):
    phi = math.acos(max(-1.0, min(1.0, l[2])))
    theta = math.atan2(l[1], l[0])
    rr = r * phi / (math.radians(fov) * 0.5)
    tx, ty = c[0] + rr * math.cos(theta), c[1] + rr * math.sin(theta)
    return phi, ((tile + tx) * 0.5, ty)


def lens_uv(v: tuple[float, float, float], p: StitchParams, back_rot: Mat):
    """카메라 좌표 v → (앞 φ, 앞 uv, 뒤 φ, 뒤 uv). uv 는 전체 텍스처 기준 (0..1, y 아래)."""
    tile_front = 0 if p.swap else 1
    pf, uf = _lens(v, p.c_front, p.r_front, p.fov_front, tile_front)
    lb = _apply(back_rot, (-v[0], v[1], -v[2]))
    pb, ub = _lens(lb, p.c_back, p.r_back, p.fov_back, 1 - tile_front)
    return pf, uf, pb, ub


# ---------------------------------------------------------------------------- 카메라 식별
@dataclass
class CameraInfo:
    key: str                       # 보정값을 기억하는 이름 (기종 + 일련번호)
    label: str                     # 사람이 읽는 이름
    defaults: StitchParams = field(default_factory=StitchParams)
    dual_stream_order: str = "21"  # 렌즈별 영상 두 개를 나란히 붙이는 순서: "21" = [2번][1번]


def _insta_trailer(path: str) -> tuple[str, str, list[float] | None]:
    """Insta360 파일 끝의 기종·일련번호·렌즈 보정값(offset)."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - 3_000_000))
            tail = f.read()
    except OSError:
        return "", "", None
    if not tail.endswith(_INSTA_MAGIC):
        return "", "", None
    m = re.search(rb"Insta360 [A-Za-z0-9 ]{1,20}", tail)
    s = re.search(rb"\n\x0e([A-Z0-9]{14})", tail)
    o = re.search(rb"2_([\d.]+)_([\d.]+)_([\d.]+)_(-?[\d.]+)_(-?[\d.]+)_(-?[\d.]+)_"
                  rb"([\d.]+)_([\d.]+)_([\d.]+)_(-?[\d.]+)_(-?[\d.]+)_(-?[\d.]+)_(\d+)_(\d+)", tail)
    model = m.group().decode(errors="ignore").strip() if m else "Insta360"
    serial = s.group(1).decode() if s else ""
    off = [float(x) for x in o.groups()] if o else None
    return model, serial, off


def _ffprobe_encoder(ffprobe: str | None, path: str) -> str:
    if not ffprobe:
        return ""
    try:
        flags = 0x08000000 if sys.platform.startswith("win") else 0
        out = subprocess.run([ffprobe, "-v", "quiet", "-show_entries", "format_tags=encoder", "-of", "json", path],
                             capture_output=True, text=True, timeout=15, creationflags=flags).stdout
        return str(json.loads(out or "{}").get("format", {}).get("tags", {}).get("encoder", ""))
    except Exception:  # noqa: BLE001
        return ""


def identify_camera(path: str, ffprobe: str | None = None) -> CameraInfo:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".insv", ".insp", ".lrv"):
        model, serial, off = _insta_trailer(path)
        p = StitchParams(fov_front=200.0, fov_back=200.0)
        if off and len(off) >= 14:
            # 원 위치·크기를 칸(한 렌즈) 기준 비율로. 첫 렌즈 = 왼쪽 칸 = 뒤, 둘째 = 오른쪽 칸 = 앞
            w = off[12] / 2.0 or 1.0
            h = off[13] or 1.0
            p.r_back, p.c_back = off[0] / w, (off[1] / w, off[2] / h)
            p.r_front, p.c_front = off[6] / w, ((off[7] - w) / w, off[8] / h)
        # 같은 카메라라도 원본(insv)과 저화질본(lrv)은 크기·자르기가 달라 따로 기억한다
        key = f"{model} {serial} {ext.lstrip('.')}".strip()
        return CameraInfo(key=key, label=model + (f" ({serial})" if serial else ""), defaults=p, dual_stream_order="21")
    if ext in (".osv", ".lrf"):
        enc = _ffprobe_encoder(ffprobe, path) or "DJI 360"
        p = StitchParams(fov_front=195.0, fov_back=195.0)
        if "avata" in enc.lower():
            # 드론: 렌즈가 위(하늘)·아래(땅)를 본다. 오른쪽 칸(앞 렌즈) = 땅 쪽 → 정면이 수평이 되게 90도 세운다
            p.g_pitch = 90.0
        return CameraInfo(key=f"{enc} {ext.lstrip('.')}", label=enc, defaults=p, dual_stream_order="12")
    return CameraInfo(key="dfisheye", label="듀얼 피쉬아이", defaults=StitchParams())


# ---------------------------------------------------------------------------- 자동 맞추기
@dataclass
class GrayFrame:
    w: int
    h: int
    data: bytes

    def sample(self, u: float, v: float) -> float | None:
        x, y = u * self.w - 0.5, v * self.h - 0.5
        if x < 0 or y < 0 or x >= self.w - 1 or y >= self.h - 1:
            return None
        x0, y0 = int(x), int(y)
        fx, fy = x - x0, y - y0
        i = y0 * self.w + x0
        d = self.data
        a = d[i] + (d[i + 1] - d[i]) * fx
        b = d[i + self.w] + (d[i + self.w + 1] - d[i + self.w]) * fx
        return a + (b - a) * fy


def grab_frames(ffmpeg: str, path: str, duration: float, dual_order: str | None, tile: int = 512,
                count: int = 3) -> list[GrayFrame]:
    """재생 화면과 같은 배치(두 칸 나란히)로 흑백 프레임 몇 장을 뽑는다."""
    frames: list[GrayFrame] = []
    from classplayer.bootstrap import run_low_priority
    for k in range(count):
        t = duration * (k + 1) / (count + 1) if duration > 0 else 1.0 + k
        if dual_order:
            a, b = ("[0:v:1]", "[0:v:0]") if dual_order == "21" else ("[0:v:0]", "[0:v:1]")
            vf = ["-filter_complex", f"{a}scale={tile}:{tile}[l];{b}scale={tile}:{tile}[r];[l][r]hstack,format=gray"]
        else:
            vf = ["-vf", f"scale={tile * 2}:{tile},format=gray"]
        cmd = [ffmpeg, "-v", "error", "-ss", f"{t:.2f}", "-i", path, *vf, "-frames:v", "1",
               "-f", "rawvideo", "-pix_fmt", "gray", "-"]
        try:
            out, _code = run_low_priority(cmd, timeout=60)
        except Exception:  # noqa: BLE001
            continue
        if len(out) == tile * 2 * tile:
            frames.append(GrayFrame(tile * 2, tile, out))
    return frames


def _band_dirs(band_deg: float = 6.0, rings: int = 5, steps: int = 120) -> list[tuple[float, float, float]]:
    """앞 렌즈 광축에서 90도 둘레(두 렌즈가 겹치는 띠)의 방향들."""
    out = []
    for i in range(rings):
        phi = math.radians(90.0 - band_deg + 2 * band_deg * i / max(1, rings - 1))
        for j in range(steps):
            th = 2 * math.pi * j / steps
            out.append((math.sin(phi) * math.cos(th), math.sin(phi) * math.sin(th), math.cos(phi)))
    return out


def seam_cost(p: StitchParams, frames: list[GrayFrame], dirs) -> float:
    """겹치는 띠에서 두 렌즈 밝기 차이 (밝기·대비를 맞춘 뒤). 작을수록 잘 맞음."""
    br = rot_matrix(p.back_yaw, p.back_pitch, p.back_roll)
    lim_f, lim_b = math.radians(p.fov_front) * 0.5, math.radians(p.fov_back) * 0.5
    total, n_all = 0.0, 0
    for fr in frames:
        a_vals, b_vals = [], []
        for v in dirs:
            pf, uf, pb, ub = lens_uv(v, p, br)
            if pf > lim_f or pb > lim_b:
                continue
            a = fr.sample(*uf)
            b = fr.sample(*ub)
            if a is None or b is None:
                continue
            a_vals.append(a)
            b_vals.append(b)
        n = len(a_vals)
        if n < len(dirs) * 0.5:
            return 1e9        # 겹치는 부분이 너무 적음 = 엉뚱한 값
        ma, mb = sum(a_vals) / n, sum(b_vals) / n
        sa = math.sqrt(sum((x - ma) ** 2 for x in a_vals) / n) or 1.0
        sb = math.sqrt(sum((x - mb) ** 2 for x in b_vals) / n) or 1.0
        total += sum(abs((x - ma) / sa - (y - mb) / sb) for x, y in zip(a_vals, b_vals))
        n_all += n
    return total / n_all if n_all else 1e9


def auto_align(start: StitchParams, frames: list[GrayFrame], progress=None) -> tuple[StitchParams, float, float]:
    """화각과 뒤 렌즈 틀어짐을 찾는다. (결과, 처음 오차, 최종 오차)"""
    dirs = _band_dirs()
    best = start.copy()
    best.back_yaw = best.back_pitch = 0.0

    def cost(q: StitchParams) -> float:
        return seam_cost(q, frames, dirs)

    # 1) 뒤 렌즈가 90도 단위로 돌아가 있는지 + 대략의 화각
    cand = []
    for roll0 in (0.0, 90.0, 180.0, -90.0):
        for fov in [180.0 + 2.5 * i for i in range(19)]:      # 180 ~ 225
            q = best.copy()
            q.back_roll, q.fov_front, q.fov_back = roll0, fov, fov
            cand.append((cost(q), q))
    cand.sort(key=lambda t: t[0])
    c0, best = cand[0]
    first = cost(start)
    if progress:
        progress(0.3)
    # 2) 좌표 하강: 화각(두 렌즈 같이), 화각 차이, 뒤 렌즈 좌우·위아래·기울기
    cur = c0
    steps = [2.0, 1.0, 0.5, 0.25, 0.1]
    for si, step in enumerate(steps):
        improved = True
        rounds = 0
        while improved and rounds < 8:
            improved = False
            rounds += 1
            for name in ("fov", "dfov", "back_yaw", "back_pitch", "back_roll"):
                for sgn in (1, -1):
                    q = best.copy()
                    if name == "fov":
                        q.fov_front += sgn * step
                        q.fov_back += sgn * step
                    elif name == "dfov":
                        q.fov_front += sgn * step * 0.5
                        q.fov_back -= sgn * step * 0.5
                    else:
                        setattr(q, name, getattr(q, name) + sgn * step)
                    c = cost(q)
                    if c < cur - 1e-6:
                        cur, best, improved = c, q, True
                        break
        if progress:
            progress(0.3 + 0.7 * (si + 1) / len(steps))
    return best, first, cur
