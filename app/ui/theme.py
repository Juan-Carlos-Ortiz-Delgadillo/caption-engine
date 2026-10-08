"""Tema plano oscuro de Animated Caption Studio."""

STYLESHEET = """
QWidget { color: #f2f3f5; font-family: Arial; font-size: 14px; }
QMainWindow, QWidget#applicationRoot { background: #17191f; }
QFrame#card { background: #22252d; border: 1px solid #30343e; border-radius: 14px; }
QLabel, QCheckBox { background: transparent; }
QLabel#title { font-size: 25px; font-weight: 700; }
QLabel#section { font-size: 13px; font-weight: 700; color: #aeb5c2; }
QLabel#fieldLabel { color: #aeb5c2; font-weight: 600; }
QLabel#videoName { font-size: 15px; font-weight: 600; }
QLabel#hint { color: #aeb5c2; font-size: 12px; }
QLabel#environmentBadge { color: #c4c8d2; background: #30343e; border-radius: 9px; padding: 8px 12px; font-size: 11px; font-weight: 700; }
QLabel#environmentBadge[ready="true"] { color: #8ae0b0; background: #20372c; }
QLabel#environmentDetails { color: #c4c8d2; line-height: 1.4; }
QLabel#status { color: #c4c8d2; }
QPushButton { background: #30343e; border: 0; border-radius: 9px; padding: 10px 15px; }
QPushButton:hover { background: #414754; }
QPushButton#secondary { padding: 7px 12px; }
QPushButton#closeApplication { background: #30343e; padding: 10px 15px; }
QPushButton#closeApplication:hover { background: #414754; }
QPushButton#primary { background: #7257ff; color: white; font-weight: 700; }
QPushButton#primary:hover { background: #846eff; }
QPushButton:disabled { color: #777d89; background: #292c33; }
QComboBox, QSpinBox, QFontComboBox { background: #30343e; border: 0; border-radius: 8px; padding: 8px; min-height: 20px; }
QComboBox QAbstractItemView { background: #30343e; selection-background-color: #7257ff; }
QCheckBox { spacing: 8px; }
QScrollArea#contentScroll, QScrollArea#contentScroll QWidget#qt_scrollarea_viewport { background: #17191f; }
QWidget#contentPage { background: #17191f; }
QProgressBar { border: 0; background: #30343e; border-radius: 5px; height: 10px; text-align: center; }
QProgressBar::chunk { background: #7257ff; border-radius: 5px; }
"""
