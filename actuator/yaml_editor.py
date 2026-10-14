"""Editing config.yaml directly, with live YAML checking."""

import subprocess

from PySide6.QtCore import QFileSystemWatcher, QPointF, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFontDatabase, QIcon, QPainter, QTextCursor
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout,
)
from ruamel.yaml import YAML


def _code(line: str) -> str:
    """A line without its comment (a # outside quotes)."""
    out, quote = [], None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"" and (i == 0 or line[i - 1] in " :-[{,"):
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] == " "):
            break
        out.append(ch)
    return "".join(out).rstrip()


def _spaces(n: int) -> str:
    return f"{n} space" + ("" if n == 1 else "s")


def _lint(lines: list[str]) -> tuple[int, str] | None:
    """Find the likely culprit line for common mistakes the parser reports badly.
    Returns (0-based line, plain-language message) or None."""
    stack = [0]  # indents of the open blocks
    opens = False  # previous line ends with ':' (or '- key:'), so the next may indent further
    item_content = None  # indent of a list item's content ("- id:" at 2 → 4)
    for n, raw in enumerate(lines):
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            return n, "This line is indented with a tab. YAML only allows spaces."
        code = _code(raw)
        if not code.strip():
            continue
        stripped = code.lstrip()
        indent = len(code) - len(stripped)
        for q in ("'", '"'):
            if code.count(q) % 2 and stripped.count(q) % 2 and not stripped.startswith(("#",)):
                return n, f"This line has an unclosed {q} quote."
        if stripped.count("[") != stripped.count("]") or stripped.count("{") != stripped.count("}"):
            return n, "A [ ] or { } on this line isn't closed."
        if indent > stack[-1]:
            if not opens and indent != item_content:
                return n, (f"This line is indented more than the line above ({_spaces(indent)} vs "
                           f"{stack[-1]}), but the line above doesn't start a new block. "
                           "Remove the extra spaces.")
            stack.append(indent)
        elif indent < stack[-1]:
            levels = stack.copy()  # before popping, so the suggestion can name the level it missed
            while stack and stack[-1] > indent:
                stack.pop()
            if not stack or stack[-1] != indent:
                valid = sorted(set(levels + ([item_content] if item_content else [])))
                # On a tie, the deeper level is the likelier intent (a sibling of the line above).
                nearest = min(valid, key=lambda v: (abs(v - indent), -v)) if valid else 0
                return n, (f"This line's indentation doesn't line up: it has {_spaces(indent)}, "
                           f"but lines at this level use {nearest}.")
        is_item = stripped.startswith("- ") or stripped == "-"
        body = stripped[2:].lstrip() if is_item else stripped
        if is_item:
            item_content = indent + 2 + (len(stripped[2:]) - len(stripped[2:].lstrip()))
            if indent + 2 not in stack:
                stack.append(item_content)
        if not is_item and ":" not in body and not body.startswith(("[", "{")) and not opens_scalar(lines, n):
            return n, "This line is missing a ':' after its key."
        opens = body.endswith(":")
    return None


def opens_scalar(lines, n) -> bool:
    """A line that's a plain list value or continuation, where no ':' is expected."""
    prev = next((_code(l) for l in reversed(lines[:n]) if _code(l).strip()), "")
    return prev.rstrip().endswith(("|", ">", "|-", ">-"))


def check(text: str) -> tuple[bool, str, int | None]:
    """(ok, message, 1-based error line or None)."""
    try:
        data = YAML(typ="safe").load(text)
    except Exception as e:  # noqa: BLE001
        lines = text.splitlines()
        found = _lint(lines)
        if found:
            n, msg = found
        else:
            mark = getattr(e, "problem_mark", None) or getattr(e, "context_mark", None)
            n = mark.line if mark else 0
            problem = str(getattr(e, "problem", "") or e)
            msg = FRIENDLY.get(next((k for k in FRIENDLY if k in problem), ""),
                               f"{problem[:1].upper()}{problem[1:]}. The mistake may be on this line or the one above.")
        quoted = lines[n].strip() if n < len(lines) else ""
        return False, f"Line {n + 1}: {msg}" + (f"\n    {quoted}" if quoted else ""), n + 1
    if data is not None and not isinstance(data, dict):
        return False, "The top level must be a mapping (keys like mouse:, touchpad:).", None
    return True, "Valid YAML", None


# Parser messages, in plain language, for when the lint above can't pin the line down.
FRIENDLY = {
    "could not find expected ':'": "A key is missing its ':' (on this line or the one above).",
    "did not find expected key": "The indentation or structure doesn't line up around here. "
                                 "Check this line and the one above.",
    "expected <block end>": "The indentation doesn't line up around here. Check this line and the one above.",
    "mapping values are not allowed here": "Unexpected ':'. If a value contains ': ', put it in quotes.",
    "did not find expected ',' or ']'": "A [ list ] isn't closed, or its items aren't separated by commas.",
    "did not find expected ',' or '}'": "A { } isn't closed, or its items aren't separated by commas.",
    "found unexpected end of stream": "A quote or bracket is never closed.",
    "tab character": "A tab is used for indentation. YAML only allows spaces.",
    "did not find expected node content": "Something is missing here, often a value after '-' or ':'.",
}


INDENT = 2  # Actuator writes 2-space YAML


def _indent(text: str) -> int | None:
    """Leading spaces, or None for a blank line."""
    return len(text) - len(text.lstrip(" ")) if text.strip() else None


class CodeEdit(QPlainTextEdit):
    """A plain text editor that draws VS Code-style indent guides."""

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self.viewport())
        color = self.palette().placeholderText().color()
        color.setAlpha(70)
        painter.setPen(color)
        space = self.fontMetrics().horizontalAdvance(" ")
        offset = self.contentOffset()
        left = offset.x() + self.document().documentMargin()
        block = self.firstVisibleBlock()
        prev = _indent(block.previous().text()) if block.previous().isValid() else 0
        bottom = self.viewport().height()
        while block.isValid():
            geom = self.blockBoundingGeometry(block).translated(offset)
            if geom.top() > bottom:
                break
            indent = _indent(block.text())
            if indent is None:  # blank: continue the guides of the block it sits in
                nxt = block.next()
                while nxt.isValid() and _indent(nxt.text()) is None:
                    nxt = nxt.next()
                after = _indent(nxt.text()) if nxt.isValid() else 0
                indent = min(prev or 0, after or 0)
            else:
                prev = indent
            for level in range(0, indent, INDENT):
                x = round(left + level * space) + 0.5
                painter.drawLine(QPointF(x, geom.top()), QPointF(x, geom.bottom()))
            block = block.next()
        painter.end()


class YamlEditor(QDialog):
    def __init__(self, parent, path, text: str):
        super().__init__(parent)
        self.setWindowTitle(f"Edit {path.name}")
        self.original = text
        self.edit = CodeEdit(text)
        self.edit.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.edit.setTabStopDistance(self.edit.fontMetrics().horizontalAdvance(" ") * 2)
        self.status = QLabel()
        self.status.setWordWrap(True)
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
        self.path = path
        open_ext = QPushButton(QIcon.fromTheme("document-edit"), "Open in text editor")
        open_ext.setToolTip("Open config.yaml in your default editor for YAML files")
        open_ext.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))))
        reveal = QPushButton(QIcon.fromTheme("folder-open"), "Show in folder")
        reveal.clicked.connect(self._reveal)
        self.buttons.addButton(open_ext, QDialogButtonBox.ButtonRole.ActionRole)
        self.buttons.addButton(reveal, QDialogButtonBox.ButtonRole.ActionRole)
        # Edits made in another editor while this is open: reload them, or warn before overwriting.
        self.watcher = QFileSystemWatcher([str(path)], self)
        self.watcher.fileChanged.connect(lambda _p: QTimer.singleShot(200, self._disk_changed))
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

    def _reveal(self):
        """Open the file manager with the file selected (Dolphin and others support this)."""
        ok = subprocess.run(
            ["qdbus6", "org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
             "org.freedesktop.FileManager1.ShowItems", QUrl.fromLocalFile(str(self.path)).toString(), ""],
            capture_output=True, timeout=5).returncode == 0
        if not ok:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.path.parent)))

    def _disk_changed(self):
        if str(self.path) not in self.watcher.files() and self.path.exists():
            self.watcher.addPath(str(self.path))  # editors that replace the file drop the watch
        try:
            disk = self.path.read_text()
        except OSError:
            return
        if disk == self.original:
            return
        if self.text() == self.original:  # nothing edited here: take the new version
            self.original = disk
            self.edit.setPlainText(disk)
            self._timer.stop()
            self._validate()
            self.status.setText("Reloaded: the file was changed in another editor.")
        else:
            self.original = disk
            self._validate()
            self.status.setText("⚠ The file was changed in another editor. Saving here will overwrite "
                                "those changes.")
            self.status.setStyleSheet("color: #d24545;")

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
