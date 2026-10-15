"""Converting between what people type ("Ctrl+PgUp") and InputActions key names."""

from PySide6.QtCore import Qt

from . import scancodes
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

# What the app shows → the key: so "KbdLightUp" or "Display", as shown, can be typed back in.
SHOWN_AS = {}
for _key, _shown in KEY_NAMES.items():
    SHOWN_AS.setdefault(_shown.lower(), _key)


def to_name(part: str) -> str:
    p = part.strip()
    if not p:
        raise ValueError("Empty key name.")
    low = p.lower()
    if low in ALIASES:
        return ALIASES[low]
    if low in SHOWN_AS:
        return SHOWN_AS[low]
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
    Qt.Key.Key_CapsLock: "capslock", Qt.Key.Key_Print: "sysrq", Qt.Key.Key_Pause: "pause",
    # With Shift held, Qt reports the shifted symbol; map it back to the physical key (US layout).
    Qt.Key.Key_BraceLeft: "leftbrace", Qt.Key.Key_BraceRight: "rightbrace",
    Qt.Key.Key_Underscore: "minus", Qt.Key.Key_Plus: "equal", Qt.Key.Key_Colon: "semicolon",
    Qt.Key.Key_QuoteDbl: "apostrophe", Qt.Key.Key_AsciiTilde: "grave", Qt.Key.Key_Less: "comma",
    Qt.Key.Key_Greater: "dot", Qt.Key.Key_Question: "slash", Qt.Key.Key_Bar: "backslash",
    Qt.Key.Key_Exclam: "1", Qt.Key.Key_At: "2", Qt.Key.Key_NumberSign: "3", Qt.Key.Key_Dollar: "4",
    Qt.Key.Key_Percent: "5", Qt.Key.Key_AsciiCircum: "6", Qt.Key.Key_Ampersand: "7",
    Qt.Key.Key_Asterisk: "8", Qt.Key.Key_ParenLeft: "9", Qt.Key.Key_ParenRight: "0",
    Qt.Key.Key_VolumeUp: "volumeup", Qt.Key.Key_VolumeDown: "volumedown", Qt.Key.Key_VolumeMute: "mute",
    Qt.Key.Key_MediaPlay: "playpause", Qt.Key.Key_MediaTogglePlayPause: "playpause",
    Qt.Key.Key_MediaNext: "nextsong", Qt.Key.Key_MediaPrevious: "previoussong",
}
# A modifier pressed and released on its own records as that key.
LONE_MODIFIERS = {
    Qt.Key.Key_Control: "leftctrl", Qt.Key.Key_Shift: "leftshift", Qt.Key.Key_Alt: "leftalt",
    Qt.Key.Key_Meta: "leftmeta", Qt.Key.Key_Super_L: "leftmeta", Qt.Key.Key_Super_R: "rightmeta",
    Qt.Key.Key_AltGr: "rightalt",
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
    # InputActions matches keys by scancode, so the physical key is the right answer, whatever the
    # layout, and it covers keys Qt has no name for (brightness, mic mute and other top-row keys).
    name = scancodes.name(event.nativeScanCode()) or _qt_key_name(key)
    if not name or name in MODIFIER_ORDER:
        return None
    return "+".join(parts + [name])


def _qt_key_name(key: Qt.Key) -> str | None:
    """Fallback when there's no scancode (not on Linux, or a synthetic event)."""
    if key in QT_KEYS:
        return QT_KEYS[key]
    if Qt.Key.Key_F1 <= key <= Qt.Key.Key_F35:
        return f"f{key - Qt.Key.Key_F1 + 1}"
    if Qt.Key.Key_A <= key <= Qt.Key.Key_Z or Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
        return chr(key).lower()
    return None


# Qt mouse buttons → InputActions mouse button names.
MOUSE_BUTTONS = {
    Qt.MouseButton.LeftButton: "left",
    Qt.MouseButton.RightButton: "right",
    Qt.MouseButton.MiddleButton: "middle",
    Qt.MouseButton.BackButton: "back",
    Qt.MouseButton.ForwardButton: "forward",
    Qt.MouseButton.TaskButton: "task",
}


# What people can type, grouped for the key list. Each entry is the typed form.
KEY_GROUPS = {
    "Modifiers": ["Ctrl", "Shift", "Alt", "AltGr", "Meta"],
    "Letters": [chr(c) for c in range(ord("A"), ord("Z") + 1)],
    "Numbers": [str(n) for n in range(10)],
    "Function keys": [f"F{n}" for n in range(1, 25)],
    "Navigation": ["Up", "Down", "Left", "Right", "Home", "End", "PgUp", "PgDn"],
    "Editing": ["Enter", "Tab", "Space", "Backspace", "Del", "Ins", "Esc", "CapsLock"],
    "Symbols": ["-", "=", "[", "]", ";", "'", "`", ",", ".", "/", "\\"],
    "Media": ["VolumeUp", "VolumeDown", "Mute", "PlayPause", "NextSong", "PreviousSong",
              "BrightnessUp", "BrightnessDown"],
    "Other": ["SysRq", "Pause", "ScrollLock", "Menu"],
}
