"""Device settings: InputActions' device_rules, shown per device type and per named device."""

import os
import re
import subprocess
from dataclasses import dataclass

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QSlider, QSplitter, QToolButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)
from ruamel.yaml import YAML

from . import icons
from . import config as cfg
from . import describe as d
from .widgets import CommandBox, Toggle

TYPES = {  # key → (label, icon)
    "mouse": ("Mouse", "input-mouse"),
    "touchpad": ("Touchpad", "input-touchpad"),
    "touchscreen": ("Touchscreen", "input-tablet"),
    "keyboard": ("Keyboard", "input-keyboard"),
}


@dataclass
class Setting:
    key: str  # dotted YAML path, e.g. "swipe.angle_tolerance"
    label: str
    help: str
    default: object
    lo: int = 0
    hi: int = 0
    unit: str = ""
    advanced: bool = False

    @property
    def is_bool(self) -> bool:
        return isinstance(self.default, bool)


ANGLE = Setting("swipe.angle_tolerance", "Swipe angle tolerance",
                "How far off straight a swipe can be and still count as up, down, left or right.",
                20, 0, 45, "°")

# From the InputActions docs (devices/*.html).
SETTINGS = {
    "mouse": [
        ANGLE,
        Setting("motion_threshold", "Motion threshold", "Movement before a swipe's direction is decided.", 10, 1, 100),
        Setting("motion_timeout", "Motion timeout",
                "How long a held button waits for movement before it counts as a press.", 200, 0, 2000, " ms"),
        Setting("press_timeout", "Press timeout", "Grace period when pressing several buttons together.",
                50, 0, 1000, " ms"),
        Setting("unblock_buttons_on_timeout", "Release held buttons on timeout",
                "If off, held buttons are pressed and released together when you let go.", True),
    ],
    "touchpad": [
        ANGLE,
        Setting("motion_threshold", "Motion threshold, 1 finger", "Movement before a swipe's direction is decided.",
                10, 1, 100),
        Setting("motion_threshold_2", "Motion threshold, 2 fingers", "", 10, 1, 100),
        Setting("motion_threshold_3", "Motion threshold, 3–4 fingers", "", 10, 1, 100),
        Setting("click_timeout", "Click timeout", "Tap faster than this for a click; slower starts a hold.",
                200, 0, 2000, " ms"),
        Setting("pressure_ranges.finger", "Finger pressure", "Touches lighter than this are ignored.",
                0, 0, 255, advanced=True),
        Setting("pressure_ranges.thumb", "Thumb pressure", "Touches at least this hard count as a thumb.",
                4294967295, 0, 255, advanced=True),
        Setting("pressure_ranges.palm", "Palm pressure", "Touches at least this hard are ignored as palms.",
                4294967295, 0, 255, advanced=True),
        Setting("buttonpad", "Buttonpad", "The whole touchpad is one button. Normally detected automatically.",
                False, advanced=True),
        Setting("handle_evdev_events", "Handle evdev events", "Turn off only if this touchpad misbehaves.",
                True, advanced=True),
    ],
    "touchscreen": [
        ANGLE,
        Setting("motion_threshold", "Motion threshold", "Movement in mm before a swipe's direction is decided.",
                4, 1, 50, " mm"),
    ],
    "keyboard": [],
}

NAME_RULE = re.compile(r"^\$name\s*==\s*(.+)$")


# Rules in the config ---------------------------------------------------------

def target_of(rule) -> tuple[str, str] | None:
    """('type', 'touchpad') or ('name', 'Razer …') for rules the form understands, else None."""
    if not isinstance(rule, dict):
        return None
    c = rule.get("conditions")
    if isinstance(c, list) and len(c) == 1:
        c = c[0]
    if not isinstance(c, str):
        return None
    c = c.strip()
    if c.startswith("$") and c[1:] in TYPES:
        return ("type", c[1:])
    if m := NAME_RULE.match(c):
        return ("name", m.group(1).strip().strip("'\""))
    return None


def condition_for(target) -> str:
    kind, value = target
    return f"${value}" if kind == "type" else f"$name == {value}"


def rules(config: cfg.Config) -> list:
    r = config.data.get("device_rules")
    if not isinstance(r, list):
        r = config.data["device_rules"] = []
    return r


def find_rule(config, target) -> dict | None:
    return next((r for r in rules(config) if target_of(r) == target), None)


def other_rules(config) -> list:
    return [r for r in rules(config) if target_of(r) is None]


def get_value(rule, key: str):
    node = rule
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def set_value(config, target, key: str, value):
    rule = find_rule(config, target)
    if rule is None:
        rule = {"conditions": condition_for(target)}
        # Named devices go first so they override the type-wide rules after them.
        r = rules(config)
        at = 0 if target[0] == "name" else sum(1 for x in r if (target_of(x) or ("",))[0] == "name")
        r.insert(at, rule)
    node = rule
    *parents, last = key.split(".")
    for part in parents:
        if not isinstance(node.get(part), dict):
            node[part] = {}
        node = node[part]
    node[last] = value


def reset_value(config, target, key: str):
    rule = find_rule(config, target)
    if rule is None:
        return
    *parents, last = key.split(".")
    chain = [rule]
    for part in parents:
        nxt = chain[-1].get(part)
        if not isinstance(nxt, dict):
            return
        chain.append(nxt)
    chain[-1].pop(last, None)
    for parent, part in zip(reversed(chain[:-1]), reversed(parents)):  # drop emptied groups
        if not parent[part]:
            del parent[part]
    if target[0] == "type" and set(rule) == {"conditions"}:
        rules(config).remove(rule)  # a type rule with nothing set is just noise


def add_device(config, name: str, device_type: str):
    config.device_types[name] = device_type
    if find_rule(config, ("name", name)) is None:
        r = rules(config)
        r.insert(0, {"conditions": condition_for(("name", name))})


def remove_device(config, name: str):
    rule = find_rule(config, ("name", name))
    if rule is not None:
        rules(config).remove(rule)
    config.device_types.pop(name, None)


def connected_devices() -> list[tuple[str, str]]:
    """[(type, name)] from `inputactions devices list`."""
    try:
        out = subprocess.run(["inputactions", "devices", "list"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    found = []
    for line in out.splitlines():
        if m := re.match(r"^\[(\w+)\]\s+(.+)$", line):
            t = m.group(1).lower()
            if t in TYPES:
                found.append((t, m.group(2).strip()))
    return found


# Touchpad clicks, taps and one-finger gestures need InputActions to read the touchpad itself,
# which takes a udev rule from its install guide's "Additional setup".
TOUCHPAD_RULE = 'ENV{ID_INPUT_TOUCHPAD}=="1", TAG+="uaccess"'
TOUCHPAD_RULE_COMMAND = f"echo '{TOUCHPAD_RULE}' | sudo tee /etc/udev/rules.d/71-touchpad.rules"
TOUCHPAD_DOCS_URL = ("https://wiki.inputactions.org/main/getting-started/installation/kwin.html"
                     "#additional-setup-optional")
TOUCHPAD_ACCESS_TYPES = {"click", "tap"}


def touchpad_paths(listing: str) -> list[str]:
    """Device files of the touchpads in `inputactions devices list` output."""
    paths, in_touchpad = [], False
    for line in listing.splitlines():
        if line.startswith("["):
            in_touchpad = line.startswith("[Touchpad]")
        elif in_touchpad and (m := re.match(r"\s*Path:\s*(\S+)", line)):
            paths.append(m.group(1))
    return paths


def touchpad_access_missing() -> bool:
    """True when there's a touchpad InputActions can't read directly (the udev rule is missing).
    KWin runs as this user, so this user's access is InputActions' access."""
    try:
        out = subprocess.run(["inputactions", "devices", "list"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    out = re.sub(r"\x1b\[[0-9;]*m", "", out)
    return any(not os.access(p, os.R_OK | os.W_OK) for p in touchpad_paths(out) if os.path.exists(p))


def touchpad_access_dialog(parent):
    """Explains the missing udev rule, with the command to copy and a link to the docs."""
    dialog = QDialog(parent)
    dialog.setWindowTitle("Touchpad setup needed")
    icon = QLabel()
    icon.setPixmap(icons.lucide("triangle-alert", "#c58a00").pixmap(32, 32))
    text = QLabel("Touchpad clicks and taps only work once InputActions can read your touchpad "
                  "directly. That takes one command, then logging out and back in:")
    text.setWordWrap(True)
    command = CommandBox(TOUCHPAD_RULE_COMMAND, 480)  # the dialog's width less its margins
    docs = QPushButton(icons.lucide("book-open"), "Open docs")
    docs.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(TOUCHPAD_DOCS_URL)))
    close = QPushButton("Close")
    close.setDefault(True)
    close.clicked.connect(dialog.accept)
    buttons = QHBoxLayout()  # one row: Open docs on the left, Close on the right
    buttons.addWidget(docs)
    buttons.addStretch(1)
    buttons.addWidget(close)
    top = QHBoxLayout()
    top.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
    top.addWidget(text, 1)
    layout = QVBoxLayout(dialog)
    layout.addLayout(top)
    layout.addWidget(command)
    layout.addLayout(buttons)
    layout.setContentsMargins(20, 18, 20, 16)
    layout.setSpacing(12)
    dialog.setFixedWidth(520)
    dialog.exec()


def check_touchpad_access(parent, section: str, trigger_type: str):
    """Warn, once per binding being made, if this touchpad trigger can't work yet."""
    if section == "touchpad" and trigger_type in TOUCHPAD_ACCESS_TYPES and touchpad_access_missing():
        touchpad_access_dialog(parent)


def guess_type(rule) -> str:
    keys = set(rule) | {f"{k}.{s}" for k, v in rule.items() if isinstance(v, dict) for s in v}
    if keys & {"click_timeout", "motion_threshold_2", "motion_threshold_3", "buttonpad"}:
        return "touchpad"
    if keys & {"motion_timeout", "press_timeout", "unblock_buttons_on_timeout"}:
        return "mouse"
    return "mouse"


# UI --------------------------------------------------------------------------

class AddDeviceDialog(QDialog):
    def __init__(self, parent, devices: list[tuple[str, str]], taken: set[str]):
        super().__init__(parent)
        self.setWindowTitle("Device-specific settings")
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        for t, (label, icon) in TYPES.items():
            names = [n for dt, n in devices if dt == t]
            if not names or not SETTINGS[t]:
                continue
            top = QTreeWidgetItem([label])
            top.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.tree.addTopLevelItem(top)
            for n in names:
                item = QTreeWidgetItem([n])
                item.setIcon(0, icons.icon(icon))
                item.setData(0, Qt.ItemDataRole.UserRole, (t, n))
                if n in taken:
                    item.setFlags(Qt.ItemFlag.NoItemFlags)
                    item.setText(0, f"{n}  (already added)")
                top.addChild(item)
            top.setExpanded(True)
        self.tree.itemDoubleClicked.connect(lambda *_: self._accept())
        hint = QLabel("Pick a connected device. Its settings apply only to it, whenever it's plugged in.")
        hint.setWordWrap(True)
        self.error = QLabel()
        self.error.setStyleSheet("color: #d24545;")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(hint)
        layout.addWidget(self.tree)
        layout.addWidget(self.error)
        layout.addWidget(buttons)
        self.resize(420, 420)

    def _accept(self):
        item = self.tree.currentItem()
        if not item or not item.data(0, Qt.ItemDataRole.UserRole):
            self.error.setText("Pick a device first.")
            return
        self.accept()

    def choice(self) -> tuple[str, str]:
        return self.tree.currentItem().data(0, Qt.ItemDataRole.UserRole)


class SettingRow(QWidget):
    changed = Signal(object)  # new value
    reset = Signal()

    def __init__(self, s: Setting, value, is_set: bool):
        super().__init__()
        self.s = s
        text = QLabel(f"{s.label}<br><small style='color:palette(placeholder-text)'>{s.help}</small>"
                      if s.help else s.label)
        text.setWordWrap(True)
        self.state = QLabel()
        self.state.setFixedWidth(60)
        self.reset_button = QToolButton(icon=icons.icon("edit-undo"))
        self.reset_button.setAutoRaise(True)
        self.reset_button.setToolTip("Reset to default")
        self.reset_button.clicked.connect(self.reset.emit)
        keep = self.reset_button.sizePolicy()
        keep.setRetainSizeWhenHidden(True)  # rows stay aligned whether or not they can be reset
        self.reset_button.setSizePolicy(keep)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 6, 0, 6)
        row.addWidget(text, 1)
        if s.is_bool:
            self.check = Toggle()
            self.check.setChecked(bool(value))
            self.check.toggled.connect(lambda on: self.changed.emit(on))
            row.addWidget(self.check)
        elif s.default == 4294967295 or (isinstance(value, int) and value > s.hi):
            # Pressure "off" values are huge; a text box with a placeholder reads better than a slider.
            self.edit = QLineEdit("" if value == s.default else str(value))
            self.edit.setPlaceholderText("off" if s.default == 4294967295 else str(s.default))
            self.edit.setFixedWidth(90)
            self.edit.editingFinished.connect(self._edited)
            row.addWidget(self.edit)
        else:
            self.slider = QSlider(Qt.Orientation.Horizontal, minimum=s.lo, maximum=s.hi)
            self.slider.setFixedWidth(180)
            self.slider.setValue(int(value))
            self.value_label = QLabel(f"{value}{s.unit}")
            self.value_label.setFixedWidth(64)
            self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.slider.valueChanged.connect(self._slid)
            row.addWidget(self.slider)
            row.addWidget(self.value_label)
        row.addWidget(self.state)
        row.addWidget(self.reset_button)
        self.mark(is_set)

    def mark(self, is_set: bool):
        self.state.setText("changed" if is_set else "default")
        self.state.setStyleSheet("color: palette(link);" if is_set else "color: palette(placeholder-text);")
        self.reset_button.setVisible(is_set)

    def _slid(self, v: int):
        self.value_label.setText(f"{v}{self.s.unit}")
        self.changed.emit(v)

    def _edited(self):
        text = self.edit.text().strip()
        if not text:
            self.reset.emit()
        elif text.isdigit():
            self.changed.emit(int(text))


class DeviceSettingsPage(QWidget):
    """Left: device types and named devices. Right: the selected one's settings."""

    changed = Signal()

    def __init__(self, config: cfg.Config):
        super().__init__()
        self.config = config
        self.connected = connected_devices()
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(False)
        self.tree.currentItemChanged.connect(lambda item, _p: self._show(item))
        add = QPushButton(icons.icon("list-add"), "Device-specific settings…")
        add.clicked.connect(self._add_device)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        reset_all = QPushButton(icons.icon("edit-clear-all"), "Reset all to defaults")
        reset_all.clicked.connect(self._reset_all)
        ll.addWidget(self.tree)
        ll.addWidget(add)
        ll.addWidget(reset_all)
        self.form_host = QScrollArea(widgetResizable=True, frameShape=QFrame.Shape.NoFrame)
        split = QSplitter()
        split.addWidget(left)
        split.addWidget(self.form_host)
        split.setSizes([240, 600])
        QVBoxLayout(self).addWidget(split)
        self._fill()

    def _fill(self, select=("type", "mouse")):
        self.tree.blockSignals(True)
        self.tree.clear()

        def header(text):
            item = QTreeWidgetItem([text])
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.tree.addTopLevelItem(item)

        def entry(text, icon, payload, dim=False):
            item = QTreeWidgetItem([text])
            item.setIcon(0, icons.icon(icon))
            item.setData(0, Qt.ItemDataRole.UserRole, payload)
            if dim:
                item.setForeground(0, self.palette().placeholderText())
                item.setToolTip(0, "Not connected right now")
            self.tree.addTopLevelItem(item)
            return item

        items = {}
        header("Device types")
        for t, (label, icon) in TYPES.items():
            if SETTINGS[t]:  # keyboards have nothing to set
                items[("type", t)] = entry(label, icon, ("type", t))
        named = [target_of(r)[1] for r in rules(self.config) if (target_of(r) or ("",))[0] == "name"]
        if named:
            header("Specific devices")
            connected = {n for _t, n in self.connected}
            for n in named:
                t = self.config.device_types.get(n) or guess_type(find_rule(self.config, ("name", n)))
                label = n if n in connected else f"{n}  · not connected"
                items[("name", n)] = entry(label, TYPES[t][1], ("name", n), dim=n not in connected)
        if other_rules(self.config):
            header("Advanced")
            items[("other", "")] = entry("Other rules (YAML)", "text-x-script", ("other", ""))
        self.tree.blockSignals(False)
        self.tree.setCurrentItem(items.get(select) or items[("type", "mouse")])

    def _type_of(self, target) -> str:
        if target[0] == "type":
            return target[1]
        return self.config.device_types.get(target[1]) or guess_type(find_rule(self.config, target) or {})

    def _show(self, item):
        if not item:
            return
        target = item.data(0, Qt.ItemDataRole.UserRole)
        if target[0] == "other":
            self.form_host.setWidget(self._other_form())
            return
        t = self._type_of(target)
        rule = find_rule(self.config, target) or {}
        base = (find_rule(self.config, ("type", t)) or {}) if target[0] == "name" else {}

        form = QWidget()
        layout = QVBoxLayout(form)
        head = QHBoxLayout()
        title = QLabel(TYPES[t][0] if target[0] == "type" else target[1])
        title.setStyleSheet("font-weight: 600; font-size: 14px;")
        head.addWidget(title, 1)
        if target[0] == "name":
            remove = QPushButton(icons.icon("edit-delete"), "Remove")
            remove.clicked.connect(lambda: self._remove_device(target[1]))
            head.addWidget(remove)
        layout.addLayout(head)
        sub = QLabel(f"Applies to every {t}." if target[0] == "type" else
                     f"Only settings you change here apply. Everything else follows the {t} settings.")
        sub.setStyleSheet("color: palette(placeholder-text);")
        layout.addWidget(sub)

        def add_row(s: Setting):
            own = get_value(rule, s.key)
            inherited = get_value(base, s.key)
            value = own if own is not None else inherited if inherited is not None else s.default
            r = SettingRow(s, value, own is not None)
            r.changed.connect(lambda v, s=s, r=r: self._set(target, s, v, r))
            r.reset.connect(lambda s=s: self._reset(target, s))
            layout.addWidget(r)

        settings = SETTINGS[t]
        for s in settings:
            if not s.advanced:
                add_row(s)
        advanced = [s for s in settings if s.advanced]
        if advanced:
            toggle = QToolButton(text="Advanced", checkable=True)
            toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            toggle.setArrowType(Qt.ArrowType.RightArrow)
            toggle.setAutoRaise(True)
            box = QWidget()
            bl = QVBoxLayout(box)
            bl.setContentsMargins(12, 0, 0, 0)
            layout.addWidget(toggle)
            layout.addWidget(box)
            outer = layout
            layout = bl
            for s in advanced:
                add_row(s)
            layout = outer
            box.setVisible(False)
            toggle.toggled.connect(lambda on: (box.setVisible(on), toggle.setArrowType(
                Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow)))
        layout.addStretch(1)
        self.form_host.setWidget(form)

    def _set(self, target, s: Setting, value, row: SettingRow):
        set_value(self.config, target, s.key, value)
        row.mark(True)
        self.changed.emit()

    def _reset(self, target, s: Setting):
        reset_value(self.config, target, s.key)
        self.changed.emit()
        self._fill(target)

    def _reset_all(self):
        n = len(rules(self.config))
        if not n:
            QMessageBox.information(self, "Actuator", "Everything is already at its defaults.")
            return
        answer = QMessageBox.warning(
            self, "Reset all device settings?",
            f"This removes all {n} device rule{'s' if n != 1 else ''}, including device-specific ones, "
            "so every device uses InputActions' defaults.\n\nIt takes effect when you save, and the "
            "save can be undone from ☰ → Restore.",
            QMessageBox.StandardButton.Reset | QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Reset:
            return
        rules(self.config).clear()
        self.config.device_types.clear()
        self.changed.emit()
        self._fill()

    def _add_device(self):
        taken = {target_of(r)[1] for r in rules(self.config) if (target_of(r) or ("",))[0] == "name"}
        dialog = AddDeviceDialog(self, self.connected, taken)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        t, name = dialog.choice()
        add_device(self.config, name, t)
        self.changed.emit()
        self._fill(("name", name))

    def _remove_device(self, name: str):
        remove_device(self.config, name)
        self.changed.emit()
        self._fill()

    def _other_form(self) -> QWidget:
        form = QWidget()
        layout = QVBoxLayout(form)
        layout.addWidget(QLabel("These rules use conditions the form doesn't cover. Edit them as YAML:"))
        edit = QPlainTextEdit(d.to_yaml(other_rules(self.config)))
        error = QLabel()
        error.setStyleSheet("color: #d24545;")
        apply = QPushButton("Apply")

        def do_apply():
            try:
                new = YAML(typ="safe").load(edit.toPlainText()) or []
                if not isinstance(new, list) or not all(isinstance(r, dict) for r in new):
                    raise ValueError("Rules must be a list.")
            except Exception as e:  # noqa: BLE001
                error.setText(f"Not applied: {e}")
                return
            r = rules(self.config)
            r[:] = [x for x in r if target_of(x) is not None] + new
            self.changed.emit()
            self._fill(("other", "") if new else ("type", "mouse"))

        apply.clicked.connect(do_apply)
        layout.addWidget(edit, 1)
        layout.addWidget(error)
        layout.addWidget(apply, 0, Qt.AlignmentFlag.AlignRight)
        return form


class DeviceSettingsDialog(QDialog):
    """Device settings in their own window. Edits a copy; Save hands back the new rules."""

    def __init__(self, parent, config: cfg.Config):
        super().__init__(parent)
        self.setWindowTitle("Device settings")
        self.copy = cfg.Config(path=config.path, data={"device_rules": d.plain(rules(config))},
                               device_types=dict(config.device_types))
        self.dirty = False
        page = DeviceSettingsPage(self.copy)
        page.changed.connect(lambda: setattr(self, "dirty", True))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(page, 1)
        layout.addWidget(buttons)
        self.resize(900, 600)

    def apply_to(self, config: cfg.Config):
        config.data["device_rules"] = d.plain(rules(self.copy))
        config.device_types = dict(self.copy.device_types)
