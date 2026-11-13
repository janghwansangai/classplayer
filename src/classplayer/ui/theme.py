"""시안 A: 밝은 교실형 테마. 흰 바탕, 둥근 파란 버튼, 얇은 회색 선."""

BLUE = "#2563eb"
BLUE_DARK = "#1d4ed8"
GRAY_BG = "#fafafa"
LINE = "#e5e7eb"
TEXT = "#111827"
MUTED = "#6b7280"

QSS = f"""
QMainWindow, QWidget#central {{ background: #ffffff; }}
QWidget {{ color: {TEXT}; font-size: 13px; }}
QMenuBar {{ background: #ffffff; }}
QStatusBar {{ background: #ffffff; border-top: 1px solid {LINE}; color: {MUTED}; }}
QStatusBar QLabel {{ color: {MUTED}; }}

/* 상단 링크 줄 */
QWidget#urlbar {{ background: #ffffff; border-bottom: 1px solid {LINE}; }}
QLineEdit#url {{ border: 1.5px solid #d1d5db; border-radius: 18px; padding: 0 14px; min-height: 36px;
                 background: #ffffff; selection-background-color: {BLUE}; }}
QLineEdit#url:focus {{ border-color: {BLUE}; }}
QPushButton {{ border: none; border-radius: 18px; min-height: 36px; padding: 0 16px; background: #f3f4f6;
               color: {TEXT}; font-weight: 600; }}
QPushButton:hover {{ background: #e5e7eb; }}
QPushButton:pressed {{ background: #d1d5db; }}
QPushButton#primary {{ background: {BLUE}; color: #ffffff; }}
QPushButton#primary:hover {{ background: {BLUE_DARK}; }}
QLabel#logo {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {BLUE}, stop:1 #06b6d4);
               color: #ffffff; border-radius: 8px; font-weight: 800; font-size: 15px;
               min-width: 30px; max-width: 30px; min-height: 30px; max-height: 30px; }}

/* 라이브러리 */
QDockWidget {{ titlebar-close-icon: none; titlebar-normal-icon: none; }}
QDockWidget::title {{ background: {GRAY_BG}; padding: 8px 12px; font-weight: 700; border-bottom: 1px solid {LINE}; }}
QWidget#library {{ background: {GRAY_BG}; }}
QWidget#library QLineEdit {{ border: 1px solid {LINE}; border-radius: 8px; padding: 0 10px; min-height: 30px; background: #ffffff; }}
QWidget#library QListWidget {{ background: transparent; border: none; outline: 0; }}
QWidget#library QListWidget::item {{ border-radius: 8px; padding: 6px; margin: 2px 0; }}
QWidget#library QListWidget::item:selected {{ background: #dbeafe; color: {TEXT}; }}
QWidget#library QListWidget::item:hover {{ background: #eef2ff; }}
QWidget#library QToolButton {{ border: 1px solid {LINE}; border-radius: 8px; background: #ffffff; min-width: 28px; min-height: 28px; }}
QWidget#library QComboBox {{ border: 1px solid {LINE}; border-radius: 8px; padding: 0 8px; min-height: 28px; background: #ffffff; }}

/* 하단 컨트롤 */
QWidget#controls {{ background: #ffffff; border-top: 1px solid {LINE}; }}
QWidget#controls[overlay="true"] {{ background: rgba(18,18,22,0.82); border: 1px solid rgba(255,255,255,0.25); border-radius: 16px; }}
QWidget#controls[overlay="true"] QToolButton {{ color: #f3f4f6; }}
QWidget#controls[overlay="true"] QToolButton#play {{ background: {BLUE}; color: #ffffff; }}
QWidget#controls[overlay="true"] QToolButton:hover {{ background: rgba(255,255,255,0.15); }}
QWidget#controls[overlay="true"] QLabel#time {{ color: #e5e7eb; }}
QWidget#controls[overlay="true"] QComboBox {{ background: rgba(255,255,255,0.12); color: #f3f4f6; border-color: rgba(255,255,255,0.25); }}
QWidget#controls QToolButton {{ border: none; border-radius: 8px; min-width: 34px; min-height: 34px; background: transparent; color: #374151; }}
QWidget#controls QToolButton:hover {{ background: #f3f4f6; }}
QWidget#controls QToolButton:checked {{ background: #dbeafe; color: {BLUE}; }}
QToolButton#play {{ background: {BLUE}; color: #ffffff; font-size: 16px; font-weight: 700; border-radius: 20px; min-width: 40px; min-height: 40px; max-width: 40px; max-height: 40px; }}
QToolButton#play:hover {{ background: {BLUE_DARK}; }}
QWidget#controls QComboBox {{ border: 1px solid {LINE}; border-radius: 8px; padding: 0 6px; min-height: 28px; background: #ffffff; }}
QLabel#time {{ color: {MUTED}; font-variant-numeric: tabular-nums; }}

QSlider::groove:horizontal {{ height: 6px; border-radius: 3px; background: {LINE}; }}
QSlider::sub-page:horizontal {{ background: {BLUE}; border-radius: 3px; }}
QSlider::handle:horizontal {{ width: 16px; height: 16px; margin: -5px 0; border-radius: 8px; background: {BLUE};
                              border: 3px solid #ffffff; }}
QSlider#volume::groove:horizontal {{ height: 4px; }}
QSlider#volume::handle:horizontal {{ width: 12px; height: 12px; margin: -4px 0; border-radius: 6px; border: 2px solid #ffffff; }}

/* 다운로드 패널 */
QProgressBar {{ border: none; border-radius: 4px; background: {LINE}; height: 8px; text-align: center; }}
QProgressBar::chunk {{ background: {BLUE}; border-radius: 4px; }}
QScrollArea {{ border: none; }}

QLabel#toast {{ background: rgba(17,24,39,0.92); color: #ffffff; border-radius: 10px; padding: 10px 16px; font-size: 14px; }}
QLabel#loading {{ background: rgba(0,0,0,0.65); color: #ffffff; border-radius: 12px; padding: 14px 22px; font-size: 15px; }}
QPushButton#toastbtn {{ background: rgba(255,255,255,0.16); color: #ffffff; min-height: 26px; padding: 0 10px; border-radius: 13px; }}

"""
