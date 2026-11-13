"""ffprobe로 로컬 파일의 360도/입체 메타데이터를 읽는다."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass


@dataclass
class SphericalInfo:
    is_spherical: bool = False
    projection: str = "equirect"   # equirect | eac
    stereo: str = "mono"           # mono | tb | sbs
    width: int = 0
    height: int = 0
    video_streams: int = 1
    dual_stream: bool = False      # insv처럼 렌즈별로 영상 스트림이 따로 있어 합쳐야 하는 경우


def probe_spherical(ffprobe: str | None, path: str) -> SphericalInfo:
    info = SphericalInfo()
    if not ffprobe:
        return info
    try:
        out = subprocess.run(
            [ffprobe, "-v", "quiet", "-print_format", "json", "-show_streams", path],
            capture_output=True, text=True, timeout=15, check=False,
        ).stdout
        data = json.loads(out or "{}")
    except Exception:  # noqa: BLE001
        return info
    vids = [st for st in data.get("streams", []) if st.get("codec_type") == "video"
            and not (st.get("disposition") or {}).get("attached_pic") and int(st.get("width") or 0) > 64]
    if not vids:
        return info
    info.video_streams = len(vids)
    st = vids[0]
    info.width = int(st.get("width") or 0)
    info.height = int(st.get("height") or 0)
    for sd in st.get("side_data_list", []) or []:
        t = str(sd.get("side_data_type", "")).lower()
        if "spherical" in t:
            info.is_spherical = True
            proj = str(sd.get("projection", "")).lower()
            info.projection = "eac" if "cube" in proj else "equirect"
        if "stereo3d" in t:
            mode = str(sd.get("type", "")).lower()
            if "top" in mode or "bottom" in mode:
                info.stereo = "tb"
            elif "side" in mode:
                info.stereo = "sbs"
    tags = {str(k).lower(): str(v).lower() for k, v in (st.get("tags") or {}).items()}
    if tags.get("spherical") == "true" or "equirectangular" in tags.get("projectiontype", ""):
        info.is_spherical = True
    if tags.get("stereomode") in ("top-bottom", "top_bottom"):
        info.stereo = "tb"
    elif tags.get("stereomode") in ("left-right", "left_right", "side-by-side"):
        info.stereo = "sbs"
    lower = path.lower()
    if lower.endswith((".osv", ".lrf")) and info.width and info.height:
        # DJI 360 원본(OSV)과 저화질 프록시(LRF): 두 렌즈 원이 나란히 담긴 듀얼 피쉬아이 (2:1)
        if abs(info.width / info.height - 2.0) < 0.1:
            info.is_spherical = True
            info.projection = "dfisheye"
        return info
    if lower.endswith((".insv", ".insp", ".lrv")) and info.width and info.height:
        ratio = info.width / info.height
        if abs(ratio - 2.0) < 0.1:
            # 한 프레임에 두 렌즈가 나란히: 듀얼 피쉬아이
            info.is_spherical = True
            info.projection = "dfisheye"
        elif len(vids) >= 2 and abs(ratio - 1.0) < 0.1:
            # X4/X5 처럼 렌즈별 영상 스트림이 2개 (각각 정사각형 원 하나): 재생 시 나란히 합친다
            info.is_spherical = True
            info.projection = "dfisheye"
            info.dual_stream = True
        elif info.is_spherical:
            info.projection = "equirect"
        return info
    if not info.is_spherical and info.width and info.height:
        ratio = info.width / info.height
        # 메타데이터가 없어도 정확히 2:1 비율의 큰 영상은 360도일 가능성이 높다
        if abs(ratio - 2.0) < 0.02 and info.width >= 2048:
            info.is_spherical = True
            info.projection = "equirect"
        elif abs(ratio - 1.0) < 0.02 and info.width >= 2048:
            info.is_spherical = True
            info.projection = "equirect"
            info.stereo = "tb"
    return info
