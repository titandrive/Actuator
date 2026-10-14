"""Editing a single binding: When / Only if / Do forms, with raw YAML as the fallback."""

import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)
from ruamel.yaml import YAML

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


def load_yaml(text: str):
    return YAML(typ="safe").load(text)


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
        self.cond_label = QLabel()
        self.cond_label.setWordWrap(True)
        cond_hint = QLabel("Use YAML for more conditions; the binding moves to Custom rules.")
        cond_hint.setStyleSheet("color: palette(placeholder-text);")
        cond_layout = QVBoxLayout(self.cond_box)
        cond_layout.addWidget(self.cond_label)
        cond_layout.addWidget(cond_hint)

        # Do
        self.do_box = QGroupBox("Do")
        self.mode = QComboBox()
        self.mode.addItems(MODES)
        self.mode.currentTextChanged.connect(self._mode_changed)
        self.stage = QComboBox()
        for value, label in TAP_STAGES:
            self.stage.addItem(label, value)
        self.stage.currentIndexChanged.connect(self._do_edited)
        self.value = QLineEdit()
        # Apply while typing, so nothing is lost if Save is clicked straight after.
        # Errors only show once the field is left, not on every half-typed key.
        self.value.textEdited.connect(lambda _t: self._do_edited(quiet=True))
        self.value.editingFinished.connect(self._do_edited)
        self.value_hint = QLabel()
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
        simple_form.addRow("Value", self.value)
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

        conds = d.conditions(g.get("conditions"))
        self.cond_label.setText("<br>".join(conds) if conds else "Always (every app)")

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
        self.value.setText(values.get("keys", values.get("value", "")))
        self.value.setPlaceholderText({
            "Press keys": "Ctrl+W", "Hold keys": "F", "Run command": "konsole",
            "Plasma shortcut": "kwin,Overview",
        }[mode])
        self.value_hint.setText({
            "Press keys": "Key combos, separated by commas for a sequence: Ctrl+C, Ctrl+V",
            "Hold keys": "Held down while the trigger is active, released when it ends.",
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
