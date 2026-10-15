"""Small shared widgets."""

from PySide6.QtCore import Property, QPropertyAnimation, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QLabel, QWidget


class Toggle(QAbstractButton):
    """A sliding on/off switch, drawn in the theme's accent color."""

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pos = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(120)
        self.toggled.connect(self._animate)

    def sizeHint(self):
        return QSize(40, 22)

    def nextCheckState(self):
        self._clicked = True  # only a click by the user slides; set from code, it just jumps
        super().nextCheckState()

    def _animate(self, on: bool):
        self._anim.stop()
        if not getattr(self, "_clicked", False):
            self._set_knob(1.0 if on else 0.0)
            return
        self._clicked = False
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def _get_knob(self) -> float:
        return self._pos

    def _set_knob(self, value: float):
        self._pos = value
        self.update()

    knob = Property(float, _get_knob, _set_knob)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect().adjusted(1, 1, -1, -1)
        on = self.palette().highlight().color()
        off = self.palette().placeholderText().color()
        track = QColor(on) if self.isChecked() else QColor(off)
        if not self.isChecked():
            track.setAlpha(110)
        if not self.isEnabled():
            track.setAlpha(60)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 4
        x = r.left() + 2 + self._pos * (r.width() - d - 4)
        p.setBrush(QColor("#ffffff") if self.isEnabled() else QColor(220, 220, 220))
        p.drawEllipse(QRectF(x, r.top() + 2, d, d))


class ToggleRow(QWidget):
    """A toggle with its label beside it, used wherever a checkbox would be. Clicking the label
    flips it too. Offers the same isChecked / setChecked / toggled as a checkbox."""

    toggled = Signal(bool)

    def __init__(self, text: str, checked: bool = False):
        super().__init__()
        self.toggle = Toggle(checked)
        self.label = QLabel(text)
        self.label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.label.mousePressEvent = lambda _e: self.toggle.isEnabled() and self.toggle.toggle()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 2, 0, 2)
        row.addWidget(self.toggle)
        row.addWidget(self.label)
        row.addStretch(1)
        self.toggle.toggled.connect(self.toggled)

    def isChecked(self) -> bool:
        return self.toggle.isChecked()

    def setChecked(self, on: bool):
        self.toggle.setChecked(on)

    def setToolTip(self, text: str):
        super().setToolTip(text)
        self.toggle.setToolTip(text)
        self.label.setToolTip(text)
