"""Converting between what people type ("Ctrl+PgUp") and InputActions key names."""

from PySide6.QtCore import Qt

from .describe import KEY_NAMES

ALIASES = {
    "ctrl": "leftctrl", "control": "leftctrl",
    "shift": "leftshift",
    "alt": "leftalt", "altgr": "rightalt",
    "meta": "leftmeta", "super": "leftmeta", "win": "leftmeta", "cmd": "leftmeta",
    "pgup": "pageup", "pgdn": "pagedown", "pagedn": "pagedown",
    "del": "delete", "ins": "insert", "escape": "esc", "return": "enter",
    "[": "leftbrace", "]": "rightbrace", "-": "minus", "=": "equal", ";": "semicolon",
    "'": "apostrophe", "`": "grave", ",": "comma", ".": "dot", "/": "slash", "\\": "backslash",
}
MODIFIER_ORDER = ["leftctrl", "rightctrl", "leftshift", "rightshift", "leftalt", "rightalt", "leftmeta", "rightmeta"]


def to_name(part: str) -> str:
    p = part.strip()
    if not p:
        raise ValueError("Empty key name.")
    low = p.lower()
    if low in ALIASES:
        return ALIASES[low]
    if low in KEY_NAMES or low.isidentifier():
        return low
    if len(low) == 1 and low.isalnum():
        return low
    raise ValueError(f"Unknown key: {p}")


def parse_combo(text: str) -> str:
    """'Ctrl+Shift+T' → 'leftctrl+leftshift+t'. A lone '+' or '-' key isn't supported here."""
    return "+".join(to_name(p) for p in text.split("+"))


def parse_sequence(text: str) -> list[str]:
    """'Ctrl+C, Ctrl+V' → ['leftctrl+c', 'leftctrl+v']."""
    combos = [parse_combo(c) for c in text.split(",") if c.strip()]
    if not combos:
        raise ValueError("Enter at least one key.")
    return combos


# Qt keys whose InputActions name isn't just the lowercase key text.
QT_KEYS = {
    Qt.Key.Key_BracketLeft: "leftbrace", Qt.Key.Key_BracketRight: "rightbrace",
    Qt.Key.Key_PageUp: "pageup", Qt.Key.Key_PageDown: "pagedown",
    Qt.Key.Key_Return: "enter", Qt.Key.Key_Enter: "kpenter", Qt.Key.Key_Escape: "esc",
    Qt.Key.Key_Backspace: "backspace", Qt.Key.Key_Delete: "delete", Qt.Key.Key_Insert: "insert",
    Qt.Key.Key_Tab: "tab", Qt.Key.Key_Backtab: "tab", Qt.Key.Key_Space: "space",
    Qt.Key.Key_Home: "home", Qt.Key.Key_End: "end",
    Qt.Key.Key_Left: "left", Qt.Key.Key_Right: "right", Qt.Key.Key_Up: "up", Qt.Key.Key_Down: "down",
    Qt.Key.Key_Minus: "minus", Qt.Key.Key_Equal: "equal", Qt.Key.Key_Semicolon: "semicolon",
    Qt.Key.Key_Apostrophe: "apostrophe", Qt.Key.Key_QuoteLeft: "grave", Qt.Key.Key_Comma: "comma",
    Qt.Key.Key_Period: "dot", Qt.Key.Key_Slash: "slash", Qt.Key.Key_Backslash: "backslash",
}
MODIFIER_KEYS = {Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta,
                 Qt.Key.Key_Super_L, Qt.Key.Key_Super_R, Qt.Key.Key_AltGr}


def from_key_event(event) -> str | None:
    """A key press → 'leftshift+leftmeta+leftbrace', or None for a lone modifier."""
    key = Qt.Key(event.key())
    if key in MODIFIER_KEYS:
        return None
    mods = event.modifiers()
    parts = []
    if mods & Qt.KeyboardModifier.ControlModifier:
        parts.append("leftctrl")
    if mods & Qt.KeyboardModifier.ShiftModifier:
        parts.append("leftshift")
    if mods & Qt.KeyboardModifier.AltModifier:
        parts.append("leftalt")
    if mods & Qt.KeyboardModifier.MetaModifier:
        parts.append("leftmeta")
    if key in QT_KEYS:
        name = QT_KEYS[key]
    elif Qt.Key.Key_F1 <= key <= Qt.Key.Key_F35:
        name = f"f{key - Qt.Key.Key_F1 + 1}"
    elif Qt.Key.Key_A <= key <= Qt.Key.Key_Z or Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
        name = chr(key).lower()
    else:
        return None
    return "+".join(parts + [name])


# Qt mouse buttons → InputActions mouse button names.
MOUSE_BUTTONS = {
    Qt.MouseButton.LeftButton: "left",
    Qt.MouseButton.RightButton: "right",
    Qt.MouseButton.MiddleButton: "middle",
    Qt.MouseButton.BackButton: "back",
    Qt.MouseButton.ForwardButton: "forward",
    Qt.MouseButton.TaskButton: "task",
}
