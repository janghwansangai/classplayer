"""시안 C: 스튜디오 다크 테마. 짙은 차콜 바탕 + 호박색(앰버) 포인트.

영상이 화면에서 가장 밝은 요소가 되도록 주변을 어둡게 눌러, 교실 프로젝터에서도 영상에 집중되게 한다.
"""
from PySide6.QtGui import QColor, QPalette

AMBER = "#f59e0b"
AMBER_HOVER = "#fbbf24"
AMBER_DIM = "#3b2f12"
BG = "#141518"          # 창 바탕
PANEL = "#17181c"       # 라이브러리·컨트롤
TOP = "#1b1c21"         # 위쪽 막대
FIELD = "#101114"       # 입력칸
RAISED = "#26272e"      # 버튼
LINE = "#2a2b31"
LINE2 = "#3a3b44"
TEXT = "#e7e8ec"
MUTED = "#8b8e99"
ICON = "#d9dbe3"

# 하위 호환 (예전 코드가 쓰던 이름)
BLUE = AMBER
BLUE_DARK = AMBER_HOVER

QSS = f"""
QMainWindow, QWidget#central, QDialog, QMessageBox {{ background: {BG}; }}
QWidget {{ color: {TEXT}; font-size: 13px; }}
QToolTip {{ background: {FIELD}; color: {TEXT}; border: 1px solid {LINE2}; padding: 4px 8px; border-radius: 6px; }}
QMenuBar {{ background: {TOP}; color: {TEXT}; }}
QMenuBar::item:selected {{ background: {RAISED}; }}
QMenu {{ background: {TOP}; color: {TEXT}; border: 1px solid {LINE2}; padding: 4px; }}
QMenu::item {{ padding: 6px 22px 6px 22px; border-radius: 6px; }}
QMenu::item:selected {{ background: {AMBER_DIM}; color: {AMBER_HOVER}; }}
QMenu::separator {{ height: 1px; background: {LINE}; margin: 4px 8px; }}
QStatusBar {{ background: {TOP}; border-top: 1px solid {LINE}; color: {MUTED}; }}
QStatusBar QLabel {{ color: {MUTED}; }}
QLabel#muted {{ color: {MUTED}; }}

/* 상단 링크 줄 */
QWidget#urlbar {{ background: {TOP}; border-bottom: 1px solid {LINE}; }}
QLabel#brand {{ color: {TEXT}; font-weight: 700; font-size: 14px; }}
QLineEdit, QPlainTextEdit, QTextEdit, QTimeEdit, QSpinBox, QDoubleSpinBox {{
    background: {FIELD}; border: 1px solid {LINE}; border-radius: 9px; padding: 0 10px; min-height: 30px;
    color: {TEXT}; selection-background-color: {AMBER}; selection-color: #1b1206; }}
QPlainTextEdit, QTextEdit {{ padding: 6px; }}
QLineEdit:focus, QTimeEdit:focus, QSpinBox:focus {{ border-color: {AMBER}; }}
QLineEdit#url {{ border-radius: 10px; padding: 0 12px; min-height: 34px; }}
QPushButton {{ border: none; border-radius: 10px; min-height: 34px; padding: 0 14px; background: {RAISED};
               color: {TEXT}; font-weight: 600; }}
QPushButton:hover {{ background: #30313a; }}
QPushButton:pressed {{ background: {LINE2}; }}
QPushButton:disabled {{ color: #5b5e69; }}
QPushButton#primary {{ background: {AMBER}; color: #1b1206; }}
QPushButton#primary:hover {{ background: {AMBER_HOVER}; }}
QPushButton#danger {{ background: #ef4444; color: #ffffff; }}
QPushButton#chip {{ min-height: 24px; padding: 0 10px; border-radius: 12px; font-size: 11.5px; font-weight: 600;
                    background: {RAISED}; color: #c3c5cd; }}
QPushButton#chip:checked {{ background: {AMBER_DIM}; color: {AMBER_HOVER}; }}
QLabel#logo {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {AMBER}, stop:1 #f97316);
               border-radius: 8px; min-width: 26px; max-width: 26px; min-height: 26px; max-height: 26px; }}
QCheckBox, QRadioButton {{ color: {TEXT}; spacing: 8px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px; border: 1.5px solid {LINE2};
                                                 background: {FIELD}; }}
QCheckBox::indicator {{ border-radius: 4px; }}
QRadioButton::indicator {{ border-radius: 9px; }}
QCheckBox::indicator:checked {{ background: {AMBER}; border-color: {AMBER}; }}
QRadioButton::indicator:checked {{ border-color: {AMBER};
    background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5, stop:0 {AMBER}, stop:0.5 {AMBER},
                                stop:0.56 {FIELD}, stop:1 {FIELD}); }}
QTimeEdit, QSpinBox, QDoubleSpinBox {{ padding: 2px 8px; }}
QAbstractSpinBox QLineEdit {{ border: none; padding: 0; margin: 0; background: transparent; min-height: 0; }}
QComboBox {{ background: {FIELD}; border: 1px solid {LINE}; border-radius: 9px; padding: 0 8px; min-height: 30px; color: {TEXT}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{ background: {TOP}; color: {TEXT}; border: 1px solid {LINE2};
                               selection-background-color: {AMBER_DIM}; selection-color: {AMBER_HOVER}; outline: none; }}
QGroupBox {{ border: 1px solid {LINE}; border-radius: 10px; margin-top: 14px; padding: 10px 10px 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; color: {MUTED}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {LINE2}; border-radius: 4px; min-height: 30px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {LINE2}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* 라이브러리 */
QDockWidget {{ color: {MUTED}; titlebar-close-icon: none; titlebar-normal-icon: none; }}
QDockWidget::title {{ background: {PANEL}; padding: 10px 14px 6px; font-weight: 600; font-size: 12px; }}
QWidget#library, QWidget#downloads {{ background: {PANEL}; }}
QWidget#library QListWidget {{ background: transparent; border: none; outline: 0; }}
QWidget#library QListWidget::item {{ border-radius: 10px; padding: 6px; margin: 2px 0; color: {TEXT}; }}
QWidget#library QListWidget::item:selected {{ background: {RAISED}; border: 1px solid {LINE2}; color: {TEXT}; }}
QWidget#library QListWidget::item:hover {{ background: #1f2026; }}
QWidget#library QToolButton {{ border: none; border-radius: 8px; background: {RAISED}; min-width: 28px; min-height: 28px; }}
QWidget#library QToolButton:hover {{ background: #30313a; }}

/* 하단 컨트롤 */
QWidget#controls {{ background: {PANEL}; border-top: 1px solid {LINE}; }}
QWidget#controls[overlay="true"] {{ background: #0f1013; border-top: 1px solid {LINE}; }}
QWidget#controls QToolButton {{ border: none; border-radius: 9px; min-width: 34px; min-height: 34px; background: transparent; color: {ICON}; }}
QWidget#controls QToolButton:hover {{ background: {RAISED}; }}
QWidget#controls QToolButton:checked {{ background: {AMBER_DIM}; }}
QWidget#controls QToolButton#play {{ background: {AMBER}; border-radius: 12px; min-width: 42px; min-height: 42px; max-width: 42px; max-height: 42px; }}
QWidget#controls QToolButton#play:hover {{ background: {AMBER_HOVER}; }}
QWidget#controls QToolButton#record:checked {{ background: rgba(239,68,68,0.22); }}
QWidget#controls QComboBox {{ background: transparent; border: 1px solid #4a4c56; border-radius: 9px; padding: 0 6px 0 10px;
                              min-height: 30px; color: #ffffff; font-weight: 700; }}
QLabel#time {{ color: #c3c5cd; }}

QSlider::groove:horizontal {{ height: 4px; border-radius: 2px; background: rgba(255,255,255,0.14); }}
QSlider::sub-page:horizontal {{ background: {AMBER}; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; background: #ffffff; }}
QSlider::handle:horizontal:hover {{ background: {AMBER_HOVER}; }}
QSlider#volume::sub-page:horizontal {{ background: {ICON}; }}
QSlider#volume::handle:horizontal {{ width: 12px; height: 12px; margin: -4px 0; border-radius: 6px; }}

/* 다운로드·녹화 패널 */
QProgressBar {{ border: none; border-radius: 4px; background: {RAISED}; height: 8px; text-align: center; color: {TEXT}; }}
QProgressBar::chunk {{ background: {AMBER}; border-radius: 4px; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

/* 영상 위 알림 */
QLabel#toast {{ background: {FIELD}; color: {TEXT}; border: 1px solid {LINE}; border-radius: 10px; padding: 10px 16px; font-size: 13px; }}
QLabel#loading {{ background: rgba(16,17,20,0.86); color: #ffffff; border: 1px solid rgba(255,255,255,0.12); border-radius: 12px; padding: 14px 22px; font-size: 14px; }}
QLabel#hud {{ background: rgba(16,17,20,0.78); color: {TEXT}; border: 1px solid rgba(255,255,255,0.12); border-radius: 13px; padding: 4px 11px; font-size: 11.5px; }}
QLabel#hudamber {{ background: rgba(16,17,20,0.78); color: {AMBER_HOVER}; border: 1px solid rgba(245,158,11,0.35); border-radius: 13px; padding: 4px 11px; font-size: 11.5px; font-weight: 600; }}
QPushButton#toastbtn {{ background: {AMBER}; color: #1b1206; min-height: 26px; padding: 0 10px; border-radius: 8px; }}
QFrame#banner {{ background: #221c0e; border-bottom: 1px solid #4a3a12; }}
QFrame#banner QLabel {{ color: {AMBER_HOVER}; }}
"""


def apply_palette(app) -> None:
    """QSS 가 닿지 않는 곳(파일 대화상자 일부, 기본 위젯)도 어둡게 맞춘다."""
    pal = QPalette()
    for role, c in (
        (QPalette.ColorRole.Window, BG), (QPalette.ColorRole.WindowText, TEXT),
        (QPalette.ColorRole.Base, FIELD), (QPalette.ColorRole.AlternateBase, PANEL),
        (QPalette.ColorRole.Text, TEXT), (QPalette.ColorRole.Button, RAISED),
        (QPalette.ColorRole.ButtonText, TEXT), (QPalette.ColorRole.Highlight, AMBER),
        (QPalette.ColorRole.HighlightedText, "#1b1206"), (QPalette.ColorRole.ToolTipBase, FIELD),
        (QPalette.ColorRole.ToolTipText, TEXT), (QPalette.ColorRole.PlaceholderText, "#6b6e7a"),
        (QPalette.ColorRole.Link, AMBER_HOVER), (QPalette.ColorRole.Mid, LINE),
    ):
        pal.setColor(role, QColor(c))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#5b5e69"))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#5b5e69"))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor("#5b5e69"))
    app.setPalette(pal)
