"""icon.svg → icon.png(앱 내 표시), icon.icns(맥), icon.ico(윈도우)."""
import os, shutil, subprocess, sys, tempfile
from pathlib import Path
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parent.parent
svg = ROOT / "packaging" / "icon.svg"
app = QGuiApplication([])
r = QSvgRenderer(str(svg))

def render(size: int) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img); r.render(p); p.end()
    return img

render(512).save(str(ROOT / "src/classplayer/assets/icon.png"))
# ICO (윈도우): 여러 크기를 담은 PNG 기반 ico
sizes = [16, 32, 48, 64, 128, 256]
import struct, io
entries, data = [], b""
offset = 6 + 16 * len(sizes)
for s in sizes:
    buf = io.BytesIO()
    from PySide6.QtCore import QBuffer, QIODevice
    qb = QBuffer(); qb.open(QIODevice.OpenModeFlag.WriteOnly)
    render(s).save(qb, "PNG"); png = bytes(qb.data())
    entries.append(struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(png), offset + len(data)))
    data += png
(ROOT / "packaging/icon.ico").write_bytes(struct.pack("<HHH", 0, 1, len(sizes)) + b"".join(entries) + data)
# ICNS (맥): iconutil
if sys.platform == "darwin" and shutil.which("iconutil"):
    with tempfile.TemporaryDirectory() as td:
        iconset = Path(td) / "icon.iconset"; iconset.mkdir()
        for s in (16, 32, 128, 256, 512):
            render(s).save(str(iconset / f"icon_{s}x{s}.png"))
            render(s * 2).save(str(iconset / f"icon_{s}x{s}@2x.png"))
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(ROOT / "packaging/icon.icns")], check=True)
print("icons written")
