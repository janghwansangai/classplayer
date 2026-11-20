"""목록에서 '지금 재생 중'인 줄 그리기 (시안 A).

앱 QSS 의 ::item 규칙(둥근 모서리)이 줄마다 준 배경색을 무시해서, 위임자(delegate)가 직접
옅은 배경 + 왼쪽 색 막대를 그린다. 색은 줄의 PLAY_ROLE 값: 재생목록 앰버, 라이브러리 파랑.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFontMetricsF, QPainter, QTextLayout, QTextOption
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
        text = idx.data(Qt.ItemDataRole.DisplayRole)
        if not isinstance(text, str) or "\n" not in text:
            super().paint(p, opt, idx)
            return
        # '제목\n폴더 · 크기' 두 부분: 각각 최대 2줄로 따로 자리를 잡아, 제목이 길어도 폴더 이름이 밀려나지 않게
        # (2026-11-20 사용자: 폴더명이 길면 잘려서 안 보임)
        o = QStyleOptionViewItem(opt)
        self.initStyleOption(o, idx)
        o.text = ""
        from PySide6.QtWidgets import QApplication
        style = o.widget.style() if o.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, o, p, o.widget)
        o.text = text
        r = QRectF(style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, o, o.widget)).adjusted(2, 0, -2, 0)
        title, meta = text.split("\n", 1)
        fm = QFontMetricsF(o.font)
        w = max(10.0, r.width())
        lh = fm.lineSpacing()
        room = max(2, int(r.height() // lh))                  # 칸 높이에 들어가는 줄 수 (보통 3)
        t_lines = _wrap(title, o.font, fm, w, min(2, room - 1))
        m_lines = _wrap(meta.replace("\n", " "), o.font, fm, w, max(1, min(2, room - len(t_lines))))
        total = lh * (len(t_lines) + len(m_lines))
        y = r.top() + max(0.0, (r.height() - total) / 2) + fm.ascent()
        sel = bool(o.state & QStyle.StateFlag.State_Selected)
        main = o.palette.highlightedText().color() if sel else o.palette.text().color()
        p.save()
        p.setFont(o.font)
        p.setClipRect(r)
        p.setPen(main)
        for ln in t_lines:
            p.drawText(QPointF(r.left(), y), ln)
            y += lh
        p.setPen(main if sel else QColor("#a7aab4"))
        for ln in m_lines:
            p.drawText(QPointF(r.left(), y), ln)
            y += lh
        p.restore()


def _wrap(text: str, font, fm: QFontMetricsF, width: float, max_lines: int) -> list[str]:
    """글을 폭에 맞춰 줄바꿈하고 max_lines 줄을 넘으면 마지막 줄을 … 로 줄인다."""
    layout = QTextLayout(text, font)
    to = QTextOption()
    to.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    layout.setTextOption(to)
    layout.beginLayout()
    lines: list[tuple[int, int]] = []
    while True:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(width)
        lines.append((line.textStart(), line.textLength()))
    layout.endLayout()
    out = [text[a:a + n].rstrip() for a, n in lines]
    if len(out) > max_lines:
        rest = text[lines[max_lines - 1][0]:].strip()
        out = out[:max_lines - 1] + [fm.elidedText(rest, Qt.TextElideMode.ElideRight, width)]
    return out or [""]
