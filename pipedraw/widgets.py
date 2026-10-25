"""Small styled building blocks for the PipeDraw interface."""
from PySide6 import QtCore, QtGui, QtWidgets

from . import theme

Qt = QtCore.Qt


def restyle(w):
    """Re-apply the stylesheet after changing a dynamic property."""
    w.style().unpolish(w)
    w.style().polish(w)


def clear_layout(lay):
    """Remove and delete every item of a layout right away."""
    while lay.count():
        it = lay.takeAt(0)
        w = it.widget()
        if w is not None:
            w.hide()
            w.setParent(None)
            w.deleteLater()
        elif it.layout() is not None:
            clear_layout(it.layout())


def label(text="", role=None, **props):
    lbl = QtWidgets.QLabel(text)
    if role:
        lbl.setProperty("role", role)
    for k, v in props.items():
        lbl.setProperty(k, v)
    return lbl


def button(text="", kind=None, icon=None, tip=None, slot=None, checkable=False):
    b = QtWidgets.QPushButton(text)
    if kind:
        b.setProperty("kind", kind)
    if icon:
        b.setIcon(icon)
    if tip:
        b.setToolTip(tip)
    if slot:
        b.clicked.connect(slot)
    b.setCheckable(checkable)
    b.setCursor(Qt.PointingHandCursor)
    return b


def hbox(*items, spacing=8, margins=(0, 0, 0, 0)):
    lay = QtWidgets.QHBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for it in items:
        _add(lay, it)
    return lay


def vbox(*items, spacing=8, margins=(0, 0, 0, 0)):
    lay = QtWidgets.QVBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for it in items:
        _add(lay, it)
    return lay


def _add(lay, it):
    if it is None:
        lay.addStretch(1)
    elif isinstance(it, QtWidgets.QLayout):
        lay.addLayout(it)
    elif isinstance(it, int):
        lay.addSpacing(it)
    else:
        lay.addWidget(it)


class InfoTip(QtWidgets.QLabel):
    """A small ⓘ that shows an explanation on hover, instead of text on screen."""

    def __init__(self, text):
        super().__init__("ⓘ")
        self.setToolTip(text)
        self.setCursor(Qt.WhatsThisCursor)
        self.setStyleSheet(f"color: {theme.MUTED}; font-size: 13px;")


def heading(text, tip=None, role="h2"):
    """A heading with an optional ⓘ explanation."""
    lbl = label(text, role)
    if not tip:
        return lbl
    w = QtWidgets.QWidget()
    w.setLayout(hbox(lbl, InfoTip(tip), None, spacing=6))
    w.title = lbl
    return w


class Card(QtWidgets.QFrame):
    """White rounded panel. ``body`` is its main layout."""

    def __init__(self, margins=(14, 14, 14, 14), spacing=12, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.body = vbox(spacing=spacing, margins=margins)
        self.setLayout(self.body)


class CardHeader(QtWidgets.QFrame):
    """A strip at the top of a card, with a divider below."""

    def __init__(self, *items):
        super().__init__()
        self.setObjectName("cardHeader")
        self.row = hbox(*items, spacing=12, margins=(14, 8, 14, 8))
        self.setLayout(self.row)


class Segmented(QtWidgets.QFrame):
    """Exclusive set of buttons on a grey track."""
    changed = QtCore.Signal(int)

    def __init__(self, labels, kind="seg"):
        super().__init__()
        self.setObjectName("segmented")
        lay = hbox(spacing=2, margins=(3, 3, 3, 3))
        self.setLayout(lay)
        self.group = QtWidgets.QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons = []
        for i, text in enumerate(labels):
            b = button(text, kind=kind, checkable=True)
            self.group.addButton(b, i)
            lay.addWidget(b, 1 if kind == "dim" else 0)
            self.buttons.append(b)
        self.buttons[0].setChecked(True)
        self.group.idClicked.connect(self.changed)

    def index(self):
        return self.group.checkedId()

    def set_index(self, i):
        self.buttons[i].setChecked(True)


def two_line(button_, title, subtitle):
    """Put a bold title and a small subtitle inside a button."""
    lay = vbox(spacing=0, margins=(10, 6, 10, 6))
    t = label(title, "h3")
    s = label(subtitle, "muted")
    for lbl in (t, s):
        lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
    lay.addWidget(t)
    lay.addWidget(s)
    button_.setLayout(lay)
    return t, s


class FlowLayout(QtWidgets.QLayout):
    """Lays out widgets left to right, wrapping onto new lines."""

    def __init__(self, spacing=6):
        super().__init__()
        self._items = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QtCore.QRect(0, 0, width, 0), dry=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, dry=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QtCore.QSize()
        for it in self._items:
            size = size.expandedTo(it.minimumSize())
        return size

    def _do_layout(self, rect, dry):
        x, y, line_h = rect.x(), rect.y(), 0
        for it in self._items:
            hint = it.sizeHint()
            if x + hint.width() > rect.right() and line_h > 0:
                x = rect.x()
                y += line_h + self._spacing
                line_h = 0
            if not dry:
                it.setGeometry(QtCore.QRect(QtCore.QPoint(x, y), hint))
            x += hint.width() + self._spacing
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y()

    def clear(self):
        while self._items:
            w = self._items.pop().widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()


class Chip(QtWidgets.QFrame):
    """Colour swatch, text and a remove button."""
    removed = QtCore.Signal()

    def __init__(self, text, color, removable=True):
        super().__init__()
        self.setObjectName("chip")
        sw = QtWidgets.QFrame()
        sw.setFixedSize(8, 8)
        sw.setStyleSheet(f"background: {color}; border-radius: 2px;")
        lay = hbox(sw, label(text, "mono"), spacing=6, margins=(8, 3, 4 if removable else 8, 3))
        if removable:
            x = button("×", kind="chipX", tip=f"Remove trace at {text}", slot=self.removed)
            lay.addWidget(x)
        self.setLayout(lay)


def legend_item(text, color):
    sw = QtWidgets.QFrame()
    sw.setFixedSize(14, 2)
    sw.setStyleSheet(f"background: {color};")
    w = QtWidgets.QWidget()
    w.setLayout(hbox(sw, label(text, "muted"), spacing=6))
    return w


class StepBadge(QtWidgets.QLabel):
    def __init__(self, number):
        super().__init__(str(number))
        self.number = number
        self.setProperty("role", "badge")
        self.setFixedSize(22, 22)
        self.setAlignment(Qt.AlignCenter)
        self.set_state("todo")

    def set_state(self, state):
        if self.property("state") == state:
            return
        self.setProperty("state", state)
        self.setText("✓" if state == "done" else str(self.number))
        restyle(self)


class Dot(QtWidgets.QFrame):
    def __init__(self, color=theme.OK, size=8):
        super().__init__()
        self.setFixedSize(size, size)
        self._size = size
        self.set_color(color)

    def set_color(self, color):
        self.setStyleSheet(f"background: {color}; border-radius: {self._size // 2}px;")


class Stepper(QtWidgets.QFrame):
    """[−] value [+]. ``widget`` is the middle field."""

    def __init__(self, widget, minus_tip, plus_tip, on_minus, on_plus, width=64):
        super().__init__()
        self.setObjectName("stepper")
        self.widget = widget
        widget.setFixedWidth(width)
        widget.setAlignment(Qt.AlignCenter)
        minus = button("−", tip=minus_tip, slot=on_minus)
        plus = button("+", tip=plus_tip, slot=on_plus)
        self.setLayout(hbox(minus, widget, plus, spacing=0))


def icon(name, color=theme.TEXT, size=16):
    """Simple line icons drawn with QPainter."""
    pm = QtGui.QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    pen = QtGui.QPen(QtGui.QColor(color), 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    s = size / 24.0
    p.scale(s, s)
    path = QtGui.QPainterPath()
    if name == "play":
        path.moveTo(7, 4)
        path.lineTo(20, 12)
        path.lineTo(7, 20)
        path.closeSubpath()
        p.fillPath(path, QtGui.QColor(color))
    elif name == "stop":
        p.fillRect(QtCore.QRectF(6, 6, 12, 12), QtGui.QColor(color))
    elif name == "folder":
        path.moveTo(3, 7)
        path.lineTo(9, 7)
        path.lineTo(11, 9)
        path.lineTo(21, 9)
        path.lineTo(21, 19)
        path.lineTo(3, 19)
        path.closeSubpath()
        p.drawPath(path)
    elif name == "copy":
        p.drawRoundedRect(QtCore.QRectF(9, 9, 11, 11), 2, 2)
        path.moveTo(5, 15)
        path.lineTo(5, 5)
        path.lineTo(15, 5)
        p.drawPath(path)
    elif name == "check":
        pen.setWidthF(2.6)
        p.setPen(pen)
        path.moveTo(5, 12)
        path.lineTo(10, 17)
        path.lineTo(19, 7)
        p.drawPath(path)
    elif name == "box":
        pen.setStyle(Qt.DashLine)
        p.setPen(pen)
        p.drawRect(QtCore.QRectF(4, 4, 16, 16))
    elif name == "upload":
        path.moveTo(12, 15)
        path.lineTo(12, 4)
        path.moveTo(7, 9)
        path.lineTo(12, 4)
        path.lineTo(17, 9)
        path.moveTo(4, 15)
        path.lineTo(4, 20)
        path.lineTo(20, 20)
        path.lineTo(20, 15)
        p.drawPath(path)
    elif name == "info":
        p.drawEllipse(QtCore.QPointF(12, 12), 9, 9)
        path.moveTo(12, 8)
        path.lineTo(12, 13)
        path.moveTo(12, 16)
        path.lineTo(12, 16.5)
        p.drawPath(path)
    elif name == "logo":
        p.drawEllipse(QtCore.QPointF(12, 12), 9, 6)
        p.drawEllipse(QtCore.QPointF(12, 12), 5, 3)
        p.drawEllipse(QtCore.QPointF(12, 12), 1, 1)
    p.end()
    return QtGui.QIcon(pm)
