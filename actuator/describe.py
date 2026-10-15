"""Human-readable text for triggers, conditions and actions."""

import io

from ruamel.yaml import YAML

KEY_NAMES = {
    "leftctrl": "Ctrl", "rightctrl": "Ctrl",
    "leftshift": "Shift", "rightshift": "Shift",
    "leftmeta": "Meta", "rightmeta": "Meta",
    "leftalt": "Alt", "rightalt": "AltGr",
    "pageup": "PgUp", "pagedown": "PgDn",
    "leftbrace": "[", "rightbrace": "]",
    "minus": "-", "equal": "=", "semicolon": ";", "apostrophe": "'",
    "grave": "`", "comma": ",", "dot": ".", "slash": "/", "backslash": "\\",
    "esc": "Esc", "delete": "Del", "insert": "Ins",
    # Top-row and media keys. Written without spaces so they can be typed back in as shown.
    "volumeup": "VolumeUp", "volumedown": "VolumeDown", "mute": "Mute", "micmute": "MicMute",
    "playpause": "PlayPause", "nextsong": "NextSong", "previoussong": "PreviousSong",
    "brightnessup": "BrightnessUp", "brightnessdown": "BrightnessDown",
    "kbdillumup": "KbdLightUp", "kbdillumdown": "KbdLightDown", "kbdillumtoggle": "KbdLight",
    "switchvideomode": "Display", "touchpad_toggle": "TouchpadToggle", "rfkill": "Airplane",
    "calc": "Calculator", "search": "Search", "sysrq": "PrintScreen", "selectivescreenshot": "Screenshot",
    "notification_center": "Notifications", "pickup_phone": "PickUp", "hangup_phone": "HangUp",
}

# InputActions' directions in words. "left_right" fires for either direction, not left-then-right.
DIRECTIONS = {
    "left_right": "left or right", "up_down": "up or down", "any": "any direction",
}
SWIPE_DIRECTIONS = {
    "left_right": "either direction (left/right)", "up_down": "either direction (up/down)",
}


def direction(value, trigger_type: str = "") -> str:
    value = str(value)
    if trigger_type == "swipe" and value in SWIPE_DIRECTIONS:
        return SWIPE_DIRECTIONS[value]
    return DIRECTIONS.get(value, value.replace("_", " "))


STAGES = {
    "begin": "On begin",
    "update": "On update",
    "tick": "Every tick",
    "end": "On end",
    "cancel": "On cancel",
    "end_cancel": "On end or cancel",
}


def key(name: str) -> str:
    name = str(name)
    if name in KEY_NAMES:
        return KEY_NAMES[name]
    return name.upper() if len(name) == 1 else name.capitalize()


def combo(text: str) -> str:
    return "+".join(key(k) for k in str(text).split("+"))


def shortcut(keys) -> str:
    return "+".join(key(k) for k in keys)


def button_label(src: str) -> str:
    """How a button reads in the app: "forward" → "Forward", "leftmeta+w" → "Meta+W"."""
    return combo(src) if "+" in src else src[:1].upper() + src[1:]


def token(t) -> str:
    """One input token: 'leftctrl+w' taps, '+f' presses, '-f' releases."""
    if isinstance(t, dict) and set(t) == {"text"}:
        return f'Type "{t["text"]}"' if isinstance(t["text"], str) else "Type text"
    if not isinstance(t, str):
        return str(t)
    if t.startswith("+") and len(t) > 1:
        return f"Hold {combo(t[1:])}"
    if t.startswith("-") and len(t) > 1:
        return f"Release {combo(t[1:])}"
    return combo(t)


def action_steps(a: dict) -> list[str]:
    if "input" in a:
        steps = []
        for item in a.get("input") or []:
            if not isinstance(item, dict):
                steps.append(str(item))
                continue
            for device, toks in item.items():
                for t in toks if isinstance(toks, list) else [toks]:
                    if device == "mouse" and isinstance(t, str) and t.isalnum():
                        steps.append(f"{button_label(t)} click")  # a plain button, not a move or wheel
                    else:
                        steps.append(token(t))
        return steps
    for k, label in (
        ("command", "Run"),
        ("plasma_shortcut", "Plasma shortcut"),
        ("activate_window", "Activate window"),
        ("replace_text", "Replace text"),
        ("sleep", "Wait"),
        ("function", "Function"),
    ):
        if k in a:
            return [f"{label}: {a[k]}"]
    if "one" in a:
        return [f"Pick one of {len(a['one'] or [])}"]
    return [", ".join(k for k in a if k != "on") or "Nothing"]


def stage(a: dict) -> str:
    on = str(a.get("on", "end"))
    label = STAGES.get(on, f"On {on}")
    if "interval" in a:
        n = a["interval"]
        label = f"Every {n:+g}" if isinstance(n, (int, float)) else f"Every {n}"
    return label


def stages(actions) -> list[tuple[str, list[str]]]:
    """Group actions by lifecycle stage, keeping first-seen order."""
    out: dict[str, list[str]] = {}
    for a in actions or []:
        if isinstance(a, dict):
            out.setdefault(stage(a), []).extend(action_steps(a))
    return list(out.items())


def summary(binding) -> str:
    from .conditions import extra_count

    text = "Nothing"
    for _stage, steps in stages(binding.data.get("actions")):
        if steps:
            text = steps[0] + (" …" if len(steps) > 1 else "")
            break
    n = extra_count(binding.data.get("conditions"))
    if n > 0:
        text += f"  (only if +{n})"
    elif n < 0:
        text += "  (conditions in YAML)"
    return text


def trigger_parts(g: dict) -> list[str]:
    parts = [str(g.get("type", "?")).capitalize()]
    if "fingers" in g:
        parts.append(f"{g['fingers']} fingers")
    if "direction" in g:
        parts.append(direction(g["direction"], str(g.get("type", ""))))
    if "mouse_buttons" in g:
        parts.append(" + ".join(str(b) for b in g["mouse_buttons"]))
    if "shortcut" in g:
        parts.append(shortcut(g["shortcut"]))
    for flag in ("instant", "accelerated"):
        if g.get(flag):
            parts.append(flag)
    if "block_events" in g:
        parts.append("blocks events" if g["block_events"] else "passes events")
    for opt in ("threshold", "speed", "resume_timeout"):
        if opt in g:
            parts.append(f"{opt.replace('_', ' ')} {g[opt]}")
    return parts


def gesture_title(g: dict) -> str:
    t = str(g.get("type", "?"))
    fingers = f"{g['fingers']}-finger " if "fingers" in g else ""
    where = f" {direction(g['direction'], t)}" if "direction" in g else ""
    if t == "shortcut":
        return shortcut(g.get("shortcut") or []) or "New shortcut"
    speed = f"{g['speed']} " if g.get("speed") in ("fast", "slow") else ""
    return f"{speed}{fingers}{t}{where}".capitalize()


def condition(c) -> str:
    if isinstance(c, str):
        text = c.strip()
        if text.startswith("$window_class "):
            text = text.replace("$window_class ==", "Window class is", 1)
            text = text.replace("$window_class matches", "Window class matches", 1)
        elif text.startswith("$"):
            text = text[1:].replace("_", " ", 1).capitalize()
        return text
    if isinstance(c, dict):
        return "; ".join(f"{k}: " + ", ".join(conditions(v)) for k, v in c.items())
    return str(c)


def conditions(c) -> list[str]:
    if c is None:
        return []
    return [condition(x) for x in c] if isinstance(c, list) else [condition(c)]


def plain(node):
    """Strip ruamel comment metadata so dumps don't drag stray comments along."""
    if isinstance(node, dict):
        return {str(k): plain(v) for k, v in node.items()}
    if isinstance(node, list):
        return [plain(v) for v in node]
    return node


def to_yaml(node) -> str:
    y = YAML()
    y.default_flow_style = False
    buf = io.StringIO()
    y.dump(plain(node), buf)
    return buf.getvalue()
