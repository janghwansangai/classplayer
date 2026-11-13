"""라벨이 붙은 등장방형(2:1) 테스트 이미지를 Qt로 그린다. drawtext 없는 ffmpeg용."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPen  # noqa: E402

W, H = 4096, 2048
out = sys.argv[1] if len(sys.argv) > 1 else "equirect_labels.png"
app = QGuiApplication([])
img = QImage(W, H, QImage.Format.Format_RGB32)
p = QPainter(img)
# 경도 띠: BACK(빨강) | LEFT(초록) | FRONT(파랑) | RIGHT(노랑) | BACK(빨강)
bands = [(0, 512, "#a02020"), (512, 1536, "#208020"), (1536, 2560, "#2040a0"), (2560, 3584, "#c0a020"), (3584, 4096, "#a02020")]
for x0, x1, c in bands:
    p.fillRect(x0, 0, x1 - x0, H, QColor(c))
p.fillRect(0, 0, W, 300, QColor("#e0e0e0"))        # 위(하늘) 밝게
p.fillRect(0, H - 300, W, 300, QColor("#101010"))  # 아래(땅) 어둡게
p.setPen(QPen(QColor(255, 255, 255, 90), 2))
for x in range(0, W, 256):
    p.drawLine(x, 0, x, H)
for y in range(0, H, 256):
    p.drawLine(0, y, W, y)
p.setPen(QPen(QColor(255, 255, 255, 220), 6))
p.drawLine(W // 2, 0, W // 2, H)   # 정면 중앙선
p.drawLine(0, H // 2, W, H // 2)   # 수평선

def label(text, cx, cy, size, color="white"):
    f = QFont("Helvetica", size, QFont.Weight.Bold)
    p.setFont(f)
    p.setPen(QColor(color))
    p.drawText(QRectF(cx - 800, cy - 200, 1600, 400), Qt.AlignmentFlag.AlignCenter, text)

label("FRONT", 2048, 1024, 200)
label("RIGHT", 3072, 1024, 200)
label("LEFT", 1024, 1024, 200)
label("BACK", 3900, 1024, 160)
label("BACK", 200, 1024, 160)
label("TOP", 2048, 150, 140, "black")
label("BOTTOM", 2048, H - 150, 140)
# 모서리 화살표 역할: 정면 위쪽에 삼각형
p.setBrush(QColor("white"))
p.setPen(Qt.PenStyle.NoPen)
from PySide6.QtGui import QPolygonF  # noqa: E402
from PySide6.QtCore import QPointF  # noqa: E402
p.drawPolygon(QPolygonF([QPointF(2048, 420), QPointF(1948, 620), QPointF(2148, 620)]))
p.end()
img.save(out)
print("saved", out, W, H)
