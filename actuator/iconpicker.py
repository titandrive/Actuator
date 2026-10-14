"""Choosing a Lucide icon (for folders): search by name or tag, pick from a grid."""

import json

from PySide6.QtCore import QAbstractListModel, QModelIndex, QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QLineEdit, QListView, QVBoxLayout,
)

from . import icons


def _all_icons() -> tuple[list[str], dict[str, list[str]]]:
    names = sorted(p.stem for p in icons.ICON_DIR.glob("*.svg"))
    try:
        tags = json.loads((icons.ICON_DIR / "tags.json").read_text())
    except (OSError, ValueError):
        tags = {}
    return names, tags


class IconModel(QAbstractListModel):
    """Icons are drawn only when the view asks for them, so 1,500 of them open instantly."""

    def __init__(self, names: list[str], color: str):
        super().__init__()
        self.names = names
        self.color = color
        self._cache: dict[str, QIcon] = {}

    def set_names(self, names: list[str]):
        self.beginResetModel()
        self.names = names
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.names)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        name = self.names[index.row()]
        if role == Qt.ItemDataRole.DecorationRole:
            if name not in self._cache:
                self._cache[name] = QIcon(icons._pixmap(name, self.color, 28))
            return self._cache[name]
        if role in (Qt.ItemDataRole.ToolTipRole, Qt.ItemDataRole.UserRole):
            return name
        return None


class IconPicker(QDialog):
    """self.chosen is the icon name when accepted, or None for "Reset to default"."""

    def __init__(self, parent, current: str | None = None, title: str = "Choose an icon"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.chosen: str | None = current
        self.names, self.tags = _all_icons()
        self.search = QLineEdit(placeholderText="Search icons: game, work, music…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        self.model = IconModel(self.names, self.palette().text().color().name())
        self.view = QListView()
        self.view.setViewMode(QListView.ViewMode.IconMode)
        self.view.setResizeMode(QListView.ResizeMode.Adjust)
        self.view.setUniformItemSizes(True)
        self.view.setIconSize(QSize(28, 28))
        self.view.setGridSize(QSize(52, 52))
        self.view.setMovement(QListView.Movement.Static)
        self.view.setModel(self.model)
        self.view.doubleClicked.connect(lambda *_: self._accept())
        self.view.selectionModel().currentChanged.connect(self._selected)
        self.name = QLabel()
        self.name.setStyleSheet("color: palette(placeholder-text);")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
                                   | QDialogButtonBox.StandardButton.RestoreDefaults)
        buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).setText("Reset to default")
        buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(self._reset)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.search)
        layout.addWidget(self.view, 1)
        layout.addWidget(self.name)
        layout.addWidget(buttons)
        self.resize(560, 520)
        self.search.setFocus()
        if current in self.names:
            self.view.setCurrentIndex(self.model.index(self.names.index(current)))

    def _filter(self, text: str):
        words = text.lower().split()
        if not words:
            shown = self.names
        else:
            shown = [n for n in self.names
                     if all(w in n or any(w in t for t in self.tags.get(n, [])) for w in words)]
        self.model.set_names(shown)
        if shown:
            self.view.setCurrentIndex(self.model.index(0))  # Enter picks the top match
        self.name.setText(f"{len(shown)} icons" if words else "")

    def _selected(self, index, _prev):
        if index.isValid():
            self.name.setText(self.model.names[index.row()])

    def _accept(self):
        index = self.view.currentIndex()
        if index.isValid():
            self.chosen = self.model.names[index.row()]
        self.accept()

    def _reset(self):
        self.chosen = None
        self.accept()
