"""시안 C 아이콘: 선 굵기 1.9의 단순한 SVG 아이콘을 원하는 색으로 그려 QIcon 으로 만든다."""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

# viewBox 0 0 24 24, stroke="currentColor" 자리에 색을 넣는다
_PATHS: dict[str, str] = {
    "play": '<path d="M7 4.5l12.5 7.5L7 19.5z" fill="C" stroke="none"/>',
    "pause": '<path d="M7 5h3.2v14H7zM13.8 5H17v14h-3.2z" fill="C" stroke="none"/>',
    "back10": '<path d="M3 12a9 9 0 1 0 3-6.7M3 4v5h5"/><text x="12" y="15.6" font-size="7.5" font-weight="700" '
              'text-anchor="middle" fill="C" stroke="none" font-family="Helvetica">10</text>',
    "fwd10": '<path d="M21 12a9 9 0 1 1-3-6.7M21 4v5h-5"/><text x="12" y="15.6" font-size="7.5" font-weight="700" '
             'text-anchor="middle" fill="C" stroke="none" font-family="Helvetica">10</text>',
    "volume": '<path d="M4 10v4h4l5 4V6l-5 4z" fill="C" stroke-linejoin="round"/><path d="M16 9a4 4 0 0 1 0 6M18.5 6.5a8 8 0 0 1 0 11"/>',
    "mute": '<path d="M4 10v4h4l5 4V6l-5 4z" fill="C" stroke-linejoin="round"/><path d="M16.5 9.5l5 5M21.5 9.5l-5 5"/>',
    "subs": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7 12h4M7 15h7M13 12h4"/>',
    "camera": '<path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/>',
    "globe": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18"/>',
    "reset": '<path d="M3 12a9 9 0 1 0 3-6.7M3 4v5h5"/>',
    "full": '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
    "exitfull": '<path d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5"/>',
    "record": '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.2" fill="#ef4444" stroke="none"/>',
    "audio": '<path d="M4 14v-4a8 8 0 0 1 16 0v4"/><rect x="3" y="14" width="4" height="6" rx="1"/><rect x="17" y="14" width="4" height="6" rx="1"/>',
    "link": '<path d="M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1"/><path d="M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1"/>',
    "download": '<path d="M12 3v12m0 0l-4-4m4 4l4-4M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/>',
    "folder": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "minus": '<path d="M5 12h14"/>',
    "refresh": '<path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/>',
    "music": '<path d="M9 18V5l11-2v13"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="17.5" cy="16" r="2.5"/>',
    "scissors": '<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M8.1 7.9L20 20M8.1 16.1L20 4"/>',
}


def svg(name: str, color: str = "#d9dbe3") -> bytes:
    body = _PATHS[name].replace('"C"', f'"{color}"')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
            f'stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">{body}</svg>').encode()


@lru_cache(maxsize=256)
def pixmap(name: str, color: str = "#d9dbe3", size: int = 20, dpr: float = 2.0) -> QPixmap:
    px = int(size * dpr)
    pm = QPixmap(px, px)
    pm.fill(Qt.GlobalColor.transparent)
    r = QSvgRenderer(QByteArray(svg(name, color)))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r.render(p, QRectF(0, 0, px, px))
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def icon(name: str, color: str = "#d9dbe3", active: str | None = None, size: int = 20) -> QIcon:
    """기본 색, 눌린(checked) 상태 색을 함께 담은 아이콘."""
    ic = QIcon()
    ic.addPixmap(pixmap(name, color, size), QIcon.Mode.Normal, QIcon.State.Off)
    ic.addPixmap(pixmap(name, active or color, size), QIcon.Mode.Normal, QIcon.State.On)
    ic.addPixmap(pixmap(name, "#5b5e69", size), QIcon.Mode.Disabled, QIcon.State.Off)
    return ic
