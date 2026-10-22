"""Editing a single binding: When / Only if / Do forms, with raw YAML as the fallback."""

import re

from PySide6.QtCore import QEvent, QProcess, Qt, Signal
from PySide6.QtGui import QFontDatabase, QIcon
from PySide6.QtWidgets import (
    QSizePolicy,
    QAbstractSpinBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QStackedWidget, QToolButton, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)
from ruamel.yaml import YAML

from . import icons
from . import apps
from . import conditions as conds
from . import config as cfg
from . import describe as d
from . import devices
from . import keys
from . import pause
from .widgets import Section, ToggleRow, WrapLabel, label_width, scroll_past, tidy_form

# Trigger types each device section supports.
GESTURE_TYPES = {
    "mouse": ["press", "wheel", "swipe", "stroke", "circle"],
    "keyboard": ["shortcut"],
    "touchpad": ["swipe", "pinch", "rotate", "hold", "tap", "click", "circle"],  # strokes need one finger, which is pointing
    "touchscreen": ["swipe", "pinch", "rotate", "hold", "tap", "circle", "stroke"],
    "pointer": ["hover"],
}
DIRECTIONS = {
    "swipe": ["up", "down", "left", "right", "left_right", "up_down", "any"],
    "pinch": ["in", "out", "any"],
    "rotate": ["clockwise", "counterclockwise", "any"],
    "circle": ["clockwise", "counterclockwise", "any"],
    "wheel": ["up", "down", "left", "right", "up_down", "left_right", "any"],
}
FINGER_TYPES = {"swipe", "pinch", "rotate", "hold", "tap", "click", "circle", "stroke"}
FINGER_SECTIONS = {"touchpad", "touchscreen"}
# Finger counts InputActions allows for each gesture (from its wiki), and where a new one starts:
# two to pinch or rotate, three for the rest so they don't fight with pointing, scrolling,
# clicking and right-clicking.
FINGER_RANGE = {
    "touchpad": {"swipe": (1, 4), "pinch": (2, 4), "rotate": (2, 4), "hold": (1, 4), "tap": (1, 5),
                 "click": (1, 5), "circle": (1, 4), "stroke": (1, 4)},
    "touchscreen": {"swipe": (1, 5), "pinch": (2, 5), "rotate": (2, 5), "hold": (1, 5), "tap": (1, 5),
                    "circle": (1, 5), "stroke": (1, 5)},
}
FINGER_DEFAULT = {"pinch": 2, "rotate": 2}
# Motion gestures that a single finger would take over from ordinary pointing and touching.
MOTION_TYPES = {"swipe", "stroke", "circle"}
# A mouse motion gesture with no held button would catch every movement of the mouse. A button
# pressed and released without moving still clicks normally (InputActions replays it).
MOUSE_HOLD_BUTTONS = ["right", "middle", "back", "forward", "task", "side", "extra"]
MOUSE_HOLD_DEFAULT = ["right"]
# Motion triggers can require being done fast or slow, so one gesture can do two things.
SPEED_TYPES = {"swipe", "pinch", "rotate", "circle", "stroke", "wheel"}
SPEEDS = [(None, "Any"), ("fast", "Fast"), ("slow", "Slow")]
# How far a gesture must go before it counts, in what its trigger measures. Press and tap
# style triggers start at once, so a threshold means nothing to them here.
THRESHOLD_UNITS = {"hold": " ms", "swipe": "", "stroke": "", "wheel": "", "circle": "°", "rotate": "°",
                   "pinch": ""}
THRESHOLD_HINTS = {"hold": "How long to hold before it fires.",
                   "circle": "How far round to go before it counts.",
                   "rotate": "How far to turn before it counts.",
                   "pinch": "How much to pinch before it counts (0.1 is a small pinch)."}
LOCK_POINTER_TYPES = {"swipe", "stroke", "circle"}  # mouse only: other devices don't move the pointer


def finger_range(section: str, trigger_type: str) -> tuple[int, int]:
    return FINGER_RANGE.get(section, {}).get(trigger_type, (1, 5))


def default_fingers(trigger_type: str) -> int:
    return FINGER_DEFAULT.get(trigger_type, 3)

MODES = ["Press keys", "Hold keys", "Click mouse button", "Type text", "Run command", "Plasma shortcut",
         "Custom (YAML)"]
CLICK_BUTTONS = ["left", "right", "middle", "back", "forward", "task", "side", "extra"]
TAP_STAGES = [("begin", "When it starts"), ("end", "When it ends")]
END_ONLY = {"stroke"}  # InputActions only runs these triggers' actions once they end


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "binding"


def unique_id(config: cfg.Config, base: str) -> str:
    taken = {b.id for b in config.bindings}
    if base not in taken:
        return base
    n = 2
    while f"{base}_{n}" in taken:
        n += 1
    return f"{base}_{n}"


def scope_conditions(config: cfg.Config, scope: str, app: str | None):
    return config.app_conditions(app) if scope == "app" else None


def default_stage(trigger_type: str) -> str:
    return "end" if trigger_type in ("tap", "click") or trigger_type in END_ONLY else "begin"


def end_only_actions(actions) -> list | None:
    """Actions moved to 'on: end' for triggers that allow nothing else, or None if already fine.
    Held keys can't be held there, so they become a press."""
    actions = list(actions or [])
    mode, values = detect_mode(actions)
    if mode == "Hold keys":
        return build_actions("Press keys", {"on": "end", "keys": values["keys"]})
    if all(not isinstance(a, dict) or str(a.get("on", "end")) == "end" for a in actions):
        return None
    return [dict(a, on="end") if isinstance(a, dict) and str(a.get("on", "end")) in ("begin", "update")
            else a for a in actions]


def new_button_binding(config: cfg.Config, button: cfg.Button, scope: str, app: str | None) -> tuple[str, dict]:
    where = app or "global"
    data = {"id": unique_id(config, f"{slug(where)}_{slug(button.name)}")}
    if button.kind == "mouse":
        section = "mouse"
        data.update(type="press", mouse_buttons=[button.src], instant=True)
    else:
        section = "keyboard"
        data.update(type="shortcut", shortcut=button.src.split("+"), clear_modifiers=True)
    if conds := scope_conditions(config, scope, app):
        data["conditions"] = conds
    data["actions"] = [{"on": "begin", "input": [{"keyboard": []}]}]
    return section, data


def new_gesture_binding(config: cfg.Config, section: str, trigger_type: str, scope: str, app: str | None) -> dict:
    where = app or "global"
    data = {"id": unique_id(config, f"{slug(where)}_{section}_{trigger_type}"), "type": trigger_type}
    if section in FINGER_SECTIONS and trigger_type in FINGER_TYPES:
        data["fingers"] = default_fingers(trigger_type)
    if trigger_type in DIRECTIONS:
        data["direction"] = DIRECTIONS[trigger_type][0]
    if trigger_type == "shortcut":
        data["shortcut"] = ["leftmeta", "a"]
        data["clear_modifiers"] = True
    if trigger_type == "press":
        data["mouse_buttons"] = ["middle"]
    if section == "mouse" and trigger_type in MOTION_TYPES:
        data["mouse_buttons"] = list(MOUSE_HOLD_DEFAULT)
    if conds := scope_conditions(config, scope, app):
        data["conditions"] = conds
    data["actions"] = [{"on": default_stage(trigger_type), "input": [{"keyboard": []}]}]
    return data


def _keyboard_tokens(action: dict) -> list[str] | None:
    """Tokens if this action is nothing but keyboard input, else None."""
    if set(action) - {"on", "input"}:
        return None
    toks = []
    for item in action.get("input") or []:
        if not isinstance(item, dict) or set(item) != {"keyboard"}:
            return None
        v = item["keyboard"]
        for t in v if isinstance(v, list) else [v]:
            if not isinstance(t, str):
                return None
            toks.append(t)
    return toks


def detect_mode(actions) -> tuple[str, dict]:
    """Which simple form can show these actions, and its values. Custom if none fits."""
    actions = list(actions or [])
    if len(actions) == 1 and isinstance(actions[0], dict):
        a = actions[0]
        on = str(a.get("on", "end"))
        toks = _keyboard_tokens(a)
        if toks is not None and on in ("begin", "end") and not any(t[:1] in "+-" and len(t) > 1 for t in toks):
            return "Press keys", {"on": on, "keys": ", ".join(d.combo(t) for t in toks)}
        items = a.get("input") if set(a) <= {"on", "input"} else None
        if isinstance(items, list) and len(items) == 1 and isinstance(items[0], dict) and len(items[0]) == 1:
            (device, toks), = items[0].items()
            toks = toks if isinstance(toks, list) else [toks]
            if device == "mouse" and len(toks) == 1 and str(toks[0]) in CLICK_BUTTONS:
                return "Click mouse button", {"on": on, "value": str(toks[0])}
            if (device == "keyboard" and len(toks) == 1 and isinstance(toks[0], dict)
                    and set(toks[0]) == {"text"} and isinstance(toks[0]["text"], str)):
                return "Type text", {"on": on, "value": toks[0]["text"]}
        if set(a) == {"on", "command"} or set(a) == {"command"}:
            return "Run command", {"on": on, "value": str(a["command"])}
        if set(a) == {"on", "plasma_shortcut"} or set(a) == {"plasma_shortcut"}:
            return "Plasma shortcut", {"on": on, "value": str(a["plasma_shortcut"])}
    if len(actions) == 2 and all(isinstance(a, dict) for a in actions):
        down, up = actions
        td, tu = _keyboard_tokens(down), _keyboard_tokens(up)
        if (td and tu and str(down.get("on")) == "begin" and str(up.get("on")) in ("end_cancel", "end")
                and all(t.startswith("+") for t in td) and [t.replace("-", "+", 1) for t in tu] == td):
            return "Hold keys", {"keys": ", ".join(d.combo(t[1:]) for t in td)}
    if not actions:
        return "Press keys", {"on": "begin", "keys": ""}
    return "Custom (YAML)", {}


def build_actions(mode: str, values: dict) -> list:
    if mode == "Press keys":
        return [{"on": values["on"], "input": [{"keyboard": keys.parse_sequence(values["keys"])}]}]
    if mode == "Hold keys":
        held = keys.parse_sequence(values["keys"])
        return [
            {"on": "begin", "input": [{"keyboard": [f"+{k}" for k in held]}]},
            {"on": "end_cancel", "input": [{"keyboard": [f"-{k}" for k in reversed(held)]}]},
        ]
    if mode == "Click mouse button":
        return [{"on": values["on"], "input": [{"mouse": [values["value"]]}]}]
    if mode == "Type text":
        if not values["value"]:
            raise ValueError("Enter the text to type.")
        return [{"on": values["on"], "input": [{"keyboard": [{"text": values["value"]}]}]}]
    if mode == "Run command":
        if not values["value"].strip():
            raise ValueError("Enter a command.")
        return [{"on": values["on"], "command": values["value"].strip()}]
    if mode == "Plasma shortcut":
        if "," not in values["value"]:
            raise ValueError("Use component,shortcut — for example kwin,Overview.")
        return [{"on": values["on"], "plasma_shortcut": values["value"].strip()}]
    raise ValueError(f"Unknown mode {mode}")


class KeyField(QLineEdit):
    """A text field that can also record key presses as combos."""

    recorded = Signal()

    def __init__(self):
        super().__init__()
        self.recording = False
        self.single = False  # record one combo, replacing the text, instead of appending
        self._lone = None  # modifier pressed with nothing else yet

    def start(self):
        self.recording = True
        self._first = True
        self.setFocus()
        # The red border switches Qt to stylesheet drawing, which is smaller: hold the size.
        self._size_limits = (self.minimumSize(), self.maximumSize())
        self.setFixedSize(self.size())
        self.setStyleSheet("QLineEdit { border: 2px solid #d24545; }")

    def stop(self):
        self.recording = False
        self._lone = None
        self.setStyleSheet("")
        if getattr(self, "_size_limits", None):
            self.setMinimumSize(self._size_limits[0])
            self.setMaximumSize(self._size_limits[1])
            self._size_limits = None

    def _add(self, combo: str):
        label = d.combo(combo)
        if self.single or self._first or not self.text().strip():
            self.setText(label)
        else:
            self.setText(f"{self.text().rstrip(', ')}, {label}")
        self._first = False
        self.recorded.emit()

    def event(self, event):
        # Qt uses Tab to move to the next field before a key press is seen; while recording, it's a key.
        if self.recording and event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Tab,
                                                                                      Qt.Key.Key_Backtab):
            self.keyPressEvent(event)
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        if not self.recording:
            return super().keyPressEvent(event)
        if event.isAutoRepeat():
            return
        combo = keys.from_key_event(event)
        if combo:
            self._lone = None
            self._add(combo)
        else:
            self._lone = keys.LONE_MODIFIERS.get(Qt.Key(event.key()))

    def keyReleaseEvent(self, event):
        if not self.recording:
            return super().keyReleaseEvent(event)
        if self._lone and keys.LONE_MODIFIERS.get(Qt.Key(event.key())) == self._lone:
            self._add(self._lone)
        self._lone = None


class KeyList(QDialog):
    """Every key name you can type. Click keys to build a combo, then Save."""

    def __init__(self, field: "KeyField", parent=None):
        super().__init__(parent)
        self.field = field
        self.setWindowTitle("Keys")
        self.preview = QLineEdit(field.text())
        self.preview.setPlaceholderText("Click keys below")
        self.search = QLineEdit(placeholderText="Search keys")
        self.search.textChanged.connect(self._filter)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Key", "Name in config"])
        for group, names in keys.KEY_GROUPS.items():
            top = QTreeWidgetItem([group])
            top.setFlags(Qt.ItemFlag.ItemIsEnabled)
            for name in names:
                try:
                    raw = keys.to_name(name)
                except ValueError:
                    raw = name.lower()
                top.addChild(QTreeWidgetItem([name, raw]))
            self.tree.addTopLevelItem(top)
            top.setExpanded(True)
        self.tree.resizeColumnToContents(0)
        self.tree.itemClicked.connect(self._insert)
        self.then_button = QPushButton("Then…")
        self.then_button.setToolTip("Start the next combo in a sequence, like Ctrl+C, then Ctrl+V")
        self.then_button.clicked.connect(self._then)
        self.then_button.setVisible(not field.single)
        clear = QPushButton("Clear")
        clear.clicked.connect(self.preview.clear)
        tools = QHBoxLayout()
        tools.addWidget(self.then_button)
        tools.addWidget(clear)
        tools.addStretch(1)
        hint = QLabel("Click keys to build a combo: Ctrl, then T makes Ctrl+T."
                      + ("" if field.single else " Use Then… to add another combo after it."))
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(placeholder-text);")
        self.error = QLabel()
        self.error.setStyleSheet("color: #d24545;")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.preview)
        layout.addLayout(tools)
        layout.addWidget(self.search)
        layout.addWidget(self.tree)
        layout.addWidget(hint)
        layout.addWidget(self.error)
        layout.addWidget(buttons)
        self.resize(360, 560)

    def _filter(self, text: str):
        text = text.lower()
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            shown = 0
            for j in range(top.childCount()):
                child = top.child(j)
                match = not text or text in child.text(0).lower() or text in child.text(1)
                child.setHidden(not match)
                shown += match
            top.setHidden(shown == 0)

    def _then(self):
        if self.preview.text().strip():
            self.preview.setText(self.preview.text().rstrip(", ") + ", ")

    def _insert(self, item, _col):
        if item.childCount() or not (item.flags() & Qt.ItemFlag.ItemIsSelectable):
            return
        name, text = item.text(0), self.preview.text()
        if not text.strip():
            text = name
        elif text.endswith(", "):
            text += name
        else:
            text += f"+{name}"
        self.preview.setText(text)

    def _save(self):
        text = self.preview.text().strip().rstrip(",")
        try:
            keys.parse_sequence(text)
        except ValueError as e:
            self.error.setText(str(e))
            return
        self.field.setText(text)
        self.field.recorded.emit()
        self.accept()


def load_yaml(text: str):
    return YAML(typ="safe").load(text)


# record-stroke prints the stroke alone on a line, as base64 (maybe quoted). A straight line is only ~12 characters.
STROKE_LINE = re.compile(r"^[A-Za-z0-9+/]{4,}={0,2}$")


def parse_stroke(output: str) -> str | None:
    # Newer versions print it quoted, as in YAML: 'PycA…'.
    lines = [line.strip().strip("'\"") for line in output.splitlines()]
    lines = [line for line in lines if STROKE_LINE.match(line)]
    return lines[-1] if lines else None


class StrokeRecorder(QDialog):
    """Runs `inputactions record-stroke` and captures the stroke it prints."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Record stroke")
        self.setFixedWidth(420)
        self.stroke: str | None = None
        self.heading = QLabel()
        font = self.heading.font()
        font.setPointSizeF(font.pointSizeF() * 1.15)
        font.setBold(True)
        self.heading.setFont(font)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setStyleSheet("color: palette(placeholder-text);")
        # InputActions' own output can be long, with lines that don't wrap: keep it in a small box.
        self.details = QPlainTextEdit(readOnly=True)
        self.details.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.details.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.details.setFixedHeight(120)
        self.details.hide()
        self.retry = QPushButton(icons.icon("media-record"), "Try again")
        self.retry.clicked.connect(self._start)
        self.cancel = QPushButton("Cancel")
        self.cancel.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.retry)
        buttons.addWidget(self.cancel)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)
        layout.addWidget(self.heading)
        layout.addWidget(self.info)
        layout.addWidget(self.details)
        layout.addSpacing(4)
        layout.addLayout(buttons)
        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.proc.finished.connect(self._finished)
        self.proc.errorOccurred.connect(self._error)
        self._start()

    def _show(self, heading: str, info: str, details: str = "", failed: bool = False):
        self.heading.setText(heading)
        self.info.setText(info)
        self.details.setPlainText(details)
        self.details.setVisible(bool(details))
        self.retry.setVisible(failed)
        self.cancel.setText("Close" if failed else "Cancel")
        self.layout().activate()
        self.setFixedHeight(self.sizeHint().height())

    def _start(self):
        self._show("Draw the shape now",
                   "Move the mouse in the shape, or draw it on the touchscreen. Recording stops "
                   "a moment after you stop moving.")
        self.proc.start("inputactions", ["record-stroke"])

    def _error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self._show("Couldn't record", "The inputactions command couldn't be run.", failed=True)

    def _finished(self, *_):
        out = re.sub(r"\x1b\[[0-9;]*m", "", bytes(self.proc.readAll()).decode(errors="replace")).strip()
        stroke = parse_stroke(out)
        if stroke:
            self.stroke = stroke
            self.accept()
        elif "valid configuration" in out:
            self._show("Couldn't record",
                       "InputActions can only record while the saved config has no errors. "
                       "Fix the problem it reported and save, then try again.", failed=True)
        else:
            self._show("No stroke was recorded", "InputActions said:" if out else "Try drawing it again.",
                       out, failed=True)

    def reject(self):
        if self.proc.state() != QProcess.ProcessState.NotRunning:
            self.proc.kill()
            self.proc.waitForFinished(1000)
        super().reject()


def shortcut_clash(config: cfg.Config, combo: str) -> str | None:
    """Why this combo can't be a keyboard shortcut here, or None. InputActions only starts a
    shortcut whose first key is a modifier, so a lone key never fires; and a combo one of the
    mouse's buttons sends belongs to that button, on the Mouse tab."""
    if combo.split("+")[0] not in keys.MODIFIER_ORDER:
        return f"Must start with Ctrl, Shift, Alt or Meta. Try Meta+{d.combo(combo)}."
    button = next((b for b in config.buttons if b.src == combo), None)
    if button:
        name = config.button_names.get(combo)
        who = f"Your {name} button" if name else "A mouse button"
        return f"{who} sends this. Bind it on the Mouse tab."
    return None


def new_shortcut_binding(config: cfg.Config, combo: str, scope: str, app: str | None) -> dict:
    data = new_gesture_binding(config, "keyboard", "shortcut", scope, app)
    data["id"] = unique_id(config, f"{slug(app or 'global')}_{slug(combo or 'shortcut')}")
    data["shortcut"] = combo.split("+") if combo else []  # empty until its keys are recorded
    return data


def record_stroke(parent) -> str | None:
    dialog = StrokeRecorder(parent)
    return dialog.stroke if dialog.exec() else None


class ConditionBuilder(QWidget):
    """Rows of [what] [how] [value] that edit a binding's conditions. The app condition,
    when there is one, is shown locked: it comes from where the binding lives."""

    changed = Signal()

    def __init__(self):
        super().__init__()
        self.data = None
        self.app = None
        self.rows: list[tuple] = []
        self.locked = QLabel()
        self.mode = QComboBox()
        for m, label in (("all", "all of these"), ("any", "any of these"), ("none", "none of these")):
            self.mode.addItem(label, m)
        self.mode.currentIndexChanged.connect(lambda _i: (self._write(), self._update_summary()))
        self.mode_row = QWidget()
        mr = QHBoxLayout(self.mode_row)
        mr.setContentsMargins(0, 0, 0, 0)
        self.mode_prefix = QLabel("Match")
        mr.addWidget(self.mode_prefix)
        mr.addWidget(self.mode)
        mr.addStretch(1)
        self.rows_box = QVBoxLayout()
        add = QPushButton(icons.icon("list-add"), "Add condition")
        add.clicked.connect(self._add)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("color: palette(placeholder-text);")
        self.too_complex = QLabel("These conditions are too complex for the editor (for example, more than "
                                  "one app or nested groups). Edit them with the YAML button above.")
        self.too_complex.setWordWrap(True)
        self.form = QWidget()
        fl = QVBoxLayout(self.form)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.addWidget(self.locked)
        fl.addWidget(self.mode_row)
        fl.addLayout(self.rows_box)
        fl.addWidget(add, 0, Qt.AlignmentFlag.AlignLeft)
        fl.addWidget(self.summary)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.form)
        layout.addWidget(self.too_complex)

    def load(self, data: dict, where: str):
        self.data, self.where = data, where
        parts = conds.split(data.get("conditions"))
        self.form.setVisible(parts is not None)
        self.too_complex.setVisible(parts is None)
        if parts is None:
            return
        self.app, mode, self.rows = parts[0], parts[1], list(parts[2])
        # The app condition isn't shown: the sidebar already says which app this is for, and it's
        # kept as is when the other conditions change.
        self.locked.setVisible(False)
        self.mode.blockSignals(True)
        self.mode.setCurrentIndex(max(self.mode.findData(mode), 0))
        self.mode.blockSignals(False)
        self._build_rows()

    def _build_rows(self):
        while self.rows_box.count():
            w = self.rows_box.takeAt(0).widget()
            if w:
                w.deleteLater()
        for i, row in enumerate(self.rows):
            self.rows_box.addWidget(self._row_widget(i, row))
        self.mode_row.setVisible(len(self.rows) > 1)  # combining only matters with two or more
        self._update_summary()

    def _row_widget(self, i: int, row) -> QWidget:
        kind, op, value = row
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        kind_box = QComboBox()
        scroll_past(kind_box)
        kind_box.addItems(list(conds.ROW_TYPES))
        kind_box.setCurrentText(kind)
        ops, value_kind = conds.ROW_TYPES[kind]
        h.addWidget(kind_box)
        op_box = None
        if kind != "Custom":
            op_box = QComboBox()
            scroll_past(op_box)
            op_box.addItems(ops)
            op_box.setCurrentText(op)
            h.addWidget(op_box)
        value_widget = None
        if value_kind == "text":
            value_widget = QLineEdit(value)
            value_widget.setPlaceholderText({"Window title": "YouTube", "Screen": "eDP-1",
                                             "Custom": "$window_pid > 1000"}.get(kind, ""))
            value_widget.textEdited.connect(lambda t, i=i: self._set(i, value=t))
            h.addWidget(value_widget, 1)
        elif isinstance(value_kind, list) and op != conds.NO_MODIFIERS:
            value_widget = QComboBox()
            scroll_past(value_widget)
            for choice in value_kind:
                value_widget.addItem(choice.capitalize(), choice)
            value_widget.setCurrentIndex(max(value_widget.findData(value or value_kind[0]), 0))
            value_widget.currentIndexChanged.connect(
                lambda _n, i=i, w=value_widget: self._set(i, value=w.currentData()))
            h.addWidget(value_widget)
        if value_widget is None or isinstance(value_widget, QComboBox):
            h.addStretch(1)
        remove = QToolButton(icon=icons.icon("edit-delete-remove", icons.icon("list-remove")))
        remove.setAutoRaise(True)
        remove.setToolTip("Remove condition")
        remove.clicked.connect(lambda _=False, i=i: self._remove(i))
        h.addWidget(remove)
        kind_box.currentTextChanged.connect(lambda k, i=i: self._set_kind(i, k))
        if op_box:
            op_box.currentTextChanged.connect(lambda o, i=i: self._set(i, op=o, rebuild=True))
        return w

    def _set_kind(self, i: int, kind: str):
        ops, value_kind = conds.ROW_TYPES[kind]
        self.rows[i] = (kind, ops[0], value_kind[0] if isinstance(value_kind, list) else "")
        self._write()
        self._build_rows()

    def _set(self, i: int, op=None, value=None, rebuild=False):
        kind, o, v = self.rows[i]
        op = op if op is not None else o
        value = value if value is not None else v
        choices = conds.ROW_TYPES[kind][1]
        if isinstance(choices, list) and not value and op != conds.NO_MODIFIERS:
            value = choices[0]  # back from "none held": the key shown is the one written
        self.rows[i] = (kind, op, value)
        self._write()
        if rebuild:
            self._build_rows()
        else:
            self._update_summary()

    def _add(self):
        self.rows.append(("Window", "is fullscreen", ""))
        self._write()
        self._build_rows()

    def _remove(self, i: int):
        del self.rows[i]
        self._write()
        self._build_rows()

    def _write(self, *_):
        if self.data is None:
            return
        value = conds.join(self.app, self.mode.currentData(), self.rows)
        if value is None:
            self.data.pop("conditions", None)
        else:
            self.data["conditions"] = value
        self.changed.emit()

    def _update_summary(self):
        parts = [conds.describe_row(r) for r in self.rows if conds.to_text(r)]
        if not parts:
            self.summary.setText(f"Fires {self.where}." if self.where else "Fires everywhere.")
            return
        mode = self.mode.currentData()
        joined = (" and " if mode == "all" else ", ").join(parts)
        lead = {"all": "when", "any": "when any of:", "none": "unless"}[mode]
        self.summary.setText(f"Fires {self.where or 'everywhere'} {lead} {joined}.")


class BindingEditor(QWidget):
    """Edits one binding's data in place. Emits changed() after every applied edit,
    structure_changed() when the binding moved scope or was deleted."""

    changed = Signal()
    structure_changed = Signal()

    def __init__(self, config: cfg.Config):
        super().__init__()
        self.config = config
        self.binding: cfg.Binding | None = None
        self._loading = False
        self.show_conditions = True  # the Only if builder (a developer option in the app)

        self.title = WrapLabel()
        self.title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)  # level with its buttons
        self.title.setStyleSheet("font-weight: 600; font-size: 11pt;")
        self.yaml_button = QPushButton(icons.icon("text-x-script"), "YAML")
        self.yaml_button.setCheckable(True)
        self.yaml_button.toggled.connect(self._toggle_yaml)
        self.delete_button = QPushButton(icons.icon("edit-delete"), "Delete")
        self.delete_button.clicked.connect(self._delete)
        self.bind_button = QPushButton(icons.icon("list-add"), "Bind an action")
        self._bind_action = None
        self.bind_button.clicked.connect(lambda: self._bind_action and self._bind_action())
        head = QHBoxLayout()
        head.addWidget(self.title, 1)
        head.addWidget(self.bind_button)
        head.addWidget(self.yaml_button)
        head.addWidget(self.delete_button)

        # When
        self.when_box = Section("Trigger")
        self.when_form = QFormLayout(self.when_box.card)
        self.trigger_label = QLabel()
        self.type_box = QComboBox()  # for gestures, whose type can be changed
        self.type_box.currentTextChanged.connect(self._type_changed)
        trigger_row = QWidget()
        tr = QHBoxLayout(trigger_row)
        tr.setContentsMargins(0, 0, 0, 0)
        tr.addWidget(self.trigger_label)
        tr.addWidget(self.type_box)
        tr.addStretch(1)
        self.strokes_label = QLabel()
        record = QPushButton(icons.icon("draw-freehand", icons.icon("media-record")), "Record stroke")
        record.clicked.connect(self._record_stroke)
        clear = QPushButton("Clear")
        clear.clicked.connect(self._clear_strokes)
        self.keys_field = KeyField()
        self.keys_field.single = True
        self.keys_field.setMinimumWidth(200)
        self.keys_field.setMaximumWidth(260)
        self.keys_field.recorded.connect(self._keys_recorded)
        self.keys_field.editingFinished.connect(self._keys_edited)
        self.keys_record = QPushButton(icons.icon("media-record"), "Record")
        self.keys_record.setCheckable(True)
        self.keys_record.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.keys_record.toggled.connect(self._keys_record_toggled)
        self.keys_row = QWidget()
        kr = QHBoxLayout(self.keys_row)
        kr.setContentsMargins(0, 0, 0, 0)
        kr.addWidget(self.keys_field)
        kr.addWidget(self.keys_record)
        kr.addStretch(1)
        self.keys_error = WrapLabel()
        self.keys_error.setStyleSheet("color: #d24545;")
        self.strokes_row = QWidget()
        sr = QHBoxLayout(self.strokes_row)
        sr.setContentsMargins(0, 0, 0, 0)
        sr.addWidget(self.strokes_label)
        sr.addWidget(record)
        sr.addWidget(clear)
        sr.addStretch(1)
        self.fingers = QSpinBox(minimum=1, maximum=5)
        self.finger_warning = WrapLabel()
        self.finger_warning.setStyleSheet("color: palette(placeholder-text);")
        self.fingers_row = self.fingers
        self.hold_button = QComboBox()
        self.hold_button.currentIndexChanged.connect(self._hold_button_edited)
        self.hold_hint = WrapLabel("Hold it while you move the mouse. A plain click still works as usual.")
        self.hold_hint.setStyleSheet("color: palette(placeholder-text);")
        self.hold_row = self.hold_button
        self.direction = QComboBox()
        self.speed = QComboBox()
        for value, label in SPEEDS:
            self.speed.addItem(label, value)
        self.speed.setToolTip("Fast and slow versions of the same gesture can do different things.")
        self.threshold = QDoubleSpinBox(minimum=0, maximum=100000, decimals=2)
        self.threshold.setSpecialValueText("Off")  # 0: no threshold
        self.threshold.setMinimumWidth(110)
        self.threshold_hint = WrapLabel()
        self.threshold_hint.setStyleSheet("color: palette(placeholder-text);")
        self.lock_pointer = ToggleRow("Keep the pointer still while drawing")
        self.instant = ToggleRow("Fire as soon as the button goes down")
        self.block = ToggleRow("Override default system input")
        self.block.setToolTip("When on, only your action runs. When off, the app also gets the original "
                              "button press or gesture.")
        self.when_form.addRow("Type", trigger_row)
        self.when_form.addRow("Keys", self.keys_row)
        self.when_form.addRow("", self.keys_error)
        self.when_form.addRow("Strokes", self.strokes_row)
        self.when_form.addRow("Hold", self.hold_row)
        self.when_form.addRow("", self.hold_hint)
        self.when_form.addRow("Fingers", self.fingers_row)
        self.when_form.addRow("", self.finger_warning)
        self.when_form.addRow("Direction", self.direction)
        self.when_form.addRow("Speed", self.speed)
        self.when_form.addRow("Threshold", self.threshold)
        self.when_form.addRow("", self.threshold_hint)
        self.when_form.addRow("", self.lock_pointer)
        self.when_form.addRow("", self.instant)
        self.when_form.addRow("", self.block)
        # One label column for every card, sized to the longest label in the user's font.
        self._label_w = label_width(self, ["Type", "Keys", "Strokes", "Hold", "Fingers", "Direction", "Speed", "Threshold", "When", "Value"])
        tidy_form(self.when_form, width=self._label_w)
        self.fingers.valueChanged.connect(self._trigger_edited)
        self.direction.currentIndexChanged.connect(self._trigger_edited)
        self.speed.currentIndexChanged.connect(self._trigger_edited)
        self.threshold.valueChanged.connect(self._trigger_edited)
        self.lock_pointer.toggled.connect(self._trigger_edited)
        self.instant.toggled.connect(self._trigger_edited)
        self.block.toggled.connect(self._trigger_edited)

        # Only if
        self.cond_box = Section("Condition (optional)")
        self.conditions = ConditionBuilder()
        self.conditions.changed.connect(self._conditions_edited)
        cond_layout = QVBoxLayout(self.cond_box.card)
        cond_layout.setContentsMargins(14, 12, 14, 12)
        cond_layout.addWidget(self.conditions)

        # Do
        self.do_box = Section("Action")
        self.mode = QComboBox()
        self.mode.addItems(MODES)
        self.mode.currentTextChanged.connect(self._mode_changed)
        self.stage = QComboBox()
        for value, label in TAP_STAGES:
            self.stage.addItem(label, value)
        self.stage.currentIndexChanged.connect(self._do_edited)
        self.value = KeyField()
        self.value.recorded.connect(self._do_edited)
        self.record = QPushButton(icons.icon("media-record"), "Record")
        self.record.setCheckable(True)
        # Keep focus in the field while recording, so key presses keep landing there.
        self.record.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.record.toggled.connect(self._record_toggled)
        value_row = QWidget()
        vr = QHBoxLayout(value_row)
        vr.setContentsMargins(0, 0, 0, 0)
        self.value.setMaximumWidth(340)
        self.value.setMinimumWidth(200)
        self.value.setClearButtonEnabled(True)
        vr.addWidget(self.value)
        self.click_button = QComboBox()
        self.click_button.currentIndexChanged.connect(self._do_edited)
        vr.addWidget(self.click_button)
        vr.addWidget(self.record)
        self.key_list_button = QPushButton(icons.icon("input-keyboard"), "Keys…")
        self.key_list_button.clicked.connect(self._show_key_list)
        self.key_list_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        vr.addWidget(self.key_list_button)
        vr.addStretch(1)
        # Apply while typing, so nothing is lost if Save is clicked straight after.
        # Errors only show once the field is left, not on every half-typed key.
        self.value.textEdited.connect(lambda _t: self._do_edited(quiet=True))
        self.value.editingFinished.connect(self._do_edited)
        self.value_hint = WrapLabel()
        self.value_hint.setStyleSheet("color: palette(placeholder-text);")
        self.error = QLabel()
        self.error.setStyleSheet("color: #d24545;")
        self.error.setVisible(False)  # shown only when there's something to say
        _set_text = self.error.setText
        self.error.setText = lambda t: (_set_text(t), self.error.setVisible(bool(t)))
        self.error.clear = lambda: self.error.setText("")
        self.actions_yaml = QPlainTextEdit()
        self.actions_yaml.setMinimumHeight(140)
        self.actions_apply = QPushButton("Apply actions")
        self.actions_apply.clicked.connect(self._apply_actions_yaml)
        self.simple = QWidget()
        simple_form = QFormLayout(self.simple)
        simple_form.setContentsMargins(0, 0, 0, 0)
        simple_form.addRow("When", self.stage)
        simple_form.addRow("Value", value_row)
        simple_form.addRow("", self.value_hint)
        tidy_form(simple_form, margins=False, width=self._label_w)  # nested in the Action card: same label column as its Type row
        self.custom = QWidget()
        custom_layout = QVBoxLayout(self.custom)
        custom_layout.setContentsMargins(0, 0, 0, 0)
        custom_layout.addWidget(QLabel("These actions use features the forms don't cover yet. Edit them as YAML:"))
        custom_layout.addWidget(self.actions_yaml)
        custom_layout.addWidget(self.actions_apply, 0, Qt.AlignmentFlag.AlignRight)
        self.do_stack = QStackedWidget()
        self.do_stack.addWidget(self.simple)
        self.do_stack.addWidget(self.custom)
        do_form = QFormLayout(self.do_box.card)
        do_form.addRow("Type", self.mode)
        do_form.addRow(self.do_stack)
        do_form.addRow(self.error)
        tidy_form(do_form, width=self._label_w)

        # Whole-binding YAML
        self.yaml_box = Section("YAML for this binding")
        self.yaml = QPlainTextEdit()
        self.yaml.setMinimumHeight(220)
        self.yaml_apply = QPushButton("Apply YAML")
        self.yaml_apply.clicked.connect(self._apply_yaml)
        self.yaml_error = QLabel()
        self.yaml_error.setStyleSheet("color: #d24545;")
        yl = QVBoxLayout(self.yaml_box.card)
        yl.setContentsMargins(14, 12, 14, 12)
        yl.addWidget(self.yaml)
        row = QHBoxLayout()
        row.addWidget(self.yaml_error, 1)
        row.addWidget(self.yaml_apply)
        yl.addLayout(row)

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.addLayout(head)
        for w in (self.when_box, self.cond_box, self.do_box, self.yaml_box):
            # Never taller than their content: spare height goes to the stretch below, not into
            # gaps inside a card when a shorter binding replaces a taller one.
            w.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
            layout.addWidget(w)
        layout.addStretch(1)
        # Dropdowns share a width so they line up down the cards.
        for combo in (self.type_box, self.direction, self.mode, self.stage, self.hold_button, self.speed,
                      self.click_button):
            combo.setMinimumWidth(200)
        scroll_past(*self.findChildren(QComboBox), *self.findChildren(QAbstractSpinBox))
        self.show_message("Select a binding to edit it.")

    # Display ---------------------------------------------------------------

    def show_message(self, text: str, bind_action=None):
        self.binding = None
        self.title.setText(text)
        for w in (self.when_box, self.cond_box, self.do_box, self.yaml_box, self.yaml_button, self.delete_button):
            w.setVisible(False)
        self.bind_button.setVisible(bind_action is not None)
        self._bind_action = bind_action

    def show_binding(self, title: str, b: cfg.Binding):
        self._loading = True
        self.binding = b
        g = b.data
        t = str(g.get("type", ""))
        self.title.setText(title)
        self._shown_gesture = d.gesture_title(g)
        self.bind_button.setVisible(False)
        for w in (self.when_box, self.do_box, self.yaml_button, self.delete_button):
            w.setVisible(True)
        self.cond_box.setVisible(self.show_conditions)
        self.yaml_box.setVisible(self.yaml_button.isChecked())

        changeable = not b.button and t not in ("press", "shortcut") and b.section in GESTURE_TYPES
        self.trigger_label.setVisible(not changeable)
        self.type_box.setVisible(changeable)
        if changeable:
            self.type_box.blockSignals(True)
            self.type_box.clear()
            for gt in GESTURE_TYPES[b.section]:
                if gt not in ("press", "shortcut"):
                    self.type_box.addItem(gt.capitalize(), gt)
            if self.type_box.findData(t) < 0:
                self.type_box.addItem(t.capitalize(), t)
            self.type_box.setCurrentIndex(self.type_box.findData(t))
            self.type_box.blockSignals(False)
        elif b.button:
            # Press vs shortcut is InputActions' business: to the user it's all a mouse button.
            raw = d.button_label(b.button)
            name = self.config.button_names.get(b.button) or raw
            self.trigger_label.setText(f"Mouse button · {name}" + (f"  (sends {raw})" if name != raw else ""))
        elif t == "shortcut":
            self.trigger_label.setText("Keyboard shortcut")
        else:
            self.trigger_label.setText(" · ".join(d.trigger_parts({"type": t, **{
                k: g[k] for k in ("mouse_buttons", "shortcut") if k in g}})))
        is_shortcut = t == "shortcut" and not b.button
        self.when_form.setRowVisible(self.keys_row, is_shortcut)
        self.when_form.setRowVisible(self.keys_error, False)
        self.keys_record.setChecked(False)
        if is_shortcut:
            self.keys_field.setText(d.shortcut(g.get("shortcut") or []))
            combo = cfg.shortcut_key(g.get("shortcut") or [])
            self._show_keys_problem(shortcut_clash(self.config, combo) if combo else None)
        self.when_form.setRowVisible(self.strokes_row, t == "stroke")
        n = len(g.get("strokes") or []) if isinstance(g.get("strokes"), list) else (1 if g.get("strokes") else 0)
        self.strokes_label.setText(f"{n} recorded" if n else "No strokes are recorded yet. Use Record stroke to record one.")
        has_fingers = "fingers" in g or (b.section in FINGER_SECTIONS and t in FINGER_TYPES)
        self.when_form.setRowVisible(self.fingers_row, has_fingers)
        if has_fingers:
            self.fingers.setRange(*finger_range(b.section, t))
            self.fingers.setValue(int(g.get("fingers", default_fingers(t))))
        self._update_finger_warning()
        show_hold = b.section == "mouse" and t in MOTION_TYPES
        self.when_form.setRowVisible(self.hold_row, show_hold)
        self.when_form.setRowVisible(self.hold_hint, show_hold)
        if show_hold:
            self._fill_hold_button(g.get("mouse_buttons"))
        self.when_form.setRowVisible(self.direction, t in DIRECTIONS)
        self.direction.clear()
        if t in DIRECTIONS:
            for value in DIRECTIONS[t]:
                self.direction.addItem(d.direction(value, t).capitalize(), value)
            current = str(g.get("direction", DIRECTIONS[t][0]))
            if current not in DIRECTIONS[t]:
                self.direction.addItem(d.direction(current, t).capitalize(), current)
            self.direction.setCurrentIndex(self.direction.findData(current))
        show_speed = t in SPEED_TYPES or "speed" in g
        self.when_form.setRowVisible(self.speed, show_speed)
        if show_speed:
            current = g.get("speed")
            if self.speed.findData(current) < 0:
                self.speed.addItem(str(current), current)
            self.speed.setCurrentIndex(self.speed.findData(current))
        current = g.get("threshold")
        numeric = current is None or (isinstance(current, (int, float)) and not isinstance(current, bool))
        show_threshold = t in THRESHOLD_UNITS and numeric  # a min-max range stays in YAML
        self.when_form.setRowVisible(self.threshold, show_threshold)
        self.when_form.setRowVisible(self.threshold_hint, show_threshold)
        if show_threshold:
            self.threshold.setSuffix(THRESHOLD_UNITS[t])
            self.threshold.setDecimals(2 if t == "pinch" else 0)
            self.threshold.setSingleStep(0.05 if t == "pinch" else (50 if t == "hold" else 5))
            self.threshold.setValue(float(current or 0))
            self.threshold_hint.setText(THRESHOLD_HINTS.get(t, "How far to move before it counts, "
                                                               "so small slips don't fire it."))
        show_lock = b.section == "mouse" and t in LOCK_POINTER_TYPES
        self.when_form.setRowVisible(self.lock_pointer, show_lock)
        self.lock_pointer.setChecked(bool(g.get("lock_pointer", False)))
        self.when_form.setRowVisible(self.instant, t == "press")
        self.instant.setChecked(bool(g.get("instant", False)))
        self.block.setChecked(bool(g.get("block_events", True)))

        where = f"in {apps.display_name(b.app)}" if b.scope == "app" else ("everywhere" if b.scope == "global" else "")
        self.conditions.load(g, where)

        if b.type in END_ONLY and (fixed := end_only_actions(g.get("actions"))) is not None:
            g["actions"] = fixed  # it wouldn't load as it was
            self.changed.emit()
        mode, values = detect_mode(g.get("actions"))
        self._fill_do(mode, values)
        self.yaml.setPlainText(d.to_yaml(g))
        self.yaml_error.clear()
        self.error.clear()
        self._loading = False

    def _fill_do(self, mode: str, values: dict):
        self.mode.setCurrentText(mode)
        custom = mode == "Custom (YAML)"
        self._show_page(self.custom if custom else self.simple)
        if custom:
            self.actions_yaml.setPlainText(d.to_yaml(self.binding.data.get("actions") or []))
            return
        end_only = self.binding.type in END_ONLY
        self.mode.model().item(MODES.index("Hold keys")).setEnabled(not end_only)
        self.stage.setVisible(mode != "Hold keys" and not end_only)
        self.simple.layout().labelForField(self.stage).setVisible(mode != "Hold keys" and not end_only)
        idx = self.stage.findData("end" if end_only else values.get("on", default_stage(self.binding.type)))
        self.stage.setCurrentIndex(max(idx, 0))
        self.record.setChecked(False)
        self.record.setVisible(mode in ("Press keys", "Hold keys"))
        self.key_list_button.setVisible(mode in ("Press keys", "Hold keys"))
        clicking = mode == "Click mouse button"
        self.value.setVisible(not clicking)
        self.click_button.setVisible(clicking)
        if clicking:
            self._fill_click_button(values.get("value") or "middle")
        self.value.single = mode == "Hold keys"
        self.value.setText(values.get("keys", values.get("value", "")) if not clicking else "")
        self.value.setPlaceholderText({
            "Press keys": "Ctrl+W", "Hold keys": "F", "Run command": "konsole",
            "Plasma shortcut": "kwin,Overview", "Type text": "Hello!", "Click mouse button": "",
        }[mode])
        self.value_hint.setText({
            "Press keys": "Click Record and press the keys, or type them: Ctrl+C, Ctrl+V",
            "Hold keys": "Held while the trigger is active. Click Record and press them, or type them.",
            "Click mouse button": "Clicks this button wherever the pointer is.",
            "Type text": "Typed into the focused window, exactly as written.",
            "Run command": "Runs in the background.",
            "Plasma shortcut": "Component and shortcut name, as in System Settings → Shortcuts.",
        }[mode])

    def _fill_click_button(self, current: str):
        self.click_button.blockSignals(True)
        self.click_button.clear()
        names = self.config.button_names if self.config else {}
        for src in CLICK_BUTTONS:
            label = d.button_label(src)
            name = names.get(src) or label
            self.click_button.addItem(name if name == label else f"{name} ({label})", src)
        if self.click_button.findData(current) < 0:
            self.click_button.addItem(d.button_label(current), current)
        self.click_button.setCurrentIndex(self.click_button.findData(current))
        self.click_button.blockSignals(False)

    # Edits -----------------------------------------------------------------

    def _commit(self):
        new = d.gesture_title(self.binding.data)
        self.title.setText(self.title.text().replace(self._shown_gesture, new, 1))
        self._shown_gesture = new
        self.yaml.setPlainText(d.to_yaml(self.binding.data))
        self.changed.emit()

    def _type_changed(self, _text):
        if self._loading or not self.binding:
            return
        g, new = self.binding.data, self.type_box.currentData()
        if not new or new == g.get("type"):
            return
        old, g["type"] = str(g.get("type")), new
        if self.binding.section in FINGER_SECTIONS and new in FINGER_TYPES:
            low, high = finger_range(self.binding.section, new)
            count = g.get("fingers")
            # Left at the old gesture's default, or not allowed for this one: use this one's default.
            if not isinstance(count, int) or count == default_fingers(old) or not low <= count <= high:
                g["fingers"] = default_fingers(new)
        else:
            g.pop("fingers", None)
        if self.binding.section == "mouse":
            if new in MOTION_TYPES and not g.get("mouse_buttons"):
                g["mouse_buttons"] = list(MOUSE_HOLD_DEFAULT)
            elif new == "wheel" and g.get("mouse_buttons") == MOUSE_HOLD_DEFAULT:
                g.pop("mouse_buttons", None)  # the one we added: a wheel needs no held button
        if new in DIRECTIONS:
            if str(g.get("direction")) not in DIRECTIONS[new]:
                g["direction"] = DIRECTIONS[new][0]
        else:
            g.pop("direction", None)
        if new != "stroke":
            g.pop("strokes", None)
        if new not in SPEED_TYPES:
            g.pop("speed", None)
        if new not in THRESHOLD_UNITS or THRESHOLD_UNITS.get(new) != THRESHOLD_UNITS.get(old):
            g.pop("threshold", None)  # measured differently now
        if new not in LOCK_POINTER_TYPES:
            g.pop("lock_pointer", None)
        if new in END_ONLY and (fixed := end_only_actions(g.get("actions"))) is not None:
            g["actions"] = fixed
        self._commit()
        self.show_binding(self.title.text(), self.binding)  # rows for the new type
        if new == "stroke" and not g.get("strokes"):
            self._record_stroke()
        devices.check_touchpad_access(self, self.binding.section, new)

    def _keys_record_toggled(self, on: bool):
        if on:
            self._keys_pause = pause.ShortcutPause()
            self._keys_pause.start()  # so the combo is recorded instead of running
            self.keys_field.start()
            self.keys_record.setText("Stop")
            self._show_keys_problem(None)
            self.keys_field.setPlaceholderText("Press the combo, like Ctrl+A")
        else:
            if getattr(self, "_keys_pause", None):
                self._keys_pause.stop()
                self._keys_pause = None
            self.keys_field.stop()
            self.keys_record.setText("Record")
            self.keys_field.setPlaceholderText("")

    def _show_keys_problem(self, problem: str | None):
        self.keys_error.setText(problem or "")
        self.when_form.setRowVisible(self.keys_error, bool(problem))

    def _keys_recorded(self):
        self.keys_record.setChecked(False)  # one combo is all a shortcut has
        self._keys_edited()

    def _keys_edited(self):
        if self._loading or not self.binding or self.binding.type != "shortcut" or self.binding.button:
            return
        if not self.keys_field.text().strip():
            self._show_keys_problem(None)
            return
        try:
            combo = keys.parse_combo(self.keys_field.text())
            problem = shortcut_clash(self.config, combo)
        except ValueError as e:
            problem = str(e)
        self._show_keys_problem(problem)
        if problem or combo.split("+") == list(self.binding.data.get("shortcut") or []):
            return
        self.binding.data["shortcut"] = combo.split("+")
        self._commit()

    def _record_stroke(self):
        if not self.binding:
            return
        stroke = record_stroke(self)
        if stroke:
            g = self.binding.data
            strokes = g.get("strokes")
            strokes = list(strokes) if isinstance(strokes, list) else ([strokes] if strokes else [])
            g["strokes"] = strokes + [stroke]
            self._commit()
            self.show_binding(self.title.text(), self.binding)

    def _clear_strokes(self):
        if self.binding and self.binding.data.pop("strokes", None) is not None:
            self._commit()
            self.show_binding(self.title.text(), self.binding)

    def _show_page(self, page: QWidget):
        """Show one page of the Action stack, sized to that page alone: a stack is otherwise as
        tall as its tallest page, leaving empty space under the short form."""
        for w in (self.simple, self.custom):
            w.setSizePolicy(QSizePolicy.Policy.Preferred,
                            QSizePolicy.Policy.Preferred if w is page else QSizePolicy.Policy.Ignored)
        self.do_stack.setCurrentWidget(page)
        self.do_stack.adjustSize()

    def _conditions_edited(self):
        if self._loading or not self.binding:
            return
        self._commit()

    def _fill_hold_button(self, current):
        self.hold_button.blockSignals(True)
        self.hold_button.clear()
        names = self.config.button_names if self.config else {}
        for src in MOUSE_HOLD_BUTTONS:
            name = names.get(src) or d.button_label(src)
            self.hold_button.addItem(name if name == d.button_label(src) else f"{name} ({d.button_label(src)})", [src])
        current = list(current) if isinstance(current, list) else ([current] if current else [])
        if not current:
            self.hold_button.addItem("Nothing (catches every mouse movement)", [])
        elif self.hold_button.findData(current) < 0:
            self.hold_button.addItem(" + ".join(d.button_label(str(c)) for c in current), current)
        self.hold_button.setCurrentIndex(self.hold_button.findData(current))
        self.hold_button.blockSignals(False)

    def _hold_button_edited(self, _index):
        if self._loading or not self.binding or self.hold_button.currentData() is None:
            return
        buttons = self.hold_button.currentData()
        if buttons:
            self.binding.data["mouse_buttons"] = list(buttons)
        else:
            self.binding.data.pop("mouse_buttons", None)
        self._commit()

    def _update_finger_warning(self):
        b = self.binding
        one = (b is not None and self.when_form.isRowVisible(self.fingers_row) and self.fingers.value() == 1
               and b.type in MOTION_TYPES)
        self.when_form.setRowVisible(self.finger_warning, one)
        if one:
            what = "moving the pointer" if b.section == "touchpad" else "touching and scrolling"
            self.finger_warning.setText(f"One finger is also how you're {what}, so this gesture "
                                        f"takes that over wherever it applies. Use three fingers instead.")

    def _trigger_edited(self, *_):
        if self._loading or not self.binding:
            return
        g = self.binding.data
        self._update_finger_warning()
        if self.when_form.isRowVisible(self.fingers_row):
            g["fingers"] = self.fingers.value()
        if self.when_form.isRowVisible(self.direction) and self.direction.currentData():
            g["direction"] = self.direction.currentData()
        if self.when_form.isRowVisible(self.speed):
            if self.speed.currentData():
                g["speed"] = self.speed.currentData()
            else:
                g.pop("speed", None)
        if self.when_form.isRowVisible(self.threshold):
            value = self.threshold.value()
            if value:
                g["threshold"] = int(value) if self.threshold.decimals() == 0 else round(value, 2)
            else:
                g.pop("threshold", None)
        if self.when_form.isRowVisible(self.lock_pointer):
            if self.lock_pointer.isChecked():
                g["lock_pointer"] = True
            else:
                g.pop("lock_pointer", None)
        if self.binding.type == "press":
            g["instant"] = self.instant.isChecked()
        if self.block.isChecked():
            g.pop("block_events", None)  # true is the default
        else:
            g["block_events"] = False
        self._commit()

    def _show_key_list(self):
        self.record.setChecked(False)
        KeyList(self.value, self).exec()

    def _record_toggled(self, on: bool):
        if on:
            self._pause = pause.ShortcutPause()
            self._pause.start()  # so Meta+W, Alt+F4 etc. are recorded instead of triggering
            self.value.start()
            self.record.setText("Stop")
            self.value_hint.setText("Recording. Press keys, then click Stop. "
                                    "Plasma shortcuts are paused until you do.")
        else:
            if getattr(self, "_pause", None):
                self._pause.stop()
                self._pause = None
            self.value.stop()
            self.record.setText("Record")
            self._fill_hint()

    def _fill_hint(self):
        self.value_hint.setText({
            "Press keys": "Click Record and press the keys, or type them: Ctrl+C, Ctrl+V",
            "Hold keys": "Held while the trigger is active. Click Record and press them, or type them.",
            "Run command": "Runs in the background.",
            "Plasma shortcut": "Component and shortcut name, as in System Settings → Shortcuts.",
        }.get(self.mode.currentText(), ""))

    def _mode_changed(self, mode: str):
        if self._loading or not self.binding:
            return
        if mode == "Custom (YAML)":
            self._fill_do(mode, {})
            return
        self._loading = True
        self._fill_do(mode, {"on": self.stage.currentData(), "keys": "", "value": ""})
        self._loading = False
        if mode == "Click mouse button":
            self._do_edited()  # nothing to type: the shown button is already a choice
        else:
            self.value.setFocus()

    def _do_edited(self, *_, quiet: bool = False):
        if self._loading or not self.binding:
            return
        mode = self.mode.currentText()
        if mode == "Custom (YAML)":
            return
        try:
            if not self.value.text().strip() and mode in ("Press keys", "Hold keys"):
                # Cleared: keep the binding but make it do nothing, matching what's shown.
                actions = [{"on": self.stage.currentData() or "begin", "input": [{"keyboard": []}]}]
            else:
                value = self.click_button.currentData() if mode == "Click mouse button" else self.value.text()
                actions = build_actions(mode, {"on": self.stage.currentData(), "keys": self.value.text(),
                                               "value": value})
        except ValueError as e:
            if not quiet:
                self.error.setText(str(e))
            return
        self.error.clear()
        self.binding.data["actions"] = actions
        self._commit()

    def _apply_actions_yaml(self):
        try:
            actions = load_yaml(self.actions_yaml.toPlainText())
            if not isinstance(actions, list):
                raise ValueError("Actions must be a list.")
        except Exception as e:  # noqa: BLE001
            self.error.setText(f"Not applied: {e}")
            return
        self.error.clear()
        self.binding.data["actions"] = actions
        self._commit()

    def _toggle_yaml(self, on: bool):
        self.yaml_box.setVisible(on and self.binding is not None)

    def _apply_yaml(self):
        try:
            new = load_yaml(self.yaml.toPlainText())
            if not isinstance(new, dict) or "type" not in new:
                raise ValueError("A binding needs at least a type.")
        except Exception as e:  # noqa: BLE001
            self.yaml_error.setText(f"Not applied: {e}")
            return
        self.yaml_error.clear()
        data = self.binding.data
        data.clear()
        data.update(new)
        self.config.reindex()
        self.structure_changed.emit()

    def _delete(self):
        b = self.binding
        answer = QMessageBox.warning(
            self, "Actuator", f"Delete {b.id}?\n\nNothing is written until you save.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.config.remove_bindings([b])
        self.binding = None
        self.structure_changed.emit()
