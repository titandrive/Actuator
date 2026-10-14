"""Editing a single binding: When / Only if / Do forms, with raw YAML as the fallback."""

import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QStackedWidget, QToolButton, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)
from ruamel.yaml import YAML

from . import conditions as conds
from . import config as cfg
from . import describe as d
from . import keys

# Trigger types each device section supports.
GESTURE_TYPES = {
    "mouse": ["press", "wheel", "swipe", "stroke", "circle"],
    "keyboard": ["shortcut"],
    "touchpad": ["swipe", "pinch", "rotate", "hold", "tap", "click", "circle", "stroke"],
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

MODES = ["Press keys", "Hold keys", "Run command", "Plasma shortcut", "Custom (YAML)"]
TAP_STAGES = [("begin", "When it starts"), ("end", "When it ends")]


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


def scope_conditions(scope: str, app: str | None):
    return [f"$window_class == {app}"] if scope == "app" else None


def default_stage(trigger_type: str) -> str:
    return "end" if trigger_type in ("tap", "click") else "begin"


def new_button_binding(config: cfg.Config, button: cfg.Button, scope: str, app: str | None) -> tuple[str, dict]:
    where = app or "global"
    data = {"id": unique_id(config, f"{slug(where)}_{slug(button.name)}")}
    if button.kind == "mouse":
        section = "mouse"
        data.update(type="press", mouse_buttons=[button.src], instant=True)
    else:
        section = "keyboard"
        data.update(type="shortcut", shortcut=button.src.split("+"), clear_modifiers=True)
    if conds := scope_conditions(scope, app):
        data["conditions"] = conds
    data["actions"] = [{"on": "begin", "input": [{"keyboard": []}]}]
    return section, data


def new_gesture_binding(config: cfg.Config, section: str, trigger_type: str, scope: str, app: str | None) -> dict:
    where = app or "global"
    data = {"id": unique_id(config, f"{slug(where)}_{section}_{trigger_type}"), "type": trigger_type}
    if section in FINGER_SECTIONS and trigger_type in FINGER_TYPES:
        data["fingers"] = 3
    if trigger_type in DIRECTIONS:
        data["direction"] = DIRECTIONS[trigger_type][0]
    if trigger_type == "shortcut":
        data["shortcut"] = ["leftmeta", "a"]
        data["clear_modifiers"] = True
    if trigger_type == "press":
        data["mouse_buttons"] = ["middle"]
    if conds := scope_conditions(scope, app):
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
        self.setStyleSheet("QLineEdit { border: 2px solid #d24545; }")

    def stop(self):
        self.recording = False
        self._lone = None
        self.setStyleSheet("")

    def _add(self, combo: str):
        label = d.combo(combo)
        if self.single or self._first or not self.text().strip():
            self.setText(label)
        else:
            self.setText(f"{self.text().rstrip(', ')}, {label}")
        self._first = False
        self.recorded.emit()

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
        self.mode_prefix = QLabel("and")
        mr.addWidget(self.mode_prefix)
        mr.addWidget(self.mode)
        mr.addStretch(1)
        self.rows_box = QVBoxLayout()
        add = QPushButton(QIcon.fromTheme("list-add"), "Add condition")
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
        self.locked.setVisible(bool(self.app))
        if self.app:
            self.locked.setText(f"🔒 {d.condition(self.app)}  <span style='color:palette(placeholder-text)'>"
                                "(from the sidebar)</span>")
        self.mode_prefix.setText("and" if self.app else "When")
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
        self.mode_row.setVisible(len(self.rows) > 1 or (len(self.rows) == 1 and not self.app))
        if not self.app and len(self.rows) <= 1:
            self.mode_prefix.setText("When")
        self._update_summary()

    def _row_widget(self, i: int, row) -> QWidget:
        kind, op, value = row
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        kind_box = QComboBox()
        kind_box.addItems(list(conds.ROW_TYPES))
        kind_box.setCurrentText(kind)
        ops, value_kind = conds.ROW_TYPES[kind]
        h.addWidget(kind_box)
        op_box = None
        if kind != "Custom":
            op_box = QComboBox()
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
        elif isinstance(value_kind, list) and op != "are none":
            value_widget = QComboBox()
            value_widget.addItems(value_kind)
            value_widget.setCurrentText(value or value_kind[0])
            value_widget.currentTextChanged.connect(lambda t, i=i: self._set(i, value=t))
            h.addWidget(value_widget)
        if value_widget is None or isinstance(value_widget, QComboBox):
            h.addStretch(1)
        remove = QToolButton(icon=QIcon.fromTheme("edit-delete-remove", QIcon.fromTheme("list-remove")))
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
        self.rows[i] = (kind, op if op is not None else o, value if value is not None else v)
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

        self.title = QLabel()
        self.title.setStyleSheet("font-weight: 600;")
        self.yaml_button = QPushButton(QIcon.fromTheme("text-x-script"), "YAML")
        self.yaml_button.setCheckable(True)
        self.yaml_button.toggled.connect(self._toggle_yaml)
        self.delete_button = QPushButton(QIcon.fromTheme("edit-delete"), "Delete")
        self.delete_button.clicked.connect(self._delete)
        self.bind_button = QPushButton(QIcon.fromTheme("list-add"), "Bind an action")
        self._bind_action = None
        self.bind_button.clicked.connect(lambda: self._bind_action and self._bind_action())
        head = QHBoxLayout()
        head.addWidget(self.title, 1)
        head.addWidget(self.bind_button)
        head.addWidget(self.yaml_button)
        head.addWidget(self.delete_button)

        # When
        self.when_box = QGroupBox("When")
        self.when_form = QFormLayout(self.when_box)
        self.trigger_label = QLabel()
        self.fingers = QSpinBox(minimum=1, maximum=5)
        self.direction = QComboBox()
        self.instant = QCheckBox("Fire as soon as the button goes down")
        self.block = QCheckBox("Block the normal input while this runs")
        self.when_form.addRow("Trigger", self.trigger_label)
        self.when_form.addRow("Fingers", self.fingers)
        self.when_form.addRow("Direction", self.direction)
        self.when_form.addRow("", self.instant)
        self.when_form.addRow("", self.block)
        self.fingers.valueChanged.connect(self._trigger_edited)
        self.direction.currentTextChanged.connect(self._trigger_edited)
        self.instant.toggled.connect(self._trigger_edited)
        self.block.toggled.connect(self._trigger_edited)

        # Only if
        self.cond_box = QGroupBox("Only if")
        self.conditions = ConditionBuilder()
        self.conditions.changed.connect(self._conditions_edited)
        QVBoxLayout(self.cond_box).addWidget(self.conditions)

        # Do
        self.do_box = QGroupBox("Do")
        self.mode = QComboBox()
        self.mode.addItems(MODES)
        self.mode.currentTextChanged.connect(self._mode_changed)
        self.stage = QComboBox()
        for value, label in TAP_STAGES:
            self.stage.addItem(label, value)
        self.stage.currentIndexChanged.connect(self._do_edited)
        self.value = KeyField()
        self.value.recorded.connect(self._do_edited)
        self.record = QPushButton(QIcon.fromTheme("media-record"), "Record")
        self.record.setCheckable(True)
        # Keep focus in the field while recording, so key presses keep landing there.
        self.record.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.record.toggled.connect(self._record_toggled)
        value_row = QWidget()
        vr = QHBoxLayout(value_row)
        vr.setContentsMargins(0, 0, 0, 0)
        self.value.setMaximumWidth(340)
        self.value.setClearButtonEnabled(True)
        vr.addWidget(self.value)
        vr.addWidget(self.record)
        self.key_list_button = QPushButton(QIcon.fromTheme("input-keyboard"), "Keys…")
        self.key_list_button.clicked.connect(self._show_key_list)
        self.key_list_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        vr.addWidget(self.key_list_button)
        vr.addStretch(1)
        # Apply while typing, so nothing is lost if Save is clicked straight after.
        # Errors only show once the field is left, not on every half-typed key.
        self.value.textEdited.connect(lambda _t: self._do_edited(quiet=True))
        self.value.editingFinished.connect(self._do_edited)
        self.value_hint = QLabel()
        self.value_hint.setWordWrap(True)
        self.value_hint.setStyleSheet("color: palette(placeholder-text);")
        self.error = QLabel()
        self.error.setStyleSheet("color: #d24545;")
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
        self.custom = QWidget()
        custom_layout = QVBoxLayout(self.custom)
        custom_layout.setContentsMargins(0, 0, 0, 0)
        custom_layout.addWidget(QLabel("These actions use features the forms don't cover yet. Edit them as YAML:"))
        custom_layout.addWidget(self.actions_yaml)
        custom_layout.addWidget(self.actions_apply, 0, Qt.AlignmentFlag.AlignRight)
        self.do_stack = QStackedWidget()
        self.do_stack.addWidget(self.simple)
        self.do_stack.addWidget(self.custom)
        do_form = QFormLayout(self.do_box)
        do_form.addRow("Action", self.mode)
        do_form.addRow(self.do_stack)
        do_form.addRow(self.error)

        # Whole-binding YAML
        self.yaml_box = QGroupBox("YAML for this binding")
        self.yaml = QPlainTextEdit()
        self.yaml.setMinimumHeight(220)
        self.yaml_apply = QPushButton("Apply YAML")
        self.yaml_apply.clicked.connect(self._apply_yaml)
        self.yaml_error = QLabel()
        self.yaml_error.setStyleSheet("color: #d24545;")
        yl = QVBoxLayout(self.yaml_box)
        yl.addWidget(self.yaml)
        row = QHBoxLayout()
        row.addWidget(self.yaml_error, 1)
        row.addWidget(self.yaml_apply)
        yl.addLayout(row)

        layout = QVBoxLayout(self)
        layout.addLayout(head)
        for w in (self.when_box, self.cond_box, self.do_box, self.yaml_box):
            layout.addWidget(w)
        layout.addStretch(1)
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
        for w in (self.when_box, self.cond_box, self.do_box, self.yaml_button, self.delete_button):
            w.setVisible(True)
        self.yaml_box.setVisible(self.yaml_button.isChecked())

        self.trigger_label.setText(" · ".join(d.trigger_parts({"type": t, **{
            k: g[k] for k in ("mouse_buttons", "shortcut") if k in g}})))
        has_fingers = "fingers" in g or (b.section in FINGER_SECTIONS and t in FINGER_TYPES)
        self.when_form.setRowVisible(self.fingers, has_fingers)
        if has_fingers:
            self.fingers.setValue(int(g.get("fingers", 3)))
        self.when_form.setRowVisible(self.direction, t in DIRECTIONS)
        self.direction.clear()
        if t in DIRECTIONS:
            self.direction.addItems(DIRECTIONS[t])
            current = str(g.get("direction", DIRECTIONS[t][0]))
            if current not in DIRECTIONS[t]:
                self.direction.addItem(current)
            self.direction.setCurrentText(current)
        self.when_form.setRowVisible(self.instant, t == "press")
        self.instant.setChecked(bool(g.get("instant", False)))
        self.block.setChecked(bool(g.get("block_events", True)))

        where = f"in {b.app.capitalize()}" if b.scope == "app" else ("everywhere" if b.scope == "global" else "")
        self.conditions.load(g, where)

        mode, values = detect_mode(g.get("actions"))
        self._fill_do(mode, values)
        self.yaml.setPlainText(d.to_yaml(g))
        self.yaml_error.clear()
        self.error.clear()
        self._loading = False

    def _fill_do(self, mode: str, values: dict):
        self.mode.setCurrentText(mode)
        custom = mode == "Custom (YAML)"
        self.do_stack.setCurrentWidget(self.custom if custom else self.simple)
        if custom:
            self.actions_yaml.setPlainText(d.to_yaml(self.binding.data.get("actions") or []))
            return
        self.stage.setVisible(mode != "Hold keys")
        self.simple.layout().labelForField(self.stage).setVisible(mode != "Hold keys")
        idx = self.stage.findData(values.get("on", default_stage(self.binding.type)))
        self.stage.setCurrentIndex(max(idx, 0))
        self.record.setChecked(False)
        self.record.setVisible(mode in ("Press keys", "Hold keys"))
        self.key_list_button.setVisible(mode in ("Press keys", "Hold keys"))
        self.value.single = mode == "Hold keys"
        self.value.setText(values.get("keys", values.get("value", "")))
        self.value.setPlaceholderText({
            "Press keys": "Ctrl+W", "Hold keys": "F", "Run command": "konsole",
            "Plasma shortcut": "kwin,Overview",
        }[mode])
        self.value_hint.setText({
            "Press keys": "Click Record and press the keys, or type them: Ctrl+C, Ctrl+V",
            "Hold keys": "Held while the trigger is active. Click Record and press them, or type them.",
            "Run command": "Runs in the background.",
            "Plasma shortcut": "Component and shortcut name, as in System Settings → Shortcuts.",
        }[mode])

    # Edits -----------------------------------------------------------------

    def _commit(self):
        new = d.gesture_title(self.binding.data)
        self.title.setText(self.title.text().replace(self._shown_gesture, new, 1))
        self._shown_gesture = new
        self.yaml.setPlainText(d.to_yaml(self.binding.data))
        self.changed.emit()

    def _conditions_edited(self):
        if self._loading or not self.binding:
            return
        self._commit()

    def _trigger_edited(self, *_):
        if self._loading or not self.binding:
            return
        g = self.binding.data
        if self.when_form.isRowVisible(self.fingers):
            g["fingers"] = self.fingers.value()
        if self.when_form.isRowVisible(self.direction) and self.direction.currentText():
            g["direction"] = self.direction.currentText()
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
            self.value.start()
            self.record.setText("Stop")
            self.value_hint.setText("Recording. Press keys, then click Stop. Shortcuts Plasma uses "
                                    "(like Meta+W) may not reach Actuator; type or pick those instead.")
        else:
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
                actions = build_actions(mode, {"on": self.stage.currentData(), "keys": self.value.text(),
                                               "value": self.value.text()})
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
        answer = QMessageBox.question(
            self, "Actuator", f"Delete {b.id}?\n\nNothing is written until you save.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.config.remove_bindings([b])
        self.binding = None
        self.structure_changed.emit()
