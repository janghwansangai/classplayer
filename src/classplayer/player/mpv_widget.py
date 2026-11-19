"""libmpv를 OpenGL로 그리는 Qt 위젯. 일반 재생과 360도 모드를 모두 담당한다.

일반 모드: mpv가 위젯의 기본 프레임버퍼에 직접 그린다 (하드웨어 디코딩 프레임이 GPU 안에 머문다).
360도 모드: mpv가 원본 해상도 크기의 오프스크린 텍스처에 그리고, 우리 셰이더가 그 텍스처를
시선 방향에 따라 투영해 화면에 그린다. CPU로 프레임을 복사하지 않으므로 8K에서도 부담이 없다.
"""
from __future__ import annotations

import math
from pathlib import Path
import os
import sys
import threading
from typing import Any

from PySide6.QtCore import QByteArray, QEvent, QPointF, Qt, Signal
from PySide6.QtGui import QImage, QMouseEvent, QNativeGestureEvent, QOpenGLContext, QSurfaceFormat, QWheelEvent
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from OpenGL import GL

from classplayer import bootstrap

bootstrap.setup_library_paths()
import mpv  # noqa: E402  (경로 설정 후에 import 해야 libmpv를 찾는다)

from classplayer.vr.shaders import FRAG_SRC, VERT_SRC  # noqa: E402
from classplayer.vr.stitch import flat, rot_matrix
from classplayer.vr.view import VrView  # noqa: E402

DRAG_THRESHOLD_PX = 4

# 영상 풀기(디코딩) 방식 — 설정 play/hwdec. 설정 › 영상 풀기 방식 메뉴에서 고른다.
#   auto   : 권장 (윈도우 = D3D11 로 풀고 복사, 맥 = 그래픽 카드 직접)
#   direct : 그래픽 카드 결과를 OpenGL 로 바로 넘김 (v0.6.1 까지의 기본, 윈도우에선 nvdec CUDA 연동 등)
#   dxva2  : 윈도우 예전 방식 DXVA2 로 풀고 복사
#   no     : CPU 로만 풀기
HWDEC_MODES = ("auto", "direct", "dxva2", "no")


def hwdec_value(mode: str, platform: str = sys.platform) -> str:
    """설정값 → mpv hwdec 값 (우선순위 목록).

    윈도우 '자동'은 2026-11-19 사용자 신고(소니 4K 56Mbps XAVC S 영상에서 옷 색이 늦게 따라오고 소리가 끊김,
    같은 파일이 맥·다른 플레이어에선 정상) 뒤로 OpenGL 연동(nvdec CUDA–GL, dxva2 NV_DX_interop) 대신
    윈도우 표준인 D3D11 로 풀어 평범한 메모리 그림으로 넘긴다. 드라이버마다 다른 연동 기능을 쓰지 않는 길.
    """
    win = platform.startswith("win")
    if mode == "no":
        return "no"
    if mode == "direct" or not win:
        return "auto-safe"
    if mode == "dxva2":
        return "dxva2-copy,auto-copy-safe"
    return "d3d11va-copy,auto-copy-safe"


def platform_options(platform: str = sys.platform) -> dict[str, Any]:
    """운영체제별로 더하는 mpv 옵션."""
    if platform.startswith("win"):
        return {
            # 소리 버퍼 0.2초(기본) → 0.5초: 무거운 영상에서 잠깐 밀려도 소리가 끊기지 않게.
            # 음량은 mpv 가 내보낼 때 적용하므로 음량 반응은 느려지지 않는다.
            "audio_buffer": 0.5,
            # 소프트웨어 디코딩 때 GPU 에 매핑된 메모리로 바로 풀기(direct rendering)를 끈다.
            # 윈도우 OpenGL 드라이버마다 동작이 달라 화면 깨짐 원인이 될 수 있다. 성능 차이는 작다.
            "vd_lavc_dr": "no",
        }
    return {}


def _get_proc_address(_ctx: Any, name: bytes) -> int:
    glctx = QOpenGLContext.currentContext()
    if glctx is None:
        return 0
    addr = glctx.getProcAddress(QByteArray(name))
    try:
        return int(addr) if addr else 0
    except TypeError:
        return 0


class MpvWidget(QOpenGLWidget):
    # 재생 상태 (mpv 스레드에서 발생 → Qt가 메인 스레드로 큐잉)
    position_changed = Signal(float)
    duration_changed = Signal(float)
    pause_changed = Signal(bool)
    video_size_changed = Signal(int, int)
    media_title_changed = Signal(str)
    file_loaded = Signal(str)
    load_failed = Signal(int)
    subs_changed = Signal(bool)        # 자막이 화면에 나오는지          # 열기 실패 (mpv 오류 번호)
    eof_reached = Signal()
    hwdec_changed = Signal(str)
    hwdec_error = Signal(int)         # 하드웨어 디코딩 오류 (이 파일에서 몇 번째)
    screenshot_done = Signal(str, bool)   # 캡처 저장 끝 (경로, 성공)
    volume_changed = Signal(float)
    mute_changed = Signal(bool)
    codec_changed = Signal(str)
    log_message = Signal(str, str, str)
    buffering_changed = Signal(bool)
    # 사용자 입력
    clicked = Signal()
    double_clicked = Signal()
    mouse_moved = Signal()
    wheel_scrolled = Signal(int)      # 360도 모드가 아닐 때의 휠 (볼륨 등)
    vr_changed = Signal(object)       # VrView 스냅샷
    _request_update = Signal()

    def __init__(self, parent=None, *, ytdl_path: str | None = None, js_runtime: str | None = None,
                 stream_max_height: int = 2160, vr_hq: bool = True, vr_max_magnification: float = 3.0,
                 hwdec_mode: str = "auto"):
        super().__init__(parent)
        fmt = QSurfaceFormat()
        fmt.setVersion(3, 3)
        fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
        fmt.setSwapInterval(int(os.environ.get("CLASSPLAYER_SWAP_INTERVAL", "1")))
        self.setFormat(fmt)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setMinimumSize(320, 180)

        opts: dict[str, Any] = {
            "vo": "libmpv",
            "hwdec": hwdec_value(hwdec_mode),
            "keep_open": "yes",
            "idle": "yes",
            "osc": False,
            "input_default_bindings": False,
            "input_vo_keyboard": False,
            "ytdl": True,
            "ytdl_format": (f"bestvideo[height<={stream_max_height}]+bestaudio/"
                            f"best[height<={stream_max_height}]/best"),
            "cache": "yes",
            "demuxer_max_bytes": "800MiB",
            "demuxer_max_back_bytes": "200MiB",
            "sub_auto": "fuzzy",
            "video_sync": "audio",
            "volume_max": 150,
        }
        opts.update(platform_options())
        self.hwdec_mode = hwdec_mode if hwdec_mode in HWDEC_MODES else "auto"
        self.hwdec_errors = 0         # 이 파일에서 하드웨어 디코딩 오류 수 (진단·안내용)
        self.gl_renderer = ""         # 그래픽 카드 이름 (환경 정보)
        script_opts = []
        if ytdl_path:
            script_opts.append(f"ytdl_hook-ytdl_path={ytdl_path}")
        if script_opts:
            opts["script_opts"] = ",".join(script_opts)
        if js_runtime:
            opts["ytdl_raw_options"] = f"js-runtimes={js_runtime}"

        self.mpv = mpv.MPV(log_handler=self._on_log, loglevel="info", **opts)
        self.ctx: mpv.MpvRenderContext | None = None
        self._proc_addr = None

        self._video_w = 0
        self._video_h = 0
        self.vr = VrView(hq=vr_hq, max_magnification=vr_max_magnification)
        self.vr_enabled = False
        self._vr_ok = False
        self._vr_tex = 0
        self._vr_fbo = 0
        self._vr_size = (0, 0)
        self._prog = 0
        self._vao = 0
        self._uniforms: dict[str, int] = {}
        self._max_tex = 4096
        self._flip_y = 0
        self._last_mag_key = None

        self.dual_lens_px = int(os.environ.get("CLASSPLAYER_DUAL_LENS_PX", "0") or 0)
        self._pending_subs: list[tuple[str, str, str]] = []
        self._press_pos: QPointF | None = None
        self._drag_last: QPointF | None = None
        self._dragged = False

        self._request_update.connect(self.update, Qt.ConnectionType.QueuedConnection)
        self.video_size_changed.connect(self._set_video_size)

        m = self.mpv
        m.observe_property("time-pos", lambda _n, v: self.position_changed.emit(float(v or 0.0)))
        m.observe_property("duration", lambda _n, v: self.duration_changed.emit(float(v or 0.0)))
        m.observe_property("pause", lambda _n, v: self.pause_changed.emit(bool(v)))
        m.observe_property("dwidth", self._on_dims)
        m.observe_property("dheight", self._on_dims)
        m.observe_property("media-title", lambda _n, v: self.media_title_changed.emit(str(v or "")))
        # 하드웨어 디코딩이 실패해 CPU 로 넘어가면 "no" 가 온다 → 그것도 알린다
        m.observe_property("hwdec-current", lambda _n, v: v is not None and self.hwdec_changed.emit(str(v)))
        m.observe_property("video-codec", lambda _n, v: self.codec_changed.emit(str(v or "")))
        m.observe_property("volume", lambda _n, v: self.volume_changed.emit(float(v or 0.0)))
        m.observe_property("mute", lambda _n, v: self.mute_changed.emit(bool(v)))
        m.observe_property("eof-reached", lambda _n, v: self.eof_reached.emit() if v else None)
        m.observe_property("sid", lambda _n, v: self.subs_changed.emit(v not in (False, None, "no", 0)))
        m.observe_property("paused-for-cache", lambda _n, v: self.buffering_changed.emit(bool(v)))

        @m.event_callback("end-file")
        def _ended(event):
            try:
                d = getattr(event, "data", None)
                if d is not None and int(getattr(d, "reason", -1)) == 4:   # MPV_END_FILE_REASON_ERROR
                    self.load_failed.emit(int(getattr(d, "error", 0)))
            except Exception:  # noqa: BLE001
                pass

        @m.event_callback("file-loaded")
        def _loaded(_event):
            self.hwdec_errors = 0
            try:
                self.file_loaded.emit(str(m.path or ""))
            except Exception:  # noqa: BLE001
                self.file_loaded.emit("")

    # ------------------------------------------------------------------ mpv 콜백
    def _on_log(self, level: str, component: str, message: str) -> None:
        if component == "vd" and "Using hardware decoding (" in message:
            self.hwdec_changed.emit(message.split("(", 1)[1].split(")", 1)[0])
            return
        if component == "vd" and "(hardware decoding)" in message:
            # 그래픽 카드가 프레임을 못 풀었다: 그 자리 화면이 번지고, 3번이면 mpv 가 CPU 로 바꾼다
            self.hwdec_errors += 1
            self.hwdec_error.emit(self.hwdec_errors)
        if level in ("info", "v", "debug", "trace"):
            return
        self.log_message.emit(level, component, message.rstrip())

    def _on_dims(self, _name: str, _value: Any) -> None:
        try:
            w, h = self.mpv.dwidth, self.mpv.dheight
        except Exception:  # noqa: BLE001
            return
        if w and h:
            self.video_size_changed.emit(int(w), int(h))

    def _set_video_size(self, w: int, h: int) -> None:
        self._video_w, self._video_h = w, h
        self.update()

    def _on_mpv_update(self) -> None:
        self._request_update.emit()

    # ------------------------------------------------------------------ 재생 제어
    @property
    def video_size(self) -> tuple[int, int]:
        return self._video_w, self._video_h

    @staticmethod
    def has_telemetry_srt(target: str) -> bool:
        """DJI 등 카메라가 영상 옆에 남기는 .SRT 는 자막이 아니라 촬영 기록(위치·ISO 등)이다."""
        p = Path(target)
        for ext in (".SRT", ".srt"):
            q = p.with_suffix(ext)
            try:
                if q.exists():
                    head = q.read_bytes()[:600].decode("utf-8", errors="ignore")
                    if "FrameCnt" in head or "[iso" in head or "[latitude" in head or "GPS(" in head:
                        return True
            except OSError:
                pass
        return False

    def play(self, target: str) -> None:
        """로컬 파일이나 일반 URL 재생 (mpv 내장 ytdl 훅 사용)."""
        self._reset_stream_options()
        self.set_dual_stream(False)
        local = not target.startswith(("http://", "https://"))
        try:
            # 로컬 파일은 디스크에서 바로 읽으므로 큰 버퍼가 필요 없다 (11GB 360 영상에서 메모리 2.3GB → 줄임)
            self.mpv["demuxer-max-bytes"] = "150MiB" if local else "800MiB"
            self.mpv["demuxer-max-back-bytes"] = "50MiB" if local else "200MiB"
        except Exception:  # noqa: BLE001
            pass
        try:
            # 촬영 기록 SRT 를 자막으로 불러와 화면을 덮지 않게
            self.mpv["sub-auto"] = "no" if (not target.startswith(("http://", "https://"))
                                            and self.has_telemetry_srt(target)) else "fuzzy"
        except Exception:  # noqa: BLE001
            pass
        self.mpv.play(target)
        self.mpv.pause = False
        self.pause_changed.emit(False)

    def play_stream(self, video_url: str, audio_url: str = "", *, title: str = "", user_agent: str = "",
                    subtitles: list[tuple[str, str, str]] | None = None, start: float = 0.0) -> None:
        """앱에서 직접 푼 스트림 주소 재생. 영상/소리가 따로 오면 audio-files 로 합친다."""
        self._reset_stream_options()
        try:
            self.mpv["demuxer-max-bytes"] = "800MiB"
            self.mpv["demuxer-max-back-bytes"] = "200MiB"
        except Exception:  # noqa: BLE001
            pass
        if start > 0:
            self.mpv["start"] = f"{start:.1f}"     # 링크의 &t=23s 같은 시작 위치
        self.mpv["ytdl"] = False
        if audio_url:
            self.mpv["audio-files"] = [audio_url]
        if user_agent:
            self.mpv["user-agent"] = user_agent
        if title:
            self.mpv["force-media-title"] = title
        self._pending_subs = list(subtitles or [])
        self.mpv.play(video_url)
        self.mpv.pause = False
        self.pause_changed.emit(False)

    DUAL_STREAM_FILTER = "[vid2][vid1]hstack[vo]"

    @staticmethod
    def dual_filter(swap: bool, lens_px: int = 0) -> str:
        """두 렌즈 합치기 lavfi-complex. lens_px 가 있으면 렌즈마다 그 크기(정사각형)로 줄인 뒤 붙인다.

        렌즈 좌표는 화면 비율(0~1)이라 줄여도 이음새 맞춤값은 그대로 맞는다.
        """
        a, b = ("vid1", "vid2") if swap else ("vid2", "vid1")
        if not lens_px:
            return f"[{a}][{b}]hstack[vo]"
        s = f"scale={lens_px}:{lens_px}:flags=fast_bilinear"
        return f"[{a}]{s}[l];[{b}]{s}[r];[l][r]hstack[vo]"

    def set_dual_lens_px(self, px: int) -> None:
        """두 렌즈 합칠 때 렌즈 한쪽의 해상도 (0 = 원본). 재생 중이면 바로 다시 적용."""
        self.dual_lens_px = max(0, int(px))
        on, swap = getattr(self, "_dual_args", (False, False))
        if on and self.dual_stream:
            try:
                self.mpv["lavfi-complex"] = self.dual_filter(swap, self.dual_lens_px)
            except Exception as e:  # noqa: BLE001
                self.log_message.emit("warn", "vr", f"합치기 해상도 바꾸기 실패: {e}")

    def set_dual_stream(self, on: bool, swap: bool = False, order: str | None = None) -> None:
        """렌즈별 영상 스트림이 2개인 파일(Insta360 X4 insv 등)을 가로로 붙여 듀얼 피쉬아이 한 장으로 만든다.

        Insta360 X4 실측: 1번 스트림이 화면 반대쪽(바깥 풍경) 렌즈라 오른쪽(앞)에, 2번(화면 쪽) 렌즈를 왼쪽(뒤)에 둔다. 필터를 거치려면 복사형 하드웨어 디코딩이 필요하다.
        """
        if order:
            swap = order == "12"
        self._dual_args = (on, swap)
        want = self.dual_filter(swap, self.dual_lens_px) if on else ""
        try:
            if (self.mpv["lavfi-complex"] or "") == want:
                return
            self.mpv["hwdec"] = self._hwdec_for(on)
            self.mpv["lavfi-complex"] = want
        except Exception as e:  # noqa: BLE001
            self.log_message.emit("warn", "vr", f"두 렌즈 스트림 합치기 실패: {e}")

    def _hwdec_for(self, dual: bool) -> str:
        if self.hwdec_mode == "no":
            return "no"
        # 두 렌즈 합치기(lavfi-complex)는 필터를 거치므로 복사형이어야 한다
        return "auto-copy-safe" if dual else hwdec_value(self.hwdec_mode)

    def set_hwdec_mode(self, mode: str) -> None:
        """영상 풀기 방식을 바꾼다. 재생 중이면 mpv 가 디코더를 다시 만들어 바로 적용된다."""
        self.hwdec_mode = mode if mode in HWDEC_MODES else "auto"
        self.hwdec_errors = 0
        try:
            self.mpv["hwdec"] = self._hwdec_for(self.dual_stream)
        except Exception as e:  # noqa: BLE001
            self.log_message.emit("warn", "vd", f"영상 풀기 방식 바꾸기 실패: {e}")

    @property
    def dual_stream(self) -> bool:
        try:
            return bool(self.mpv["lavfi-complex"])
        except Exception:  # noqa: BLE001
            return False

    def _reset_stream_options(self) -> None:
        self._pending_subs = []
        try:
            self.mpv["ytdl"] = True
            self.mpv["audio-files"] = []
            self.mpv["force-media-title"] = ""
            self.mpv["start"] = "none"
        except Exception:  # noqa: BLE001
            pass

    def add_pending_subtitles(self) -> None:
        subs, self._pending_subs = self._pending_subs, []
        for i, (lang, name, url) in enumerate(subs):
            try:
                self.mpv.command("sub-add", url, "select" if i == 0 else "auto", name, lang)
            except Exception as e:  # noqa: BLE001
                self.log_message.emit("warn", "sub", f"자막 추가 실패 ({lang}): {e}")

    def stop(self) -> None:
        self.mpv.command("stop")

    def toggle_pause(self) -> None:
        self.mpv.pause = not self.mpv.pause

    def set_pause(self, paused: bool) -> None:
        self.mpv.pause = paused

    def _cmd(self, *args) -> None:
        """재생 조작 명령. 파일을 여는 중·열기 전에는 mpv 가 오류를 내므로 조용히 무시한다."""
        try:
            self.mpv.command(*args)
        except SystemError:
            pass

    def seek_relative(self, seconds: float) -> None:
        self._cmd("seek", seconds, "relative")

    def seek_absolute(self, seconds: float, exact: bool = True) -> None:
        self._cmd("seek", seconds, "absolute+exact" if exact else "absolute+keyframes")

    def frame_step(self, back: bool = False) -> None:
        self._cmd("frame-back-step" if back else "frame-step")

    def set_volume(self, volume: float) -> None:
        self.mpv.volume = max(0.0, min(150.0, float(volume)))

    def toggle_mute(self) -> None:
        self.mpv.mute = not self.mpv.mute

    def set_speed(self, speed: float) -> None:
        self.mpv.speed = float(speed)

    def cycle_subtitles(self) -> None:
        self.mpv.command("cycle", "sub")

    def cycle_audio(self) -> None:
        self.mpv.command("cycle", "audio")

    def screenshot(self, path: str) -> bool:
        """캡처를 시작한다. 끝나면 screenshot_done(경로, 성공) 신호.

        일반 화면: mpv 가 영상 원래 해상도(4K 면 3840×2160)로, 화면과 같은 색 처리를 거쳐 무손실 PNG 로 저장한다
            (보이는 자막 포함, 10비트 영상은 16비트 PNG). mpv 가 따로 스레드에서 저장하므로 재생이 멈추지 않는다.
            예전엔 창에 보이는 크기(grabFramebuffer)로 저장해 해상도가 낮았다 (2026-11-19 사용자 요청).
        360도: 지금 보는 시점을 원본 화소 밀도에 맞는 크기(최대 4096 폭)로 따로 그려 저장한다.
        """
        if self.vr_enabled and self._vr_ok and self._vr_tex:
            img = self._grab_vr_hq()
            if img is None or img.isNull():
                img = self.grabFramebuffer()

            def save() -> None:
                ok = (not img.isNull()) and img.save(path)
                self.screenshot_done.emit(path, bool(ok))
            threading.Thread(target=save, daemon=True).start()
            return True
        try:
            fut = self.mpv.command_async("screenshot-to-file", path, "subtitles")
        except Exception as e:  # noqa: BLE001
            self.log_message.emit("warn", "screenshot", str(e))
            return False

        def done(f) -> None:
            try:
                f.result()
                ok = os.path.exists(path)
            except Exception as e:  # noqa: BLE001
                self.log_message.emit("warn", "screenshot", str(e))
                ok = False
            self.screenshot_done.emit(path, ok)
        fut.add_done_callback(done)
        return True

    def screenshot_window(self, path: str) -> bool:
        """창에 보이는 크기 그대로 저장 (원본 해상도 캡처가 실패했을 때)."""
        img = self.grabFramebuffer()
        return (not img.isNull()) and img.save(path)

    def _grab_vr_hq(self) -> QImage | None:
        """360도 시점을 화면보다 크게(원본 1픽셀 ≈ 결과 1픽셀) 따로 그려 그림으로 돌려준다."""
        w, h = self._view_px()
        tw, th = self._vr_size
        v = self.vr
        if tw <= 0 or w <= 0:
            return None
        src_px = v.effective_full_width(tw) * v.fov / (2 * math.pi)      # 지금 시야에 들어온 원본 가로 픽셀 수
        limit = min(4096, self._max_tex)
        cw = int(max(w, min(src_px, limit)))
        ch = max(1, round(cw * h / w))
        if ch > limit:
            cw, ch = max(1, round(cw * limit / ch)), limit
        tex = fbo = 0
        self.makeCurrent()
        try:
            tex = int(GL.glGenTextures(1))
            GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
            GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA8, cw, ch, 0, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, None)
            GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
            fbo = int(GL.glGenFramebuffers(1))
            GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, fbo)
            GL.glFramebufferTexture2D(GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0, GL.GL_TEXTURE_2D, tex, 0)
            if GL.glCheckFramebufferStatus(GL.GL_FRAMEBUFFER) != GL.GL_FRAMEBUFFER_COMPLETE:
                return None
            self._draw_vr(fbo, cw, ch, tw, th, emit=False)
            GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, fbo)
            GL.glPixelStorei(GL.GL_PACK_ALIGNMENT, 1)
            data = GL.glReadPixels(0, 0, cw, ch, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE)
            raw = bytes(data)
            img = QImage(raw, cw, ch, cw * 4, QImage.Format.Format_RGBA8888)
            # OpenGL 은 아래 줄부터 읽힌다 → 위아래 뒤집기 (grabFramebuffer 와 같은 방향)
            return img.mirrored(False, True).convertToFormat(QImage.Format.Format_RGB888)
        except Exception as e:  # noqa: BLE001
            self.log_message.emit("warn", "screenshot", f"360도 고해상도 캡처 실패: {e}")
            return None
        finally:
            try:
                if fbo:
                    GL.glDeleteFramebuffers(1, [fbo])
                if tex:
                    GL.glDeleteTextures(1, [tex])
                v.compute_magnification(w, tw)      # 화면 기준 확대 배율으로 되돌림
                self._restore_gl_state(int(self.defaultFramebufferObject()))
            finally:
                self.doneCurrent()

    def property(self, name: str, default: Any = None) -> Any:
        try:
            v = self.mpv._get_property(name)  # noqa: SLF001
            return default if v is None else v
        except Exception:  # noqa: BLE001
            return default

    # ------------------------------------------------------------------ 360도
    def set_vr_enabled(self, enabled: bool, projection: str | None = None, stereo: str | None = None) -> None:
        if projection:
            self.vr.projection = projection
        if stereo:
            self.vr.stereo = stereo
        self.vr_enabled = bool(enabled) and self._vr_ok
        if not self.vr_enabled and enabled and not self._vr_ok:
            self.log_message.emit("error", "vr", "360도 렌더러 초기화에 실패해 360도 모드를 켤 수 없습니다.")
        self.setCursor(Qt.CursorShape.OpenHandCursor if self.vr_enabled else Qt.CursorShape.ArrowCursor)
        self.update()
        self.vr_changed.emit(self.vr)

    def vr_reset(self) -> None:
        self.vr.reset()
        self.update()
        self.vr_changed.emit(self.vr)

    def vr_zoom(self, factor: float) -> None:
        self.vr.zoom(factor, self._view_px()[0], self._vr_size[0] or self._video_w)
        self.update()
        self.vr_changed.emit(self.vr)

    def vr_rotate_deg(self, dyaw: float, dpitch: float) -> None:
        self.vr.rotate(math.radians(dyaw), math.radians(dpitch))
        self.update()
        self.vr_changed.emit(self.vr)

    def set_vr_options(self, *, hq: bool | None = None, max_magnification: float | None = None) -> None:
        if hq is not None:
            self.vr.hq = hq
        if max_magnification is not None:
            self.vr.max_magnification = max_magnification
            self.vr.zoom(1.0, self._view_px()[0], self._vr_size[0] or self._video_w)
        self.update()
        self.vr_changed.emit(self.vr)

    def _view_px(self) -> tuple[int, int]:
        dpr = self.devicePixelRatioF()
        return max(1, int(self.width() * dpr)), max(1, int(self.height() * dpr))

    # ------------------------------------------------------------------ OpenGL
    def initializeGL(self) -> None:
        self._proc_addr = mpv.MpvGlGetProcAddressFn(_get_proc_address)
        self.ctx = mpv.MpvRenderContext(self.mpv, "opengl", opengl_init_params={"get_proc_address": self._proc_addr})
        self.ctx.update_cb = self._on_mpv_update
        if os.environ.get("CLASSPLAYER_BLOCK_TARGET", "1") == "0":
            self.ctx.block_for_target_time = False
        try:
            self._max_tex = int(GL.glGetIntegerv(GL.GL_MAX_TEXTURE_SIZE))
        except Exception:  # noqa: BLE001
            self._max_tex = 4096
        try:
            rnd = GL.glGetString(GL.GL_RENDERER) or b""
            ver = GL.glGetString(GL.GL_VERSION) or b""
            self.gl_renderer = f"{rnd.decode(errors='replace')} (OpenGL {ver.decode(errors='replace')})"
        except Exception:  # noqa: BLE001
            self.gl_renderer = ""
        try:
            self._init_vr_gl()
            self._vr_ok = True
        except Exception as e:  # noqa: BLE001
            self._vr_ok = False
            self.log_message.emit("error", "vr", f"셰이더 초기화 실패: {e}")

    def _compile(self, kind: int, src: str) -> int:
        sh = GL.glCreateShader(kind)
        GL.glShaderSource(sh, src)
        GL.glCompileShader(sh)
        if not GL.glGetShaderiv(sh, GL.GL_COMPILE_STATUS):
            raise RuntimeError(GL.glGetShaderInfoLog(sh).decode(errors="replace"))
        return sh

    def _init_vr_gl(self) -> None:
        vs = self._compile(GL.GL_VERTEX_SHADER, VERT_SRC)
        fs = self._compile(GL.GL_FRAGMENT_SHADER, FRAG_SRC)
        prog = GL.glCreateProgram()
        GL.glAttachShader(prog, vs)
        GL.glAttachShader(prog, fs)
        GL.glLinkProgram(prog)
        if not GL.glGetProgramiv(prog, GL.GL_LINK_STATUS):
            raise RuntimeError(GL.glGetProgramInfoLog(prog).decode(errors="replace"))
        GL.glDeleteShader(vs)
        GL.glDeleteShader(fs)
        self._prog = prog
        for name in ("u_tex", "u_texSize", "u_viewSize", "u_yaw", "u_pitch", "u_fov",
                     "u_projection", "u_stereo", "u_hq", "u_flipY", "u_fisheyeSwap",
                     "u_lensC0", "u_lensC1", "u_lensR0", "u_lensR1", "u_lensFov0", "u_lensFov1",
                     "u_globalRot", "u_backRot", "u_blend"):
            self._uniforms[name] = GL.glGetUniformLocation(prog, name)
        self._vao = GL.glGenVertexArrays(1)

    def _ensure_vr_target(self, vw: int, vh: int) -> tuple[int, int]:
        tw, th = min(vw, self._max_tex), min(vh, self._max_tex)
        if (tw, th) == self._vr_size and self._vr_tex:
            return self._vr_size
        self._release_vr_target()
        tex = GL.glGenTextures(1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA8, tw, th, 0, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, None)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)
        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
        fbo = GL.glGenFramebuffers(1)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, fbo)
        GL.glFramebufferTexture2D(GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0, GL.GL_TEXTURE_2D, tex, 0)
        status = GL.glCheckFramebufferStatus(GL.GL_FRAMEBUFFER)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self.defaultFramebufferObject())
        if status != GL.GL_FRAMEBUFFER_COMPLETE:
            GL.glDeleteFramebuffers(1, [fbo])
            GL.glDeleteTextures(1, [tex])
            raise RuntimeError(f"FBO 생성 실패 ({tw}x{th}, status={status})")
        self._vr_tex, self._vr_fbo, self._vr_size = int(tex), int(fbo), (tw, th)
        return self._vr_size

    def _release_vr_target(self) -> None:
        if self._vr_fbo:
            GL.glDeleteFramebuffers(1, [self._vr_fbo])
        if self._vr_tex:
            GL.glDeleteTextures(1, [self._vr_tex])
        self._vr_fbo = self._vr_tex = 0
        self._vr_size = (0, 0)

    def paintGL(self) -> None:
        if self.ctx is None:
            return
        w, h = self._view_px()
        target = int(self.defaultFramebufferObject())
        if self.vr_enabled and self._vr_ok and self._video_w > 0:
            try:
                tw, th = self._ensure_vr_target(self._video_w, self._video_h)
            except Exception as e:  # noqa: BLE001
                self._vr_ok = False
                self.vr_enabled = False
                self.log_message.emit("error", "vr", str(e))
                self.ctx.render(flip_y=True, opengl_fbo={"fbo": target, "w": w, "h": h})
                return
            self.ctx.render(flip_y=False, opengl_fbo={"fbo": self._vr_fbo, "w": tw, "h": th})
            self._draw_vr(target, w, h, tw, th)
        else:
            self.ctx.render(flip_y=True, opengl_fbo={"fbo": target, "w": w, "h": h})
        self._restore_gl_state(target)

    @staticmethod
    def _restore_gl_state(target: int) -> None:
        """mpv가 바꿔 둔 GL 상태를 되돌린다.

        그대로 두면 Qt가 이 위젯 위에 겹친 일반 위젯(전체화면 컨트롤바 등)을 합성할 때
        블렌딩·sRGB 설정이 꼬여 글자와 색이 흐릿하게 나온다.
        """
        try:
            GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, target)
            GL.glUseProgram(0)
            GL.glBindVertexArray(0)
            GL.glActiveTexture(GL.GL_TEXTURE0)
            GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
            GL.glDisable(GL.GL_DEPTH_TEST)
            GL.glDisable(GL.GL_SCISSOR_TEST)
            GL.glDisable(GL.GL_CULL_FACE)
            GL.glDisable(GL.GL_STENCIL_TEST)
            GL.glDisable(GL.GL_FRAMEBUFFER_SRGB)
            GL.glColorMask(GL.GL_TRUE, GL.GL_TRUE, GL.GL_TRUE, GL.GL_TRUE)
            GL.glDepthMask(GL.GL_TRUE)
            GL.glDisable(GL.GL_BLEND)
            GL.glBlendFuncSeparate(GL.GL_ONE, GL.GL_ONE_MINUS_SRC_ALPHA, GL.GL_ONE, GL.GL_ONE_MINUS_SRC_ALPHA)
            GL.glBlendEquation(GL.GL_FUNC_ADD)
            GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 4)
            GL.glPixelStorei(GL.GL_UNPACK_ROW_LENGTH, 0)
        except Exception:  # noqa: BLE001
            pass

    def _draw_vr(self, target: int, w: int, h: int, tw: int, th: int, emit: bool = True) -> None:
        v = self.vr
        mag = v.compute_magnification(w, tw)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, target)
        GL.glViewport(0, 0, w, h)
        GL.glDisable(GL.GL_DEPTH_TEST)
        GL.glDisable(GL.GL_BLEND)
        GL.glDisable(GL.GL_SCISSOR_TEST)
        GL.glDisable(GL.GL_CULL_FACE)
        GL.glUseProgram(self._prog)
        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self._vr_tex)
        wrap_s = GL.GL_REPEAT if (v.projection == "equirect" and v.stereo != "sbs") else GL.GL_CLAMP_TO_EDGE
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, wrap_s)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)
        if mag < 1.0:
            # 축소 중에는 밉맵으로 앨리어싱을 막는다
            GL.glGenerateMipmap(GL.GL_TEXTURE_2D)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR_MIPMAP_LINEAR)
        else:
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
        u = self._uniforms
        GL.glUniform1i(u["u_tex"], 0)
        GL.glUniform2f(u["u_texSize"], float(tw), float(th))
        GL.glUniform2f(u["u_viewSize"], float(w), float(h))
        GL.glUniform1f(u["u_yaw"], v.yaw)
        GL.glUniform1f(u["u_pitch"], v.pitch)
        GL.glUniform1f(u["u_fov"], v.fov)
        GL.glUniform1i(u["u_projection"], {"eac": 1, "dfisheye": 2}.get(v.projection, 0))
        st = v.stitch
        GL.glUniform1i(u["u_fisheyeSwap"], 1 if st.swap else 0)
        GL.glUniform2f(u["u_lensC0"], *st.c_front)
        GL.glUniform2f(u["u_lensC1"], *st.c_back)
        GL.glUniform1f(u["u_lensR0"], st.r_front)
        GL.glUniform1f(u["u_lensR1"], st.r_back)
        GL.glUniform1f(u["u_lensFov0"], math.radians(st.fov_front))
        GL.glUniform1f(u["u_lensFov1"], math.radians(st.fov_back))
        GL.glUniform1f(u["u_blend"], math.radians(st.blend))
        GL.glUniformMatrix3fv(u["u_globalRot"], 1, GL.GL_TRUE, flat(rot_matrix(st.g_yaw, st.g_pitch, st.g_roll)))
        GL.glUniformMatrix3fv(u["u_backRot"], 1, GL.GL_TRUE, flat(rot_matrix(st.back_yaw, st.back_pitch, st.back_roll)))
        GL.glUniform1i(u["u_stereo"], {"mono": 0, "tb": 1, "sbs": 2}.get(v.stereo, 0))
        GL.glUniform1i(u["u_hq"], 1 if v.hq else 0)
        GL.glUniform1i(u["u_flipY"], self._flip_y)
        GL.glBindVertexArray(self._vao)
        GL.glDrawArrays(GL.GL_TRIANGLES, 0, 3)
        GL.glBindVertexArray(0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
        GL.glUseProgram(0)
        key = round(mag, 2)
        if emit and key != self._last_mag_key:
            self._last_mag_key = key
            self.vr_changed.emit(v)

    def shutdown(self) -> None:
        """종료 전에 반드시 호출: 렌더 컨텍스트를 GL 컨텍스트가 살아있는 동안 해제한다."""
        if self.ctx is not None:
            try:
                self.makeCurrent()
                self.ctx.update_cb = None
                self.ctx.free()
                self._release_vr_target()
                if self._vao:
                    GL.glDeleteVertexArrays(1, [self._vao])
                if self._prog:
                    GL.glDeleteProgram(self._prog)
            except Exception:  # noqa: BLE001
                pass
            finally:
                self.ctx = None
                try:
                    self.doneCurrent()
                except Exception:  # noqa: BLE001
                    pass
        try:
            self.mpv.terminate()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ 입력
    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._press_pos = e.position()
            self._drag_last = e.position()
            self._dragged = False
            if self.vr_enabled:
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        self.mouse_moved.emit()
        if self._press_pos is not None and (e.buttons() & Qt.MouseButton.LeftButton):
            p = e.position()
            if (p - self._press_pos).manhattanLength() > DRAG_THRESHOLD_PX:
                self._dragged = True
            if self.vr_enabled and self._drag_last is not None:
                dx = p.x() - self._drag_last.x()
                dy = p.y() - self._drag_last.y()
                k = self.vr.fov / max(1.0, float(self.width()))
                self.vr.rotate(-dx * k, dy * k)
                self._drag_last = p
                self.update()
                self.vr_changed.emit(self.vr)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            if self.vr_enabled:
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            if self._press_pos is not None and not self._dragged:
                self.clicked.emit()
            self._press_pos = None
            self._drag_last = None
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit()
        super().mouseDoubleClickEvent(e)

    def wheelEvent(self, e: QWheelEvent) -> None:
        pd, ad = e.pixelDelta(), e.angleDelta()
        steps = (pd.y() / 40.0) if not pd.isNull() else (ad.y() / 120.0)
        if steps == 0:
            return
        if self.vr_enabled:
            self.vr_zoom(math.pow(0.88, steps))
        else:
            self.wheel_scrolled.emit(1 if steps > 0 else -1)
        e.accept()

    def event(self, e: QEvent) -> bool:
        if e.type() == QEvent.Type.NativeGesture and self.vr_enabled:
            ge: QNativeGestureEvent = e  # type: ignore[assignment]
            if ge.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
                self.vr_zoom(1.0 / (1.0 + ge.value()))
                return True
        return super().event(e)
