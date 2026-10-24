"""Colours, fonts and the Qt stylesheet for PipeDraw's interface."""
import re

from PySide6 import QtGui

ACCENT = "#2557D6"
ACCENT_DARK = "#1C44A8"
ACCENT_SOFT = "#EEF3FD"
GROUND = "#F4F5F7"
CARD = "#FFFFFF"
BORDER = "#E3E5E9"
BORDER_STRONG = "#D5D9E0"
DIVIDER = "#EEF0F3"
SUBTLE = "#F7F8FA"
TEXT = "#16181D"
TEXT2 = "#3B4250"
MUTED = "#5B616E"
NEG = "#D9730D"          # negative contours
TRACE_MARK = "#B45309"   # trace positions on the 2D plot
PIVOT = "#0E7C66"
PIVOT_SOFT = "#E7F5EE"
PIVOT_TEXT = "#0B5E4E"
OK = "#16A34A"
WARN = "#D9730D"
ERROR = "#B42318"
CONSOLE = "#16181D"
TRACE_COLORS = [TEXT, ACCENT, PIVOT, "#7C3AED", TRACE_MARK, "#DB2777"]

_SUPERSCRIPT = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")


def nice_label(label: str) -> str:
    """'15N' -> '¹⁵N' for axis labels."""
    m = re.match(r"^(\d+)([A-Z][a-z]?)(.*)$", label.strip())
    if not m:
        return label
    return m.group(1).translate(_SUPERSCRIPT) + m.group(2) + m.group(3)


def _first_family(*names):
    available = set(QtGui.QFontDatabase.families())
    for name in names:
        if name in available:
            return name
    return names[-1]


def ui_font(size=13, weight=QtGui.QFont.Normal) -> QtGui.QFont:
    f = QtGui.QFont(_first_family("Inter", "SF Pro Text", ".AppleSystemUIFont",
                                  "Helvetica Neue", "Arial"))
    f.setPixelSize(size)
    f.setWeight(weight)
    return f


def mono_font(size=12) -> QtGui.QFont:
    f = QtGui.QFont(_first_family("JetBrains Mono", "SF Mono", "Menlo", "Monaco", "Courier"))
    f.setStyleHint(QtGui.QFont.Monospace)
    f.setPixelSize(size)
    return f


def stylesheet() -> str:
    mono = mono_font().family()
    return f"""
QMainWindow, QWidget#ground {{ background: {GROUND}; }}
QWidget {{ color: {TEXT}; }}
QToolTip {{ background: {TEXT}; color: white; border: 0; padding: 4px 6px; }}

QFrame#header, QFrame#footer {{ background: {CARD}; }}
QFrame#header {{ border-bottom: 1px solid {BORDER}; }}
QFrame#footer {{ border-top: 1px solid {BORDER}; }}

QFrame#card {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#cardHeader {{ border-bottom: 1px solid {DIVIDER}; }}
QFrame#console {{ background: {CONSOLE}; border-radius: 12px; }}
QFrame#console QLabel {{ color: #E6E8EC; }}
QFrame#banner {{ background: {ACCENT_SOFT}; border-bottom: 1px solid #D6E2FB; }}
QFrame#banner QLabel {{ color: #1C3A86; }}
QFrame#dropZone {{ background: {CARD}; border: 2px dashed #C3C9D3; border-radius: 14px; }}
QFrame#dropZone[hover="true"] {{ border-color: {ACCENT}; background: {ACCENT_SOFT}; }}

QLabel[role="h1"] {{ font-size: 28px; font-weight: 700; }}
QLabel[role="h2"] {{ font-size: 15px; font-weight: 700; }}
QLabel[role="h3"] {{ font-size: 13px; font-weight: 600; }}
QLabel[role="field"] {{ font-size: 12px; font-weight: 600; color: {TEXT2}; }}
QLabel[role="muted"] {{ font-size: 12px; color: {MUTED}; }}
QLabel[role="section"] {{ font-size: 11px; font-weight: 700; color: {TEXT2}; letter-spacing: 0.5px; }}
QLabel[role="mono"] {{ font-family: "{mono}"; font-size: 12px; }}
QLabel[role="pill"] {{ background: #EEF1F6; color: {TEXT2}; border-radius: 10px; padding: 2px 8px;
                       font-family: "{mono}"; font-size: 12px; }}
QLabel[role="pivot"] {{ background: {PIVOT_SOFT}; color: {PIVOT_TEXT}; border-radius: 6px; padding: 4px 8px;
                        font-family: "{mono}"; font-size: 12px; }}
QLabel[role="badge"] {{ border-radius: 11px; font-size: 12px; font-weight: 700; }}
QLabel[role="badge"][state="done"] {{ background: {PIVOT_SOFT}; color: {PIVOT}; }}
QLabel[role="badge"][state="active"] {{ background: {ACCENT}; color: white; }}
QLabel[role="badge"][state="todo"] {{ background: {DIVIDER}; color: {TEXT2}; }}
QLabel[role="new"] {{ font-family: "{mono}"; font-size: 12px; color: {MUTED}; }}
QLabel[role="new"][changed="true"] {{ color: {ACCENT_DARK}; font-weight: 700; }}
QLabel[role="status"] {{ border-radius: 9px; padding: 2px 8px; font-size: 12px; font-weight: 600; }}
QLabel[role="status"][state="idle"] {{ background: #2A2E36; color: #C3C8D1; }}
QLabel[role="status"][state="running"] {{ background: #3A2A12; color: #FBC98E; }}
QLabel[role="status"][state="ok"] {{ background: #12352A; color: #86EFAC; }}
QLabel[role="status"][state="failed"] {{ background: #3B1714; color: #FCA5A5; }}

QPushButton {{ background: {CARD}; border: 1px solid {BORDER_STRONG}; border-radius: 8px;
               padding: 0 12px; min-height: 30px; font-weight: 500; color: {TEXT}; }}
QPushButton:hover {{ background: {SUBTLE}; }}
QPushButton:pressed {{ background: {DIVIDER}; }}
QPushButton:disabled {{ color: #9AA0AB; }}
QPushButton:focus {{ border-color: {ACCENT}; }}
QPushButton[kind="primary"] {{ background: {ACCENT}; border: 0; color: white; font-weight: 600; }}
QPushButton[kind="primary"]:hover {{ background: {ACCENT_DARK}; }}
QPushButton[kind="primary"]:disabled {{ background: #9DB3EA; color: white; }}
QPushButton[kind="big"] {{ min-height: 40px; border-radius: 9px; }}
QPushButton[kind="link"] {{ background: transparent; border: 0; color: {ACCENT}; padding: 0 2px;
                            min-height: 20px; text-decoration: underline; font-size: 12px; }}
QPushButton[kind="step"] {{ min-width: 30px; max-width: 30px; padding: 0; background: {SUBTLE};
                            font-size: 16px; color: {TEXT2}; }}
QPushButton[kind="chipX"] {{ background: transparent; border: 0; min-height: 18px; min-width: 18px;
                             max-width: 18px; padding: 0; color: {MUTED}; }}
QPushButton[kind="chipX"]:hover {{ color: {TEXT}; }}
QPushButton[kind="toggle"] {{ min-height: 24px; padding: 0 8px; border-radius: 6px; font-size: 12px; }}
QPushButton[kind="toggle"]:checked {{ border-color: {ACCENT}; background: {ACCENT_SOFT}; color: {ACCENT_DARK}; }}
QPushButton[kind="outline"] {{ text-align: left; padding: 6px 10px; border-color: {BORDER}; }}
QPushButton[kind="outline"][pending="true"] {{ border: 2px solid {ACCENT}; background: {ACCENT_SOFT}; }}
QPushButton[kind="console"] {{ background: #2A2E36; border: 0; color: #E6E8EC; }}

QFrame#segmented {{ background: {DIVIDER}; border-radius: 9px; }}
QPushButton[kind="seg"] {{ background: transparent; border: 0; border-radius: 7px; padding: 0 14px;
                           min-height: 28px; color: {TEXT2}; }}
QPushButton[kind="seg"]:checked {{ background: {CARD}; color: {TEXT}; font-weight: 600; }}
QPushButton[kind="seg"]:disabled {{ color: #9AA0AB; }}
QPushButton[kind="dim"] {{ background: transparent; border: 0; border-radius: 7px; min-height: 44px; }}
QPushButton[kind="dim"]:checked {{ background: {CARD}; }}

QFrame#chip {{ background: {GROUND}; border-radius: 6px; }}
QFrame#stepper {{ border: 1px solid {BORDER_STRONG}; border-radius: 8px; background: {CARD}; }}
QFrame#stepper QPushButton {{ border: 0; border-radius: 0; background: {SUBTLE}; min-width: 30px;
                              max-width: 30px; padding: 0; font-size: 16px; color: {TEXT2}; }}
QFrame#stepper QLineEdit, QFrame#stepper QLabel {{ border: 0; border-left: 1px solid {BORDER};
                              border-right: 1px solid {BORDER}; border-radius: 0; background: {CARD}; }}

QFrame#stepper QSpinBox {{ border: 0; border-left: 1px solid {BORDER}; border-right: 1px solid {BORDER};
                           border-radius: 0; }}
QComboBox {{ border: 1px solid {BORDER_STRONG}; border-radius: 7px; padding: 0 8px; min-height: 28px;
             background: {CARD}; font-size: 12px; }}
QComboBox:focus {{ border-color: {ACCENT}; }}
QLineEdit, QDoubleSpinBox, QSpinBox {{ border: 1px solid {BORDER_STRONG}; border-radius: 7px;
               padding: 0 8px; min-height: 28px; background: {CARD}; font-family: "{mono}";
               font-size: 12px; selection-background-color: {ACCENT_SOFT}; selection-color: {TEXT}; }}
QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus {{ border-color: {ACCENT}; }}
QDoubleSpinBox[readOnly="true"] {{ border-color: transparent; background: transparent; color: {MUTED}; }}
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button, QSpinBox::up-button, QSpinBox::down-button {{
    width: 0; border: 0; }}

QCheckBox {{ font-size: 12px; color: {TEXT2}; spacing: 6px; }}

QSlider::groove:horizontal {{ height: 4px; background: {BORDER}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 16px; height: 16px; margin: -6px 0; border-radius: 8px;
                              background: {CARD}; border: 2px solid {ACCENT}; }}

QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: 0; }}
QSplitter::handle {{ background: transparent; }}

QPlainTextEdit#editor {{ border: 0; background: {CARD}; font-family: "{mono}"; font-size: 13px;
                         selection-background-color: #CFDDFB; }}
QPlainTextEdit#log {{ border: 0; background: {CONSOLE}; color: #C3C8D1; font-family: "{mono}";
                      font-size: 12px; }}
QProgressBar {{ background: #2A2E36; border: 0; border-radius: 3px; max-height: 6px; }}
QProgressBar::chunk {{ background: #F59E0B; border-radius: 3px; }}
"""
