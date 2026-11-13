"""libmpv를 OpenGL로 그리는 Qt 위젯. 일반 재생과 360도 모드를 모두 담당한다.

일반 모드: mpv가 위젯의 기본 프레임버퍼에 직접 그린다 (하드웨어 디코딩 프레임이 GPU 안에 머문다).
360도 모드: mpv가 원본 해상도 크기의 오프스크린 텍스처에 그리고, 우리 셰이더가 그 텍스처를
시선 방향에 따라 투영해 화면에 그린다. CPU로 프레임을 복사하지 않으므로 8K에서도 부담이 없다.
"""
from __future__ import annotations

import math
import os
import sys
from typing import Any

from PySide6.QtCore import QByteArray, QEvent, QPointF, Qt, Signal
from PySide6.QtGui import QMouseEvent, QNativeGestureEvent, QOpenGLContext, QSurfaceFormat, QWheelEvent
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from OpenGL import GL

from classplayer import bootstrap

bootstrap.setup_library_paths()
import mpv  # noqa: E402  (경로 설정 후에 import 해야 libmpv를 찾는다)

from classplayer.vr.shaders import FRAG_SRC, VERT_SRC  # noqa: E402
from classplayer.vr.view import VrView  # noqa: E402

DRAG_THRESHOLD_PX = 4


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
    eof_reached = Signal()
    hwdec_changed = Signal(str)
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
                 stream_max_height: int = 2160, vr_hq: bool = True, vr_max_magnification: float = 3.0):
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
            "hwdec": "auto-safe",
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
        script_opts = []
        if ytdl_path:
            script_opts.append(f"ytdl_hook-ytdl_path={ytdl_path}")
        if script_opts:
            opts["script_opts"] = ",".join(script_opts)
        if js_runtime:
            opts["ytdl_raw_options"] = f"js-runtimes={js_runtime}"

        self.mpv = mpv.MPV(log_handler=self._on_log, loglevel="warn", **opts)
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
        m.observe_property("hwdec-current", lambda _n, v: self.hwdec_changed.emit(str(v or "")))
        m.observe_property("video-codec", lambda _n, v: self.codec_changed.emit(str(v or "")))
        m.observe_property("volume", lambda _n, v: self.volume_changed.emit(float(v or 0.0)))
        m.observe_property("mute", lambda _n, v: self.mute_changed.emit(bool(v)))
        m.observe_property("eof-reached", lambda _n, v: self.eof_reached.emit() if v else None)
        m.observe_property("paused-for-cache", lambda _n, v: self.buffering_changed.emit(bool(v)))

        @m.event_callback("file-loaded")
        def _loaded(_event):
            try:
                self.file_loaded.emit(str(m.path or ""))
            except Exception:  # noqa: BLE001
                self.file_loaded.emit("")

    # ------------------------------------------------------------------ mpv 콜백
    def _on_log(self, level: str, component: str, message: str) -> None:
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

    def play(self, target: str) -> None:
        """로컬 파일이나 일반 URL 재생 (mpv 내장 ytdl 훅 사용)."""
        self._reset_stream_options()
        self.mpv.play(target)
        self.mpv.pause = False
        self.pause_changed.emit(False)

    def play_stream(self, video_url: str, audio_url: str = "", *, title: str = "", user_agent: str = "",
                    subtitles: list[tuple[str, str, str]] | None = None) -> None:
        """앱에서 직접 푼 스트림 주소 재생. 영상/소리가 따로 오면 audio-files 로 합친다."""
        self._reset_stream_options()
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

    def _reset_stream_options(self) -> None:
        self._pending_subs = []
        try:
            self.mpv["ytdl"] = True
            self.mpv["audio-files"] = []
            self.mpv["force-media-title"] = ""
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

    def seek_relative(self, seconds: float) -> None:
        self.mpv.command("seek", seconds, "relative")

    def seek_absolute(self, seconds: float, exact: bool = True) -> None:
        self.mpv.command("seek", seconds, "absolute+exact" if exact else "absolute+keyframes")

    def frame_step(self, back: bool = False) -> None:
        self.mpv.command("frame-back-step" if back else "frame-step")

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
        """화면에 보이는 그대로 저장한다 (360도 모드면 투영된 시점이 저장된다)."""
        img = self.grabFramebuffer()
        return (not img.isNull()) and img.save(path)

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
                     "u_projection", "u_stereo", "u_hq", "u_flipY", "u_fisheyeFov", "u_fisheyeSwap"):
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

    def _draw_vr(self, target: int, w: int, h: int, tw: int, th: int) -> None:
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
        GL.glUniform1f(u["u_fisheyeFov"], math.radians(v.fisheye_fov_deg))
        GL.glUniform1i(u["u_fisheyeSwap"], 1 if v.fisheye_swap else 0)
        GL.glUniform1i(u["u_stereo"], {"mono": 0, "tb": 1, "sbs": 2}.get(v.stereo, 0))
        GL.glUniform1i(u["u_hq"], 1 if v.hq else 0)
        GL.glUniform1i(u["u_flipY"], self._flip_y)
        GL.glBindVertexArray(self._vao)
        GL.glDrawArrays(GL.GL_TRIANGLES, 0, 3)
        GL.glBindVertexArray(0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
        GL.glUseProgram(0)
        key = round(mag, 2)
        if key != self._last_mag_key:
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
