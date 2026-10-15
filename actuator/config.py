"""Loading an InputActions config into a model the UI can show."""

import re
from dataclasses import dataclass, field
from pathlib import Path

from ruamel.yaml import YAML

CONFIG_PATH = Path.home() / ".config" / "inputactions" / "config.yaml"
# InputActions prefers this over the user's config when it exists (see its docs: config.html).
SYSTEM_CONFIG_PATH = Path("/etc/inputactions/config.yaml")
# First line of every file Actuator writes; files without it get the one-time "reorganize" notice.
MANAGED_HEADER = "# InputActions configuration, managed by Actuator."

# YAML section key for each device tab, in display order.
DEVICES = {
    "Mouse": "mouse",
    "Keyboard": "keyboard",
    "Touchpad": "touchpad",
    "Touchscreen": "touchscreen",
    "Pointer": "pointer",
}
# The tabs Actuator shows. Pointer (hover) bindings are still read and kept in the file, just not shown.
DEVICE_TABS = {title: section for title, section in DEVICES.items() if section != "pointer"}

# Button registry lines Actuator writes at the top of the file:
#   # actuator:button forward = Side button 1
#   # actuator:button leftshift+leftmeta+leftbrace = Wheel tilt left
BUTTON_LINE = re.compile(r"^\s*#\s*actuator:button\s+(\S+)\s*=\s*(.+?)\s*$")
APP_LINE = re.compile(r"^\s*#\s*actuator:app\s+(.+?)\s*$")  # app names can have spaces
# A note is a comment right above its binding:  # Note: Opens the inventory
# ("# Label:" is read too, from before it was called a note.)
LABEL_LINE = re.compile(r"^\s*#\s*(?:Note|Label):\s*(.*?)\s*$")
ID_LINE = re.compile(r"^\s*-\s*id:\s*['\"]?([^'\"\s#]+)")
DEVICE_LINE = re.compile(r"^\s*#\s*actuator:device\s+(.+?)\s*=\s*(\w+)\s*$")
FOLDER_ICON_LINE = re.compile(r"^\s*#\s*actuator:foldericon\s+(.+?)\s*=\s*([a-z0-9-]+)\s*$")
FOLDER_LINE = re.compile(r"^\s*#\s*actuator:folder\s+(.+?)\s*=\s*(.*?)\s*$")
PREF_LINE = re.compile(r"^\s*#\s*actuator:pref\s+(\S+)\s*=\s*(.*?)\s*$")  # Actuator's own options
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
    folders: dict[str, list[str]] = field(default_factory=dict)  # folder name -> apps, in order
    folder_icons: dict[str, str] = field(default_factory=dict)  # folder name -> Lucide icon name
    device_types: dict[str, str] = field(default_factory=dict)  # named device -> mouse/touchpad/...
    labels: dict[str, str] = field(default_factory=dict)  # binding id -> the user's note
    prefs: dict[str, str] = field(default_factory=dict)  # Actuator's options, when stored in the config

    @property
    def apps(self) -> list[str]:
        found: dict[str, str] = {}
        for a in [b.app for b in self.bindings if b.app] + sorted(self.extra_apps):
            found.setdefault(a.lower(), a)  # "factorio" and "Factorio" are the same app
        return sorted(found.values(), key=str.lower)

    def same_app(self, app: str) -> str:
        """The existing spelling of an app, ignoring case, or app itself if new."""
        return next((a for a in self.apps if a.lower() == app.lower()), app)

    @property
    def device_rules(self) -> list:
        return list(self.data.get("device_rules") or [])

    def bindings_for(self, scope: str, app: str | None = None, section: str | None = None):
        return [
            b
            for b in self.bindings
            if b.scope == scope
            and (scope != "app" or (b.app or "").lower() == (app or "").lower())
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
        self.extra_apps.add(self.same_app(app))

    def folder_of(self, app: str) -> str | None:
        return next((f for f, apps in self.folders.items() if app.lower() in map(str.lower, apps)), None)

    def move_to_folder(self, app: str, folder: str | None):
        for apps in self.folders.values():
            if app in apps:
                apps.remove(app)
        if folder:
            self.folders.setdefault(folder, []).append(app)

    def rename_folder(self, old: str, new: str):
        self.folders = {new if f == old else f: apps for f, apps in self.folders.items()}
        if old in self.folder_icons:
            self.folder_icons[new] = self.folder_icons.pop(old)

    def delete_folder(self, name: str):
        self.folders.pop(name, None)
        self.folder_icons.pop(name, None)

    def set_folder_icon(self, name: str, icon: str | None):
        if icon:
            self.folder_icons[name] = icon
        else:
            self.folder_icons.pop(name, None)

    def remove_app(self, app: str):
        self.extra_apps.discard(app)
        self.move_to_folder(app, None)
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

    def set_label(self, binding_id: str, text: str):
        text = " ".join(text.split())  # one line: it lives in a comment
        if text:
            self.labels[binding_id] = text
        else:
            self.labels.pop(binding_id, None)

    def add_button(self, src: str, name: str):
        self.button_names[src] = name
        self.reindex()

    def reindex(self):
        self.bindings, self.buttons = [], []
        canonical: dict[str, str] = {}
        buttons: dict[str, Button] = {}
        for section in DEVICES.values():
            gestures = (self.data.get(section) or {}).get("gestures") or []
            for i, g in enumerate(gestures):
                if not isinstance(g, dict):
                    continue
                scope, app = parse_scope(g.get("conditions"))
                if app:
                    app = canonical.setdefault(app.lower(), app)
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
    """The app name from a window class condition's value: '(?i)^Factorio$' → 'Factorio'."""
    value = value.strip().strip("'\"")
    value = re.sub(r"^\(\?i\)", "", value)
    value = value.removeprefix("^").removesuffix("$")
    return re.sub(r"\\(.)", r"\1", value)  # undo regex escaping


def parse_scope(conditions) -> tuple[str, str | None]:
    """App if there's exactly one window class check (plus any extras the form can show),
    global if there's none, custom if the conditions are beyond the form."""
    from .conditions import split  # local: conditions has no dependency on config

    parts = split(conditions)
    if parts is None:
        return "custom", None
    app, _mode, _rows = parts
    if app:
        return "app", app_key(WINDOW_CLASS.match(app.strip()).group(2))
    return "global", None


def shortcut_key(keys) -> str:
    return "+".join(str(k) for k in keys)


def ensure_exists(path: Path | None = None) -> bool:
    """Create an empty config where InputActions looks for one. True if it had to."""
    path = path or CONFIG_PATH
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(MANAGED_HEADER + "\n")  # new and empty: nothing to reorganize or keep a copy of
    return True


def load(path: Path | None = None) -> Config:
    path = path or CONFIG_PATH
    return loads(path.read_text(), path)


def loads(text: str, path: Path | None = None) -> Config:
    """A config from YAML text (as read from disk, or an undo step)."""
    path = path or CONFIG_PATH
    config = Config(path=path, data=YAML(typ="rt").load(text) or {})
    for line in text.splitlines():
        if m := BUTTON_LINE.match(line):
            from .describe import button_label
            # "back = back" or "back = Back" just record the button; only a different name was chosen.
            if m.group(2) not in (m.group(1), button_label(m.group(1))):
                config.button_names[m.group(1)] = m.group(2)
        elif m := APP_LINE.match(line):
            config.extra_apps.add(m.group(1))
        elif m := DEVICE_LINE.match(line):
            config.device_types[m.group(1)] = m.group(2)
        elif m := FOLDER_ICON_LINE.match(line):
            config.folder_icons[m.group(1)] = m.group(2)
        elif m := FOLDER_LINE.match(line):
            config.folders[m.group(1)] = [a.strip() for a in m.group(2).split(",") if a.strip()]
        elif m := PREF_LINE.match(line):
            config.prefs[m.group(1)] = m.group(2)
    # Notes: a "# Note:" comment applies to the next binding (its "- id:" line).
    pending = None
    for line in text.splitlines():
        if m := LABEL_LINE.match(line):
            pending = m.group(1)
        elif m := ID_LINE.match(line):
            if pending:
                config.labels[m.group(1)] = pending
            pending = None
        elif line.strip() and not line.lstrip().startswith("#"):
            pending = None
    config.reindex()
    return config
