"""목록에서 '지금 재생 중'인 줄 그리기 (시안 A).

앱 QSS 의 ::item 규칙(둥근 모서리)이 줄마다 준 배경색을 무시해서, 위임자(delegate)가 직접
옅은 배경 + 왼쪽 색 막대를 그린다. 색은 줄의 PLAY_ROLE 값: 재생목록 앰버, 라이브러리 파랑.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

PLAY_ROLE = Qt.ItemDataRole.UserRole + 7
PLAYLIST_COLOR = "#f59e0b"
LIBRARY_COLOR = "#60a5fa"


class NowPlayingDelegate(QStyledItemDelegate):
    def paint(self, p: QPainter, opt, idx) -> None:
        color = idx.data(PLAY_ROLE)
        if color:
            c = QColor(str(color))
            bg = QColor(c)
            bg.setAlpha(44)
            r = QRectF(opt.rect).adjusted(1, 2, -1, -2)
            p.save()
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(r, 10, 10)
            p.setBrush(c)
            p.drawRoundedRect(QRectF(r.left() + 1, r.top() + 6, 3.5, r.height() - 12), 2, 2)
            p.restore()
            # 선택된 줄이어도 선택 색(회색·흰 글자)이 재생 중 표시를 덮지 않게
            opt = QStyleOptionViewItem(opt)
            opt.state &= ~QStyle.StateFlag.State_Selected
        super().paint(p, opt, idx)
