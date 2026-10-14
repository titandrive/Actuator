"""The Restore window: saved versions on the left, what restoring one would change on the right."""

import html
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
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


def _versions() -> list[tuple[str, Path]]:
    out = []
    for snap in writer.snapshots():
        try:
            stamp = datetime.strptime(snap.stem.removeprefix("config-"), "%Y%m%d-%H%M%S")
            out.append((f"Before save at {stamp:%b %d, %H:%M:%S}", snap))
        except ValueError:
            out.append((snap.name, snap))
    orig = writer.original_path(cfg.CONFIG_PATH)
    if orig.exists():
        out.append(("Original (before Actuator)", orig))
    return out


class VersionsDialog(QDialog):
    """Pick a version to restore. self.chosen holds its path when accepted."""

    def __init__(self, parent, current: cfg.Config | None):
        super().__init__(parent)
        self.setWindowTitle("Restore")
        self.current = current
        self.chosen: Path | None = None
        self.list = QListWidget()
        self.list.currentItemChanged.connect(lambda item, _p: self._preview(item))
        self.list.itemDoubleClicked.connect(lambda *_: self._restore())
        self.title = QLabel()
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
        note = QLabel("Restoring keeps your current config as a new version first, so it can be undone.")
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(placeholder-text);")
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
        layout.addWidget(note)
        layout.addLayout(buttons)
        self.resize(820, 520)
        self._fill()

    def _fill(self, keep: Path | None = None):
        self.list.clear()
        for label, path in _versions():
            try:
                broken = not yaml_editor.check(path.read_text())[0]
            except OSError:
                broken = True
            item = QListWidgetItem(label)
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
        answer = QMessageBox.question(
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
        self._fill()
        if self.list.count():
            self.list.setCurrentRow(min(row, self.list.count() - 1))

    def _from_file(self):
        source, _ = QFileDialog.getOpenFileName(self, "Restore from file", str(Path.home()), "YAML (*.yaml *.yml)")
        if source:
            self.chosen = Path(source)
            self.accept()
