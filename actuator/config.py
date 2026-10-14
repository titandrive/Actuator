"""Loading an InputActions config into a model the UI can show."""

import re
from dataclasses import dataclass, field
from pathlib import Path

from ruamel.yaml import YAML

CONFIG_PATH = Path.home() / ".config" / "inputactions" / "config.yaml"

# YAML section key for each device tab, in display order.
DEVICES = {
    "Mouse": "mouse",
    "Touchpad": "touchpad",
    "Keyboard": "keyboard",
    "Touchscreen": "touchscreen",
    "Pointer": "pointer",
}

# Button registry lines Actuator writes at the top of the file:
#   # actuator:button forward = Side button 1
#   # actuator:button leftshift+leftmeta+leftbrace = Wheel tilt left
BUTTON_LINE = re.compile(r"^\s*#\s*actuator:button\s+(\S+)\s*=\s*(.+?)\s*$")
APP_LINE = re.compile(r"^\s*#\s*actuator:app\s+(\S+)\s*$")
WINDOW_CLASS = re.compile(r"^\$window_class\s*(==|matches)\s*(.+)$")


@dataclass
class Button:
    src: str  # raw input: a mouse button name, or a shortcut joined with "+"
    name: str
    kind: str  # "mouse" (physical button) or "shortcut" (emitted by mouse firmware)


@dataclass
class Binding:
    section: str  # YAML section key, e.g. "mouse"
    index: int  # position in that section's gestures list
    data: dict
    scope: str  # "global", "app" or "custom"
    app: str | None  # lowercase app key when scope == "app"
    button: str | None  # Button.src when this binding is a mouse button

    @property
    def id(self) -> str:
        return str(self.data.get("id", f"{self.section}[{self.index}]"))

    @property
    def type(self) -> str:
        return str(self.data.get("type", ""))


@dataclass
class Config:
    path: Path
    data: dict
    bindings: list[Binding] = field(default_factory=list)
    buttons: list[Button] = field(default_factory=list)
    button_names: dict[str, str] = field(default_factory=dict)  # src -> user's name
    extra_apps: set[str] = field(default_factory=set)  # apps kept without bindings

    @property
    def apps(self) -> list[str]:
        return sorted({b.app for b in self.bindings if b.app} | self.extra_apps)

    @property
    def device_rules(self) -> list:
        return list(self.data.get("device_rules") or [])

    def bindings_for(self, scope: str, app: str | None = None, section: str | None = None):
        return [
            b
            for b in self.bindings
            if b.scope == scope
            and (scope != "app" or b.app == app)
            and (section is None or b.section == section)
        ]

    # Edits. Each one changes the data and re-indexes; nothing touches disk.

    def rename_button(self, src: str, name: str):
        name = name.strip()
        if name and name != src:
            self.button_names[src] = name
        else:
            self.button_names.pop(src, None)
        self.reindex()

    def add_app(self, app: str):
        self.extra_apps.add(app_key(app))

    def remove_app(self, app: str):
        self.extra_apps.discard(app)
        self.remove_bindings(self.bindings_for("app", app))

    def add_binding(self, section: str, data: dict) -> Binding:
        body = self.data.get(section)
        if not isinstance(body, dict):
            body = self.data[section] = {}
        if not isinstance(body.get("gestures"), list):
            body["gestures"] = []
        body["gestures"].append(data)
        self.reindex()
        return next(b for b in self.bindings if b.data is data)

    def remove_bindings(self, bindings: list[Binding]):
        # By position, highest first, so equal-looking bindings can't be confused.
        for b in sorted(bindings, key=lambda b: (b.section, b.index), reverse=True):
            del self.data[b.section]["gestures"][b.index]
        self.reindex()

    def add_button(self, src: str, name: str):
        self.button_names[src] = name
        self.reindex()

    def reindex(self):
        self.bindings, self.buttons = [], []
        buttons: dict[str, Button] = {}
        for section in DEVICES.values():
            gestures = (self.data.get(section) or {}).get("gestures") or []
            for i, g in enumerate(gestures):
                if not isinstance(g, dict):
                    continue
                scope, app = parse_scope(g.get("conditions"))
                button = None
                if section == "mouse" and g.get("type") == "press":
                    mb = g.get("mouse_buttons") or []
                    if len(mb) == 1:
                        button = str(mb[0])
                        buttons.setdefault(button, Button(button, self.button_names.get(button, button), "mouse"))
                elif section == "keyboard" and g.get("type") == "shortcut":
                    key = shortcut_key(g.get("shortcut") or [])
                    # Without a name, guess from the id until the user names it.
                    if key in self.button_names or "mouse" in str(g.get("id", "")):
                        button = key
                        buttons.setdefault(key, Button(key, self.button_names.get(key, key), "shortcut"))
                self.bindings.append(Binding(section, i, g, scope, app, button))
        # Named buttons with no bindings yet still belong in the list.
        for src, name in self.button_names.items():
            buttons.setdefault(src, Button(src, name, "shortcut" if "+" in src else "mouse"))
        self.buttons = list(buttons.values())


def app_key(value: str) -> str:
    value = value.strip().strip("'\"")
    value = re.sub(r"^\(\?i\)", "", value)
    return value.lower()


def parse_scope(conditions) -> tuple[str, str | None]:
    """Global if unconditional, app if exactly one window class check, else custom."""
    if conditions is None:
        return "global", None
    items = conditions if isinstance(conditions, list) else [conditions]
    if len(items) == 1 and isinstance(items[0], str):
        m = WINDOW_CLASS.match(items[0].strip())
        if m:
            return "app", app_key(m.group(2))
    return "custom", None


def shortcut_key(keys) -> str:
    return "+".join(str(k) for k in keys)


def load(path: Path | None = None) -> Config:
    path = path or CONFIG_PATH
    text = path.read_text()
    config = Config(path=path, data=YAML(typ="rt").load(text) or {})
    for line in text.splitlines():
        if m := BUTTON_LINE.match(line):
            config.button_names[m.group(1)] = m.group(2)
        elif m := APP_LINE.match(line):
            config.extra_apps.add(m.group(1))
    config.reindex()
    return config
