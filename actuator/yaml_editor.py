"""Editing config.yaml directly, with live YAML checking."""

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFontDatabase, QTextCursor
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout,
)
from ruamel.yaml import YAML
from ruamel.yaml.error import MarkedYAMLError


def check(text: str) -> tuple[bool, str, int | None]:
    """(ok, message, 1-based error line or None)."""
    try:
        data = YAML(typ="safe").load(text)
    except MarkedYAMLError as e:
        mark = e.problem_mark or e.context_mark
        line = mark.line + 1 if mark else None
        return False, f"Line {line}: {e.problem or e.context}" if line else str(e), line
    except Exception as e:  # noqa: BLE001
        return False, str(e), None
    if data is not None and not isinstance(data, dict):
        return False, "The top level must be a mapping (keys like mouse:, touchpad:).", None
    return True, "Valid YAML", None


class YamlEditor(QDialog):
    def __init__(self, parent, path, text: str):
        super().__init__(parent)
        self.setWindowTitle(f"Edit {path.name}")
        self.original = text
        self.edit = QPlainTextEdit(text)
        self.edit.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.edit.setTabStopDistance(self.edit.fontMetrics().horizontalAdvance(" ") * 2)
        self.status = QLabel()
        self.go_to = QPushButton("Go to error")
        self.go_to.clicked.connect(self._go_to_error)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self._cancel)
        note = QLabel("Saving writes the file, reloads InputActions, and keeps the previous version in "
                      "Backup → Recent versions. Actuator reorganizes the file the next time it saves.")
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(placeholder-text);")
        row = QHBoxLayout()
        row.addWidget(self.status, 1)
        row.addWidget(self.go_to)
        layout = QVBoxLayout(self)
        layout.addWidget(self.edit, 1)
        layout.addLayout(row)
        layout.addWidget(note)
        layout.addWidget(self.buttons)
        self.resize(820, 760)
        self._error_line = None
        self._timer = QTimer(self, singleShot=True, interval=300)
        self._timer.timeout.connect(self._validate)
        self.edit.textChanged.connect(self._timer.start)
        self._validate()

    def text(self) -> str:
        return self.edit.toPlainText()

    def _validate(self):
        ok, message, line = check(self.text())
        self._error_line = line
        changed = self.text() != self.original
        self.status.setText(message if ok or not changed else f"⚠ {message}")
        self.status.setStyleSheet("" if ok else "color: #d24545;")
        self.go_to.setVisible(line is not None)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(ok and changed)

    def _go_to_error(self):
        if self._error_line:
            cursor = QTextCursor(self.edit.document().findBlockByLineNumber(self._error_line - 1))
            self.edit.setTextCursor(cursor)
            self.edit.setFocus()

    def _cancel(self):
        from PySide6.QtWidgets import QMessageBox

        if self.text() != self.original:
            answer = QMessageBox.question(self, "Discard changes?", "Close without saving your edits?",
                                          QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
            if answer != QMessageBox.StandardButton.Discard:
                return
        self.reject()
