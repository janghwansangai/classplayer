"""일반 모드 재생 성능 확인: N초 재생 후 드롭 프레임 수와 디코더를 보고한다."""
import os
import sys
os.environ.setdefault("CLASSPLAYER_TEST_SETTINGS", "1")
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from classplayer import bootstrap  # noqa: E402

bootstrap.setup_library_paths()
from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtGui import QSurfaceFormat  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from classplayer.app import MainWindow  # noqa: E402

video, out = sys.argv[1], Path(sys.argv[2])
secs = float(sys.argv[3]) if len(sys.argv) > 3 else 6.0
vr_mode = sys.argv[4] if len(sys.argv) > 4 else ""
out.mkdir(parents=True, exist_ok=True)
fmt = QSurfaceFormat()
fmt.setVersion(3, 3)
fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
QSurfaceFormat.setDefaultFormat(fmt)
QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
app = QApplication(sys.argv)
win = MainWindow()
win.resize(1280, 800)
win.show()
import os  # noqa: E402
if os.environ.get("CLASSPLAYER_TEST_MAX_HEIGHT"):
    win.resolver.max_height = int(os.environ["CLASSPLAYER_TEST_MAX_HEIGHT"])
# 시험용 mpv 옵션 덮어쓰기: CLASSPLAYER_TEST_MPV="hwdec=no;audio-buffer=0.5"
for kv in filter(None, os.environ.get("CLASSPLAYER_TEST_MPV", "").split(";")):
    k, v = kv.split("=", 1)
    win.player.mpv[k] = v
win.open_target(video)
P = win.player
_mid = {}


def sample_mid():
    _mid["drops"] = P.property("frame-drop-count", 0)
    _mid["pos"] = P.property("time-pos", 0.0)


QTimer.singleShot(int(secs * 500), sample_mid)
if os.environ.get("CLASSPLAYER_TEST_TRACE"):
    # 1초마다 위치·버린 프레임·실행 중인 ffmpeg 수 (어느 순간에 끊기는지)
    import subprocess as _sp

    def _trace():
        try:
            n = len([ln for ln in _sp.run(["ps", "-axo", "comm"], capture_output=True, text=True).stdout.splitlines()
                     if ln.strip().endswith("ffmpeg") or ln.strip().endswith("ffprobe")])
        except Exception:  # noqa: BLE001
            n = -1
        print(f"TRACE t={P.property('time-pos', 0) or 0:6.2f} drop={P.property('frame-drop-count', 0)} "
              f"delayed={P.property('vo-delayed-frame-count', 0)} ffmpeg={n}", flush=True)
    _tt = QTimer()
    _tt.timeout.connect(_trace)
    _tt.start(1000)
if vr_mode:
    QTimer.singleShot(1500, lambda: win.set_vr(True, projection=vr_mode))


def finish():
    keys = ("hwdec-current", "video-codec", "video-params/w", "video-params/h", "container-fps", "estimated-vf-fps",
            "frame-drop-count", "decoder-frame-drop-count", "vo-delayed-frame-count", "time-pos", "demuxer-cache-duration",
            "hwdec", "audio-buffer", "vd-lavc-dr", "current-ao")
    print("PERF", {k: P.property(k) for k in keys})
    if _mid:
        d = (P.property("frame-drop-count", 0) or 0) - (_mid["drops"] or 0)
        span = (P.property("time-pos", 0.0) or 0.0) - (_mid["pos"] or 0.0)
        print(f"STEADY 후반 {span:.1f}초 동안 드롭 {d}프레임")
    print("VR enabled:", P.vr_enabled, "| GPU:", P.gl_renderer, "| hw 오류:", P.hwdec_errors,
          "| 미리보기:", win.thumbs.note or ("있음" if win.thumbs.has_source() else "없음"))
    # 화면 캡처는 숫자를 읽은 뒤에 (4K 캡처가 재생을 잠깐 멈춰 버린 프레임으로 잡히던 문제)
    P.grabFramebuffer().save(str(out / "perf_frame.png"))
    win.close()
    app.quit()


QTimer.singleShot(int(secs * 1000), finish)
QTimer.singleShot(int(secs * 1000) + 15000, app.quit)
sys.exit(app.exec())
