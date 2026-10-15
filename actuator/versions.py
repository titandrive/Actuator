"""The Restore window: saved versions on the left, what restoring one would change on the right."""

import html
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPalette
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton,
    QSplitter, QTextBrowser, QVBoxLayout, QWidget,
)

from . import icons
from . import config as cfg
from . import diffing
from . import writer
from . import yaml_editor
from .widgets import WrapLabel

COLORS = {"+": "#3a9a5b", "−": "#d24545", "~": "#c58a00"}


def changes_html(groups: dict | None, palette: QPalette) -> str:
    """The +/−/~ before → after table, in the theme's colors."""
    if groups is None:
        return "<p>Your current config can't be read, so the changes can't be listed.</p>"
    if not groups:
        return "<p>Same bindings and device settings as your current config.</p>"
    muted = palette.placeholderText().color().name()
    normal = palette.text().color().name()  # Qt rich text has no "inherit"; use the theme's
    # One flowing line per change: compact when short, wraps under itself when long.
    parts = []
    for group, lines in groups.items():
        parts.append(f"<p style='margin:10px 0 4px'><b>{html.escape(group)}</b></p>")
        for mark, what, before, after in lines:
            parts.append(
                f"<p style='margin:2px 0 2px 26px; text-indent:-18px'>"
                f"<span style='color:{COLORS[mark]}; font-weight:600'>{mark}</span>&nbsp;&nbsp;"
                f"{html.escape(what)}: "
                f"<span style='color:{muted if mark == '+' else normal}'>{html.escape(before)}</span>"
                f" → <span style='color:{muted if mark == '−' else normal}'>{html.escape(after)}</span></p>")
    return "".join(parts)


def auto_label(path: Path) -> str:
    """A version's own name: what it is and when it was made."""
    if path == writer.original_path(cfg.CONFIG_PATH):
        return "Original (before Actuator)"
    try:
        stamp = datetime.strptime(path.stem.removeprefix("config-")[:15], "%Y%m%d-%H%M%S")
    except ValueError:
        return path.name
    kind = "Manual backup" if "-backup" in path.stem else "Auto backup"
    return f"{kind} at {stamp:%b %d, %H:%M:%S}"


def _versions() -> list[tuple[str, Path]]:
    """(label, path), newest first: the name someone gave it in Restore, or its own."""
    paths = writer.snapshots()
    orig = writer.original_path(cfg.CONFIG_PATH)
    if orig.exists():
        paths.append(orig)
    return [(writer.version_name(p) or auto_label(p), p) for p in paths]


class VersionsDialog(QDialog):
    """Pick a version to restore. self.chosen holds its path when accepted."""

    def __init__(self, parent, current: cfg.Config | None):
        super().__init__(parent)
        self.setWindowTitle("Restore")
        self.current = current
        self.chosen: Path | None = None
        self.list = QListWidget()
        # Long names end in "…" instead of making the list scroll sideways.
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.list.currentItemChanged.connect(lambda item, _p: self._preview(item))
        self.list.itemDoubleClicked.connect(lambda *_: self._restore())
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        self.list.itemChanged.connect(self._renamed)
        # Esc while renaming: put the label back. (A committed rename reaches _renamed first.)
        self.list.itemDelegate().closeEditor.connect(lambda *_: QTimer.singleShot(0, self._rename_closed))
        self._renaming: tuple[QListWidgetItem, Path] | None = None
        self.title = WrapLabel()  # a long custom name wraps rather than widening the window
        self.title.setStyleSheet("font-weight: 600;")
        self.view = QTextBrowser()
        self.view.setOpenLinks(False)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(self.title)
        rl.addWidget(self.view, 1)
        split = QSplitter()
        split.addWidget(self.list)
        split.addWidget(right)
        split.setSizes([240, 480])
        self.split = split

        from_file = QPushButton(icons.icon("document-import"), "Restore from file…")
        from_file.clicked.connect(self._from_file)
        open_menu = QMenu(self)
        open_menu.addAction(icons.icon("text-x-script"), "View YAML", self._view)
        open_menu.addAction(icons.icon("document-edit"), "Open in text editor",
                            lambda: self._path() and yaml_editor.open_external(self._path()))
        open_menu.addAction(icons.icon("folder-open"), "Show in folder",
                            lambda: self._path() and yaml_editor.reveal(self._path()))
        self.open = QPushButton(icons.icon("document-open"), "Open")
        self.open.setMenu(open_menu)
        self.lock = QPushButton()
        self.lock.clicked.connect(self._toggle_lock)
        self.delete = QPushButton(icons.icon("edit-delete"), "Delete")
        self.delete.clicked.connect(self._delete)
        self.restore = QPushButton(icons.icon("document-revert"), "Restore")
        self.restore.setDefault(True)
        self.restore.clicked.connect(self._restore)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addWidget(from_file)
        buttons.addWidget(self.open)
        buttons.addWidget(self.lock)
        buttons.addWidget(self.delete)
        buttons.addStretch(1)
        buttons.addWidget(self.restore)
        buttons.addWidget(close)
        layout = QVBoxLayout(self)
        layout.addWidget(split, 1)
        layout.addLayout(buttons)
        self.resize(820, 520)
        self._fill()
        # Wide enough for the version names in this font, so they aren't cut off.
        # (Measured on the automatic names: a long custom name is cut short with "…" instead.)
        labels = [auto_label(path) for _label, path in _versions()] or ["Manual backup at Oct 15, 12:00:00"]
        icon = self.list.iconSize().width() + 8
        names = max(self.list.fontMetrics().horizontalAdvance(t) for t in labels) + icon + \
            2 * self.list.frameWidth() + self.list.verticalScrollBar().sizeHint().width() + 12
        self.split.setSizes([names, max(self.width() - names, 360)])

    def _fill(self, keep: Path | None = None):
        self.list.clear()
        for label, path in _versions():
            try:
                broken = not yaml_editor.check(path.read_text())[0]
            except OSError:
                broken = True
            item = QListWidgetItem(label)
            if label != auto_label(path):  # renamed: the full name, and what and when it was
                item.setToolTip(f"{label}\n{auto_label(path)}")
            if writer.is_locked(path):
                item.setIcon(icons.lucide("lock"))
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            if broken:  # red is enough; the preview explains the error
                item.setForeground(QColor(COLORS["−"]))
                item.setToolTip("This version has a YAML error")
            self.list.addItem(item)
        if not self.list.count():
            self.title.setText("No saved versions yet")
            self.view.setHtml("<p>A version is kept every time you save, so you can come back to it here.</p>")
            self.restore.setEnabled(False)
            self.delete.setEnabled(False)
            self.open.setEnabled(False)
            self.lock.setEnabled(False)
            return
        row = next((i for i in range(self.list.count())
                    if keep and self.list.item(i).data(Qt.ItemDataRole.UserRole) == str(keep)), 0)
        self.list.setCurrentRow(row)

    def _path(self) -> Path | None:
        item = self.list.currentItem()
        return Path(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def _preview(self, item):
        if item is None:  # the list is being refilled
            return
        path = self._path()
        self.restore.setEnabled(path is not None)
        self.open.setEnabled(path is not None)
        self.lock.setEnabled(path is not None)
        if not path:
            return
        locked = writer.is_locked(path)
        self.lock.setText("Unlock" if locked else "Lock")
        self.lock.setIcon(icons.icon("object-unlocked" if locked else "object-locked"))
        self.lock.setToolTip("Locked versions are never deleted until you unlock them" if not locked
                             else "Let this version be deleted again")
        self.delete.setEnabled(not locked)  # locked versions are protected
        self.delete.setToolTip("Unlock it to delete" if locked else "")
        self.title.setText(item.text())
        try:
            text = path.read_text()
        except OSError as e:
            self.view.setHtml(f"<p>Can't open this version: {html.escape(str(e))}</p>")
            return
        ok, message, _line = yaml_editor.check(text)
        if not ok:
            self.view.setHtml(
                "<p><b>This version isn't valid YAML.</b> You can still restore it; Actuator will then "
                "show the error so you can fix it.</p>"
                f"<p style='white-space:pre-wrap; font-family:monospace'>{html.escape(message)}</p>")
            return
        groups = diffing.changes(self.current, cfg.loads(text, path)) if self.current else None
        intro = "<p>Restoring makes these changes to your config:</p>" if groups else ""
        self.view.setHtml(intro + changes_html(groups, self.palette()))

    def _toggle_lock(self):
        path = self._path()
        if path:
            writer.set_locked(path, not writer.is_locked(path))
            self._fill(keep=path)
            # Back to the list: left without focus, the theme draws it dimmed until it's clicked.
            self.list.setFocus()

    def _view(self):
        path = self._path()
        if not path:
            return
        try:
            text = path.read_text()
        except OSError as e:
            QMessageBox.warning(self, "Actuator", f"Couldn't open it.\n\n{e}")
            return
        yaml_editor.YamlEditor(self, path, text, title=f"View YAML – {self.title.text()}", read_only=True).exec()

    def _restore(self):
        path = self._path()
        if path:
            self.chosen = path
            self.accept()

    def _delete(self):
        path = self._path()
        if not path or writer.is_locked(path):
            return
        is_original = path == writer.original_path(cfg.CONFIG_PATH)
        warning = ("This is your config from before you started using Actuator. "
                   if is_original else "")
        answer = QMessageBox.warning(
            self, "Delete this version?",
            f"{warning}Delete “{self.title.text()}” permanently? This can't be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        row = self.list.currentRow()
        try:
            path.unlink()
        except OSError as e:
            QMessageBox.warning(self, "Actuator", f"Couldn't delete it.\n\n{e}")
            return
        writer.set_version_name(path, None)
        self._fill()
        if self.list.count():
            self.list.setCurrentRow(min(row, self.list.count() - 1))
            self.list.setFocus()

    def _menu(self, pos):
        item = self.list.itemAt(pos)
        if not item:
            return
        self.list.setCurrentItem(item)
        path = self._path()
        locked = writer.is_locked(path)
        menu = QMenu(self)
        menu.addAction(icons.icon("edit-rename"), "Rename", self._rename)
        menu.addAction(icons.icon("object-unlocked" if locked else "object-locked"),
                       "Unlock" if locked else "Lock", self._toggle_lock)
        delete = menu.addAction(icons.icon("edit-delete"), "Delete", self._delete)
        delete.setEnabled(not locked)  # locked versions are protected
        menu.exec(self.list.viewport().mapToGlobal(pos))

    def _rename(self):
        item, path = self.list.currentItem(), self._path()
        if not item or not path:
            return
        self.list.blockSignals(True)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        self.list.blockSignals(False)
        self._renaming = (item, path)
        self.list.editItem(item)

    def _renamed(self, item):
        if not self._renaming or self._renaming[0] is not item:
            return
        path = self._renaming[1]
        self._renaming = None
        text = item.text().strip()
        writer.set_version_name(path, None if text == auto_label(path) else text)  # empty: its own name
        self._fill(keep=path)
        self.list.setFocus()

    def _rename_closed(self):
        if self._renaming:
            path = self._renaming[1]
            self._renaming = None
            self._fill(keep=path)

    def _from_file(self):
        source, _ = QFileDialog.getOpenFileName(self, "Restore from file", str(Path.home()), "YAML (*.yaml *.yml)")
        if source:
            self.chosen = Path(source)
            self.accept()
