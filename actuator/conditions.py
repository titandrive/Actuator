"""Condition rows for the "Only if" builder, and converting them to and from InputActions syntax."""

import re

WINDOW_CLASS = re.compile(r"^\$window_class\s*(==|matches)\s*(.+)$")
MODIFIERS = ["ctrl", "shift", "alt", "meta"]
EDGE = 0.02  # pointer within 2% of a screen edge counts as "at" it

# Row type → (operations, value kind). Value kinds: None, "text", or a list of choices.
ROW_TYPES = {
    "Window": (["is fullscreen", "is not fullscreen", "is maximized", "is not maximized"], None),
    "Window title": (["contains", "is", "matches"], "text"),
    "Keys held": (["include", "don't include", "are none"], MODIFIERS),
    "Pointer": (["is at top edge", "is at bottom edge", "is at left edge", "is at right edge"], None),
    "Screen": (["is", "is not"], "text"),
    "Overview": (["is open", "is closed"], None),
    "Custom": ([""], "text"),
}
MODES = ["all", "any", "none"]

_BOOLS = {
    ("Window", "is fullscreen"): "$window_fullscreen",
    ("Window", "is not fullscreen"): "!$window_fullscreen",
    ("Window", "is maximized"): "$window_maximized",
    ("Window", "is not maximized"): "!$window_maximized",
    ("Overview", "is open"): "$plasma_overview_active",
    ("Overview", "is closed"): "!$plasma_overview_active",
    ("Keys held", "are none"): "$keyboard_modifiers == []",
}
_EDGES = {
    "is at top edge": f"$pointer_position_screen_percentage_y <= {EDGE}",
    "is at bottom edge": f"$pointer_position_screen_percentage_y >= {1 - EDGE}",
    "is at left edge": f"$pointer_position_screen_percentage_x <= {EDGE}",
    "is at right edge": f"$pointer_position_screen_percentage_x >= {1 - EDGE}",
}
_TITLE_OPS = {"contains": "contains", "is": "==", "matches": "matches"}


def to_text(row: tuple[str, str, str]) -> str:
    kind, op, value = row
    value = value.strip()
    if (kind, op) in _BOOLS:
        return _BOOLS[(kind, op)]
    if kind == "Pointer":
        return _EDGES[op]
    if kind == "Window title":
        return f"$window_title {_TITLE_OPS[op]} {value}"
    if kind == "Keys held":
        return f"{'!' if op == "don't include" else ''}$keyboard_modifiers contains {value}"
    if kind == "Screen":
        return f"$screen_name {'==' if op == 'is' else '!='} {value}"
    return value  # Custom: written as typed


def from_text(text: str) -> tuple[str, str, str]:
    t = " ".join(str(text).split())
    for (kind, op), s in _BOOLS.items():
        if t == s:
            return kind, op, ""
    for op, s in _EDGES.items():
        if t == s:
            return "Pointer", op, ""
    if m := re.fullmatch(r"\$window_title (contains|==|matches) (.+)", t):
        op = {v: k for k, v in _TITLE_OPS.items()}[m.group(1)]
        return "Window title", op, m.group(2)
    if m := re.fullmatch(r"(!?)\$keyboard_modifiers contains (\w+)", t):
        if m.group(2) in MODIFIERS:
            return "Keys held", "don't include" if m.group(1) else "include", m.group(2)
    if m := re.fullmatch(r"\$screen_name (==|!=) (.+)", t):
        return "Screen", "is" if m.group(1) == "==" else "is not", m.group(2)
    return "Custom", "", str(text)


def describe_row(row) -> str:
    kind, op, value = row
    if kind == "Custom":
        return value
    if kind in ("Window title", "Screen", "Keys held") and op != "are none":
        return f"{kind.lower()} {op} {value}"
    return f"{kind.lower()} {op}"


def split(conditions) -> tuple[str | None, str, list] | None:
    """(app condition kept as written, mode, rows) — or None if the form can't show these."""
    if conditions is None:
        return None, "all", []
    items = list(conditions) if isinstance(conditions, list) else [conditions]
    app = [x for x in items if isinstance(x, str) and WINDOW_CLASS.match(x.strip())]
    if len(app) > 1:
        return None
    rest = [x for x in items if not (app and x is app[0])]
    if all(isinstance(x, str) for x in rest):
        return (app[0] if app else None), "all", [from_text(x) for x in rest]
    if len(rest) == 1 and isinstance(rest[0], dict) and len(rest[0]) == 1:
        (mode, group), = rest[0].items()
        if mode in ("any", "none", "all") and isinstance(group, list) and all(isinstance(x, str) for x in group):
            return (app[0] if app else None), mode, [from_text(x) for x in group]
    return None


def join(app: str | None, mode: str, rows: list):
    """Back to a conditions value, or None for no conditions at all."""
    texts = [to_text(r) for r in rows if to_text(r)]
    items = [app] if app else []
    if texts:
        items += texts if mode == "all" else [{mode: texts}]
    return items or None


def extra_count(conditions) -> int:
    """How many conditions beyond the app one; -1 if they're too complex for the form."""
    parts = split(conditions)
    return -1 if parts is None else len(parts[2])
