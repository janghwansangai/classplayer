"""네트워크나 화면 없이 돌릴 수 있는 단위 테스트."""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from classplayer.youtube.common import is_url, is_youtube_url, youtube_video_id  # noqa: E402
from classplayer.vr.view import FOV_MAX, FOV_MIN, VrView  # noqa: E402


def test_youtube_id_forms():
    vid = "aqz-KE-bpKQ"
    assert youtube_video_id(f"https://www.youtube.com/watch?v={vid}&t=10s") == vid
    assert youtube_video_id(f"https://youtu.be/{vid}?si=abc") == vid
    assert youtube_video_id(f"https://www.youtube.com/shorts/{vid}") == vid
    assert youtube_video_id(f"https://m.youtube.com/live/{vid}") == vid
    assert youtube_video_id("https://www.youtube.com/playlist?list=PL123") is None
    assert youtube_video_id("https://example.com/watch?v=aqz-KE-bpKQ") is None
    assert youtube_video_id("/Users/me/영상.mp4") is None


def test_url_detection():
    assert is_url("https://www.youtube.com/watch?v=x")
    assert not is_url("/Users/me/video.mp4")
    assert is_youtube_url("https://youtu.be/aqz-KE-bpKQ")
    assert not is_youtube_url("https://vimeo.com/123")


def test_vr_zoom_guard_limits_magnification():
    v = VrView(max_magnification=3.0)
    view_w, tex_w = 1920, 3840            # FHD 화면, 4K 등장방형
    v.zoom(0.01, view_w, tex_w)           # 아주 세게 확대
    mag = v.compute_magnification(view_w, tex_w)
    assert mag <= 3.0 + 1e-6
    assert v.fov >= FOV_MIN


def test_vr_zoom_out_clamped():
    v = VrView()
    v.zoom(100.0, 1920, 3840)
    assert v.fov == FOV_MAX


def test_vr_magnification_math():
    v = VrView()
    v.fov = math.radians(90)
    # 8K 등장방형을 FHD 화면에서 90도로 보면 원본 1920픽셀 → 1:1
    assert abs(v.compute_magnification(1920, 7680) - 1.0) < 1e-6
    # 4K 는 2배 확대 상태
    assert abs(v.compute_magnification(1920, 3840) - 2.0) < 1e-6


def test_vr_eac_effective_width():
    v = VrView(projection="eac")
    assert abs(v.effective_full_width(3000) - 4000) < 1e-6
    v2 = VrView(stereo="sbs")
    assert abs(v2.effective_full_width(4000) - 2000) < 1e-6


def test_vr_rotate_wraps_and_clamps():
    v = VrView()
    v.rotate(math.radians(350), 0)
    assert -math.pi <= v.yaw <= math.pi
    v.rotate(0, math.radians(200))
    assert v.pitch <= math.radians(89) + 1e-9
