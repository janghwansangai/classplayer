"""음악(소리) 재생 화면.

음악 파일을 열거나 '소리만 재생'을 켜면 영상 자리 대신 이 화면을 보여 준다.
앨범 표지(없으면 앰버 그라데이션)를 흐리게 깐 바탕 위에 표지 카드·돌아가는 레코드판·곡 정보·막대 그래프.
막대 높이는 mpv 의 실제 소리 크기(astats RMS)를 따라 움직인다 (레벨을 못 받으면 부드럽게 흔들리기만).
QOpenGLWidget 위에 겹치면 맥에서 흐리게 합성되므로 영상 위젯과 같은 자리에 '바꿔 끼우는' 페이지로 쓴다.
"""
from __future__ import annotations

import math
import time
from typing import Callable

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QFont, QFontMetrics, QLinearGradient, QPainter,
                           QPainterPath, QPen, QPixmap, QRadialGradient)
from PySide6.QtWidgets import QWidget

from classplayer.ui import icons
from classplayer.ui.theme import AMBER, AMBER_DIM, AMBER_HOVER, BG, MUTED, TEXT

BARS = 32


class MusicView(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setMinimumSize(320, 180)
        self.setMouseTracking(True)
        self._title = ""
        self._artist = ""
        self._kind = "음악 재생"
        self._cover: QPixmap | None = None
        self._backdrop: QPixmap | None = None
        self._backdrop_key = None
        self._playing = False
        self._level_source: Callable[[], float | None] | None = None
        self._levels = [0.08] * BARS
        self._env = 0.0                # 소리 크기 (0~1, 부드럽게)
        self._angle = 0.0              # 레코드판 회전 각도
        self._t0 = time.monotonic()
        self._last = self._t0
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)

    # ------------------------------------------------------------------ 바깥에서 부르는 것
    def set_track(self, title: str, artist: str = "", kind: str = "음악 재생") -> None:
        self._title, self._artist, self._kind = title, artist, kind
        self.update()

    def set_cover(self, pm: QPixmap | None) -> None:
        self._cover = pm if (pm is not None and not pm.isNull()) else None
        self._backdrop = None
        self._backdrop_key = None
        self.update()

    def set_playing(self, playing: bool) -> None:
        self._playing = playing
        self._ensure_timer()

    def set_level_source(self, fn: Callable[[], float | None] | None) -> None:
        """현재 소리 크기(dB, RMS)를 돌려주는 함수. None 이면 실제 레벨 없이 흔들리기만."""
        self._level_source = fn

    # ------------------------------------------------------------------ 애니메이션
    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        self._last = time.monotonic()
        self._ensure_timer()

    def hideEvent(self, e) -> None:  # noqa: N802
        self._timer.stop()
        super().hideEvent(e)

    def _ensure_timer(self) -> None:
        if self.isVisible() and (self._playing or max(self._levels) > 0.09 or self._env > 0.01):
            if not self._timer.isActive():
                self._last = time.monotonic()
                self._timer.start()

    def _read_level(self) -> float | None:
        if not self._level_source:
            return None
        try:
            db = self._level_source()
        except Exception:  # noqa: BLE001
            return None
        if db is None:
            return None
        return max(0.0, min(1.0, (db + 48.0) / 40.0))    # -48dB → 0, -8dB → 1

    def _tick(self) -> None:
        now = time.monotonic()
        dt = min(0.1, now - self._last)
        self._last = now
        t = now - self._t0
        if self._playing:
            lv = self._read_level()
            target = lv if lv is not None else 0.55 + 0.15 * math.sin(t * 2.1)
            self._angle = (self._angle + dt * 200.0) % 360.0      # 33⅓ rpm 쯤
        else:
            target = 0.0
        k = 1 - math.exp(-dt * (14.0 if target > self._env else 5.0))
        self._env += (target - self._env) * k
        for i in range(BARS):
            x = i / (BARS - 1)
            # 낮은 음(왼쪽)이 더 크게, 막대마다 다른 박자로 흔들림
            shape = 0.55 + 0.45 * math.cos(x * math.pi * 0.9)
            wob = (0.5 + 0.5 * math.sin(t * (3.1 + 1.7 * math.sin(i * 1.3)) + i * 0.9)) * \
                  (0.6 + 0.4 * math.sin(t * (1.3 + 0.4 * i % 3) + i * 2.1))
            goal = 0.08 + 0.92 * self._env * shape * (0.35 + 0.65 * abs(wob))
            cur = self._levels[i]
            self._levels[i] = cur + (goal - cur) * (1 - math.exp(-dt * (22.0 if goal > cur else 7.0)))
        self.update()
        if not self._playing and self._env < 0.01 and max(self._levels) < 0.09:
            self._timer.stop()

    # ------------------------------------------------------------------ 그리기
    def _make_backdrop(self) -> QPixmap | None:
        key = (self.width(), self.height(), id(self._cover))
        if self._backdrop is not None and self._backdrop_key == key:
            return self._backdrop
        if self._cover is None:
            return None
        # 아주 작게 줄였다가 부드럽게 키우면 값싼 흐림 효과가 된다
        tiny = self._cover.scaled(18, 18, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                  Qt.TransformationMode.SmoothTransformation)
        big = tiny.scaled(max(1, self.width()), max(1, self.height()), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                          Qt.TransformationMode.SmoothTransformation)
        self._backdrop, self._backdrop_key = big, key
        return big

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        w, h = self.width(), self.height()

        # 바탕: 표지를 흐리게 + 어둡게, 없으면 앰버 기운의 어두운 그라데이션
        bd = self._make_backdrop()
        if bd is not None:
            p.drawPixmap((w - bd.width()) // 2, (h - bd.height()) // 2, bd)
            p.fillRect(self.rect(), QColor(10, 10, 12, 190))
        else:
            g = QLinearGradient(0, 0, w, h)
            g.setColorAt(0, QColor("#22190a"))
            g.setColorAt(0.55, QColor(BG))
            g.setColorAt(1, QColor("#0c0d10"))
            p.fillRect(self.rect(), g)
        vg = QRadialGradient(QPointF(w / 2, h / 2), max(w, h) * 0.75)
        vg.setColorAt(0.0, QColor(0, 0, 0, 0))
        vg.setColorAt(1.0, QColor(0, 0, 0, 170))
        p.fillRect(self.rect(), vg)

        wide = w >= h * 1.25
        s = min(h * (0.50 if wide else 0.38), w * (0.34 if wide else 0.55))
        s = max(90.0, s)
        disc_shift = s * 0.36
        art_w = s + disc_shift
        if wide:
            text_w = min(520.0, w - art_w - 120)
            total = art_w + 56 + text_w
            ax = (w - total) / 2
            ay = (h - s) / 2
            tx, ty = ax + art_w + 56, ay + s * 0.06
        else:
            text_w = min(560.0, w - 48)
            block_h = s + 28 + 150
            ax = (w - art_w) / 2
            ay = max(20.0, (h - block_h) / 2)
            tx, ty = (w - text_w) / 2, ay + s + 28

        self._draw_disc(p, QPointF(ax + s / 2 + disc_shift, ay + s / 2), s * 0.48)
        self._draw_cover(p, QRectF(ax, ay, s, s))
        self._draw_text(p, tx, ty, text_w, s, centered=not wide)
        p.end()

    def _draw_cover(self, p: QPainter, r: QRectF) -> None:
        rad = r.width() * 0.06
        for i in range(10, 0, -1):     # 부드러운 그림자
            sh = r.adjusted(-i * 1.2, -i * 0.6, i * 1.2, i * 2.2)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 9))
            p.drawRoundedRect(sh, rad + i, rad + i)
        path = QPainterPath()
        path.addRoundedRect(r, rad, rad)
        p.save()
        p.setClipPath(path)
        if self._cover is not None:
            pm = self._cover.scaled(int(r.width() * 2), int(r.height() * 2), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                    Qt.TransformationMode.SmoothTransformation)
            pm.setDevicePixelRatio(2.0)
            p.drawPixmap(QPointF(r.x() - (pm.width() / 2 - r.width()) / 2, r.y() - (pm.height() / 2 - r.height()) / 2), pm)
        else:
            g = QLinearGradient(r.topLeft(), r.bottomRight())
            g.setColorAt(0, QColor("#f59e0b"))
            g.setColorAt(0.55, QColor("#b45309"))
            g.setColorAt(1, QColor("#3b2f12"))
            p.fillRect(r, g)
            ic = icons.pixmap("music", "#1b1206", int(r.width() * 0.34), 2.0)
            p.drawPixmap(QPointF(r.center().x() - ic.width() / 4, r.center().y() - ic.height() / 4), ic)
        # 위쪽 빛 반사
        gl = QLinearGradient(r.topLeft(), QPointF(r.left(), r.bottom()))
        gl.setColorAt(0, QColor(255, 255, 255, 34))
        gl.setColorAt(0.4, QColor(255, 255, 255, 0))
        p.fillRect(r, gl)
        p.restore()
        p.setPen(QPen(QColor(255, 255, 255, 28), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r, rad, rad)

    def _draw_disc(self, p: QPainter, c: QPointF, rad: float) -> None:
        p.save()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 90))
        p.drawEllipse(c + QPointF(0, rad * 0.04), rad * 1.02, rad * 1.02)
        body = QRadialGradient(c, rad)
        body.setColorAt(0, QColor("#1c1c20"))
        body.setColorAt(1, QColor("#0a0a0c"))
        p.setBrush(body)
        p.drawEllipse(c, rad, rad)
        # 홈(동심원)
        p.setBrush(Qt.BrushStyle.NoBrush)
        n = 14
        for i in range(n):
            rr = rad * (0.40 + 0.57 * i / n)
            p.setPen(QPen(QColor(255, 255, 255, 10 if i % 2 else 16), 1))
            p.drawEllipse(c, rr, rr)
        # 돌아가는 빛줄기
        sweep = QConicalGradient(c, -self._angle)
        sweep.setColorAt(0.00, QColor(255, 255, 255, 0))
        sweep.setColorAt(0.06, QColor(255, 220, 160, 38))
        sweep.setColorAt(0.12, QColor(255, 255, 255, 0))
        sweep.setColorAt(0.50, QColor(255, 255, 255, 0))
        sweep.setColorAt(0.56, QColor(255, 220, 160, 26))
        sweep.setColorAt(0.62, QColor(255, 255, 255, 0))
        sweep.setColorAt(1.00, QColor(255, 255, 255, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(sweep))
        p.drawEllipse(c, rad * 0.97, rad * 0.97)
        # 가운데 라벨 (표지를 둥글게, 같이 돈다)
        lr = rad * 0.36
        p.translate(c)
        p.rotate(self._angle)
        lab = QPainterPath()
        lab.addEllipse(QPointF(0, 0), lr, lr)
        p.setClipPath(lab)
        if self._cover is not None:
            pm = self._cover.scaled(int(lr * 4), int(lr * 4), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                    Qt.TransformationMode.SmoothTransformation)
            pm.setDevicePixelRatio(2.0)
            p.drawPixmap(QPointF(-pm.width() / 4, -pm.height() / 4), pm)
        else:
            lg = QRadialGradient(QPointF(0, 0), lr)
            lg.setColorAt(0, QColor(AMBER_HOVER))
            lg.setColorAt(1, QColor("#b45309"))
            p.fillRect(QRectF(-lr, -lr, lr * 2, lr * 2), lg)
            p.setPen(QPen(QColor(27, 18, 6, 120), max(1.0, lr * 0.05)))
            p.drawLine(QPointF(-lr * 0.55, 0), QPointF(-lr * 0.2, 0))
        p.setClipping(False)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#0b0b0d"))
        p.drawEllipse(QPointF(0, 0), lr * 0.12, lr * 0.12)
        p.restore()

    def _draw_text(self, p: QPainter, x: float, y: float, w: float, s: float, centered: bool) -> None:
        align = Qt.AlignmentFlag.AlignHCenter if centered else Qt.AlignmentFlag.AlignLeft
        # 알약: ♪ 음악 재생
        f = QFont()
        f.setPixelSize(12)
        f.setBold(True)
        p.setFont(f)
        fm = QFontMetrics(f)
        label = self._kind
        pw = fm.horizontalAdvance(label) + 34
        px = x + (w - pw) / 2 if centered else x
        pill = QRectF(px, y, pw, 24)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(245, 158, 11, 40))
        p.drawRoundedRect(pill, 12, 12)
        ic = icons.pixmap("music", AMBER, 13, 2.0)
        p.drawPixmap(QPointF(pill.x() + 10, pill.y() + 5.5), ic)
        p.setPen(QColor(AMBER))
        p.drawText(pill.adjusted(26, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, label)
        y += 24 + 16

        # 제목 (두 줄까지)
        tf = QFont()
        tf.setPixelSize(int(max(20, min(34, s * 0.11))))
        tf.setBold(True)
        p.setFont(tf)
        tfm = QFontMetrics(tf)
        title = self._title or "제목 없음"
        lines = self._wrap(title, tfm, w, 2)
        p.setPen(QColor(TEXT))
        for ln in lines:
            p.drawText(QRectF(x, y, w, tfm.height()), align | Qt.AlignmentFlag.AlignVCenter, ln)
            y += tfm.height()
        y += 6
        if self._artist:
            af = QFont()
            af.setPixelSize(int(max(13, min(18, s * 0.055))))
            p.setFont(af)
            afm = QFontMetrics(af)
            p.setPen(QColor(MUTED))
            p.drawText(QRectF(x, y, w, afm.height()), align | Qt.AlignmentFlag.AlignVCenter,
                       afm.elidedText(self._artist, Qt.TextElideMode.ElideRight, int(w)))
            y += afm.height()
        y += 22

        # 막대 그래프
        bh = max(40.0, min(110.0, s * 0.30))
        bw_total = min(w, 420.0)
        bx = x + (w - bw_total) / 2 if centered else x
        gap = 4.0
        bw = (bw_total - gap * (BARS - 1)) / BARS
        grad = QLinearGradient(0, y + bh, 0, y)
        grad.setColorAt(0, QColor(AMBER))
        grad.setColorAt(1, QColor(AMBER_HOVER))
        for i, lv in enumerate(self._levels):
            hh = max(3.0, bh * lv)
            r = QRectF(bx + i * (bw + gap), y + bh - hh, bw, hh)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(grad))
            p.drawRoundedRect(r, bw / 2, bw / 2)
            # 바닥 반사
            rr = QRectF(r.x(), y + bh + 4, bw, hh * 0.28)
            p.setBrush(QColor(245, 158, 11, 30))
            p.drawRoundedRect(rr, bw / 2, bw / 2)
        if not self._playing:
            p.setPen(QColor(MUTED))
            f2 = QFont()
            f2.setPixelSize(12)
            p.setFont(f2)
            p.drawText(QRectF(bx, y + bh + 14, bw_total, 18), Qt.AlignmentFlag.AlignHCenter, "일시 정지")
        _ = AMBER_DIM

    @staticmethod
    def _wrap(text: str, fm: QFontMetrics, width: float, max_lines: int) -> list[str]:
        out: list[str] = []
        rest = text
        while rest and len(out) < max_lines:
            if fm.horizontalAdvance(rest) <= width or len(out) == max_lines - 1:
                out.append(fm.elidedText(rest, Qt.TextElideMode.ElideRight, int(width)))
                break
            cut = len(rest)
            while cut > 1 and fm.horizontalAdvance(rest[:cut]) > width:
                cut -= 1
            sp = rest.rfind(" ", 0, cut)
            if sp > cut * 0.5:
                cut = sp
            out.append(rest[:cut].rstrip())
            rest = rest[cut:].lstrip()
        return out
