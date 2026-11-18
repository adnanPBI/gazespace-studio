"""Shared desktop colors and Qt styling."""
BG = '#0c1421'
PANEL = '#142032'
BORDER = '#26364d'
TEXT = '#edf5ff'
MUTED = '#93a7c0'
TEAL = '#45dfbd'
BLUE = '#5ca9ff'
AMBER = '#ffcf81'
STYLE = '''
QMainWindow, QDialog, QWidget#root { background:#0c1421; color:#edf5ff; }
QWidget { color:#edf5ff; font-family:"Segoe UI","DejaVu Sans",sans-serif; font-size:13px; }
QFrame#sidebar { background:#101b2b; border-right:1px solid #26364d; }
QFrame#card { background:#142032; border:1px solid #26364d; border-radius:12px; }
QLabel#muted { color:#93a7c0; }
QLabel#title { font-size:27px; font-weight:700; }
QLabel#section { font-size:17px; font-weight:600; }
QLabel#metric { font-size:29px; font-weight:600; color:#f2f7ff; }
QLabel#eyebrow { color:#45dfbd; font-size:11px; font-weight:700; letter-spacing:2px; }
QLabel#badge { background:#153d3c; border:1px solid #2e6860; border-radius:11px; color:#78efd3; padding:5px 11px; font-size:11px; }
QPushButton { background:#20324a; border:1px solid #344a67; border-radius:7px; padding:8px 13px; font-weight:600; min-height:19px; }
QPushButton:hover { background:#2b4261; border-color:#5ca9ff; }
QPushButton:pressed { background:#1c2b3f; }
QPushButton:disabled { background:#172337; border-color:#27364b; color:#677a93; }
QPushButton#primary { background:#45dfbd; border:1px solid #45dfbd; color:#05271f; }
QPushButton#primary:hover { background:#7cebd2; }
QPushButton#danger { border:1px solid #b66570; color:#ffb7c1; background:#392435; }
QPushButton#nav { text-align:left; border:0; background:transparent; border-radius:8px; padding:11px 14px; color:#9dafc7; }
QPushButton#nav:checked { background:#1b3b42; color:#78efd3; }
QPushButton#nav:hover { background:#1c2c42; color:#edf5ff; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QPlainTextEdit { background:#0d1828; border:1px solid #30445f; border-radius:6px; padding:7px; color:#eaf4ff; selection-background-color:#326d81; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border:1px solid #45dfbd; }
QComboBox QAbstractItemView { background:#19283d; color:#edf5ff; selection-background-color:#2c5968; }
QCheckBox { spacing:8px; }
QCheckBox::indicator { width:16px; height:16px; border:1px solid #49607d; border-radius:3px; background:#0d1828; }
QCheckBox::indicator:checked { background:#45dfbd; border-color:#45dfbd; }
QProgressBar { border:1px solid #30445f; border-radius:5px; background:#0d1828; text-align:center; min-height:17px; }
QProgressBar::chunk { background:#32bda2; border-radius:4px; }
QTableWidget, QListWidget { background:#101d2e; alternate-background-color:#16253a; border:1px solid #2c405b; border-radius:7px; gridline-color:#263950; }
QTableWidget::item { padding:6px; }
QTableWidget::item:selected, QListWidget::item:selected { background:#24515c; }
QHeaderView::section { background:#203149; color:#abc0d9; border:0; padding:9px; font-weight:600; }
QTabWidget::pane { border:1px solid #2c405b; border-radius:8px; }
QTabBar::tab { background:#17273b; padding:10px 18px; border:1px solid #2c405b; }
QTabBar::tab:selected { color:#73edd2; background:#1d3d45; }
QScrollArea { border:0; background:transparent; }
QScrollBar:vertical { background:#111e30; width:10px; }
QScrollBar::handle:vertical { background:#3a4d67; border-radius:5px; min-height:25px; }
QStatusBar { background:#101b2b; color:#93a7c0; border-top:1px solid #26364d; }
QMenuBar, QMenu { background:#142032; color:#edf5ff; }
QToolTip { background:#253a53; color:#edf5ff; border:1px solid #4b647f; }
'''
