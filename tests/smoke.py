"""앱을 실제로 띄워 영상을 재생하고 화면을 캡처한다.

사용법: python tests/smoke.py <영상> <출력폴더> [projection: equirect|eac] [stereo: mono|tb|sbs]
"""
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from classplayer import bootstrap  # noqa: E402

bootstrap.setup_library_paths()
from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtGui import QSurfaceFormat  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from classplayer.app import MainWindow  # noqa: E402

video = sys.argv[1]
out = Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
projection = sys.argv[3] if len(sys.argv) > 3 else "equirect"
stereo = sys.argv[4] if len(sys.argv) > 4 else "mono"

fmt = QSurfaceFormat()
fmt.setVersion(3, 3)
fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
QSurfaceFormat.setDefaultFormat(fmt)
QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
app = QApplication(sys.argv)
win = MainWindow()
win.resize(1280, 800)
win.show()
win.open_target(video)
P = win.player
results = {}


def grab(name):
    img = P.grabFramebuffer()
    path = out / f"{name}.png"
    img.save(str(path))
    print(f"grab {name}: {img.width()}x{img.height()} -> {path}")


def report():
    for k in ("hwdec-current", "video-codec", "video-params/w", "video-params/h", "container-fps",
              "estimated-vf-fps", "frame-drop-count", "vo-delayed-frame-count", "time-pos", "pause"):
        results[k] = P.property(k)
    print("REPORT", results)
    print("VR", P.vr.describe(), "enabled=", P.vr_enabled)


steps = [
    (3000, lambda: grab("01_normal")),
    (100, lambda: win.set_vr(True, projection=projection, stereo=stereo)),
    (900, lambda: grab("02_vr_front")),
    (50, lambda: P.vr_rotate_deg(90, 0)),
    (500, lambda: grab("03_vr_right")),
    (50, lambda: (P.vr_reset(), P.vr_rotate_deg(-90, 0))),
    (500, lambda: grab("04_vr_left")),
    (50, lambda: (P.vr_reset(), P.vr_rotate_deg(180, 0))),
    (500, lambda: grab("05_vr_back")),
    (50, lambda: (P.vr_reset(), P.vr_rotate_deg(0, 70))),
    (500, lambda: grab("06_vr_up")),
    (50, lambda: (P.vr_reset(), P.vr_rotate_deg(0, -70))),
    (500, lambda: grab("07_vr_down")),
    (50, lambda: (P.vr_reset(), P.vr_zoom(0.35))),
    (500, lambda: grab("08_vr_zoom")),
    (50, report),
    (200, lambda: (win.close(), app.quit())),
]


def run_steps(i=0):
    if i >= len(steps):
        return
    delay, fn = steps[i]

    def go():
        try:
            fn()
        except Exception:
            traceback.print_exc()
        run_steps(i + 1)

    QTimer.singleShot(delay, go)


run_steps()
QTimer.singleShot(30000, lambda: (print("TIMEOUT"), app.quit()))
sys.exit(app.exec())
