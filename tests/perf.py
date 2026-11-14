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
win.open_target(video)
P = win.player
_mid = {}


def sample_mid():
    _mid["drops"] = P.property("frame-drop-count", 0)
    _mid["pos"] = P.property("time-pos", 0.0)


QTimer.singleShot(int(secs * 500), sample_mid)
if vr_mode:
    QTimer.singleShot(1500, lambda: win.set_vr(True, projection=vr_mode))


def finish():
    P.grabFramebuffer().save(str(out / "perf_frame.png"))
    keys = ("hwdec-current", "video-codec", "video-params/w", "video-params/h", "container-fps", "estimated-vf-fps",
            "frame-drop-count", "decoder-frame-drop-count", "vo-delayed-frame-count", "time-pos", "demuxer-cache-duration")
    print("PERF", {k: P.property(k) for k in keys})
    if _mid:
        d = (P.property("frame-drop-count", 0) or 0) - (_mid["drops"] or 0)
        span = (P.property("time-pos", 0.0) or 0.0) - (_mid["pos"] or 0.0)
        print(f"STEADY 후반 {span:.1f}초 동안 드롭 {d}프레임")
    print("VR enabled:", P.vr_enabled)
    win.close()
    app.quit()


QTimer.singleShot(int(secs * 1000), finish)
QTimer.singleShot(int(secs * 1000) + 15000, app.quit)
sys.exit(app.exec())
