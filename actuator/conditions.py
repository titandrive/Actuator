"""Condition rows for the "Only if" builder, and converting them to and from InputActions syntax."""

import re

WINDOW_CLASS = re.compile(r"^\$window_class\s*(==|matches)\s*(.+)$")
WINDOW_TITLE_IS = re.compile(r"^\$window_title\s*==\s*(.+)$")
# Apps that run games inside their own window, so every game reports the wrapper's class. A game in
# one is told apart by its window title instead: "$window_class == gamescope" and "$window_title == X".
WRAPPERS = {"gamescope"}
MODIFIERS = ["ctrl", "shift", "alt", "meta"]


def game_condition(app: str, title: str, steam_id: str | None = None) -> dict:
    """A game's "in this app" condition: matched by its own window class, by Steam's window for it
    (steam_app_<id>), or inside gamescope (whose window every game shares) by its window title.
    So its bindings work however it's launched."""
    ways: list = [app_condition(app)]  # first: says which app this is
    if steam_id:
        ways.append(f"$window_class == steam_app_{steam_id}")
    ways.append({"all": ["$window_class == gamescope", f"$window_title matches (?i)^{re.escape(title)}"]})
    return {"any": ways}


def app_conditions(app: str, game: tuple[str, str | None] | None = None) -> list:
    """The conditions for "in this app". game: (window title, Steam id) for a game."""
    if game:
        return [game_condition(app, *game)]
    return [app_condition(app)]


def game_app(item) -> tuple[str, str | None] | None:
    """(app, Steam id) if this condition is a game's any-of-these, else None."""
    if not (isinstance(item, dict) and list(item) == ["any"] and isinstance(item["any"], list)):
        return None
    ways = item["any"]
    if not ways or not isinstance(ways[0], str) or not (m := WINDOW_CLASS.match(ways[0].strip())):
        return None
    in_gamescope = any(isinstance(w, dict) and isinstance(w.get("all"), list)
                       and "$window_class == gamescope" in [str(x).strip() for x in w["all"]] for w in ways)
    if not in_gamescope:
        return None
    steam = next((g.group(1) for w in ways if isinstance(w, str)
                  and (g := re.match(r"^\$window_class\s*==\s*steam_app_(\d+)$", w.strip()))), None)
    return m.group(2), steam


def wrapped_app(items) -> tuple[list[str], str, str] | None:
    """(the two conditions, wrapper, title) for the older "gamescope and this title" form."""
    strings = [x for x in items if isinstance(x, str)]
    classes = [x for x in strings if WINDOW_CLASS.match(x.strip())]
    titles = [x for x in strings if WINDOW_TITLE_IS.match(x.strip())]
    if len(classes) != 1 or len(titles) != 1:
        return None
    m = WINDOW_CLASS.match(classes[0].strip())
    wrapper = m.group(2).strip().strip("'\"")
    if m.group(1) != "==" or wrapper not in WRAPPERS:
        return None
    title = WINDOW_TITLE_IS.match(titles[0].strip()).group(1).strip().strip("'\"")
    return [classes[0], titles[0]], wrapper, title


def app_condition(app: str) -> str:
    """The condition for "in this app". Lowercase classes compare exactly; others ignore case,
    since launchers often capitalize a class the window itself reports in lowercase."""
    if app == app.lower():
        return f"$window_class == {app}"
    return f"$window_class matches (?i)^{re.escape(app)}$"
EDGE = 0.02  # pointer within 2% of a screen edge counts as "at" it

# Row type → (operations, value kind). Value kinds: None, "text", or a list of choices.
ROW_TYPES = {
    "Window": (["is fullscreen", "is not fullscreen", "is maximized", "is not maximized"], None),
    "Window title": (["contains", "is", "matches"], "text"),
    "Modifier key": (["is held", "is not held", "none held"], MODIFIERS),
    "Pointer": (["is at top edge", "is at bottom edge", "is at left edge", "is at right edge"], None),
    "Screen": (["is", "is not"], "text"),
    "Overview": (["is open", "is closed"], None),
    "Custom": ([""], "text"),
}
MODES = ["all", "any", "none"]
NO_MODIFIERS = "none held"  # the Modifier key operation that takes no key

_BOOLS = {
    ("Window", "is fullscreen"): "$window_fullscreen",
    ("Window", "is not fullscreen"): "!$window_fullscreen",
    ("Window", "is maximized"): "$window_maximized",
    ("Window", "is not maximized"): "!$window_maximized",
    ("Overview", "is open"): "$plasma_overview_active",
    ("Overview", "is closed"): "!$plasma_overview_active",
    ("Modifier key", NO_MODIFIERS): "$keyboard_modifiers == []",
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
    if kind == "Modifier key":
        return f"{'!' if op == 'is not held' else ''}$keyboard_modifiers contains {value}"
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
            return "Modifier key", "is not held" if m.group(1) else "is held", m.group(2)
    if m := re.fullmatch(r"\$screen_name (==|!=) (.+)", t):
        return "Screen", "is" if m.group(1) == "==" else "is not", m.group(2)
    return "Custom", "", str(text)


def describe_row(row) -> str:
    kind, op, value = row
    if kind == "Custom":
        return value
    if kind == "Modifier key":
        return "no modifier key is held" if op == NO_MODIFIERS else f"{value.capitalize()} {op}"
    if kind in ("Window title", "Screen"):
        return f"{kind.lower()} {op} {value}"
    return f"{kind.lower()} {op}"


def split(conditions) -> tuple[str | None, str, list] | None:
    """(app condition kept as written, mode, rows) — or None if the form can't show these."""
    if conditions is None:
        return None, "all", []
    items = list(conditions) if isinstance(conditions, list) else [conditions]
    games = [x for x in items if game_app(x)]
    if len(games) == 1:  # a game: its any-of-these is the app
        rest = [x for x in items if x is not games[0]]
        app = [[games[0]]]
    elif wrapped := wrapped_app(items):  # the older form: gamescope and its title
        pair = wrapped[0]
        rest = [x for x in items if not any(x is y for y in pair)]
        app = [pair]
    else:
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


def join(app, mode: str, rows: list):
    """Back to a conditions value, or None for no conditions at all. The app is one condition,
    or a list of them (a game in gamescope)."""
    texts = [to_text(r) for r in rows if to_text(r)]
    items = list(app) if isinstance(app, list) else [app] if app else []
    if texts:
        items += texts if mode == "all" else [{mode: texts}]
    return items or None


def extra_count(conditions) -> int:
    """How many conditions beyond the app one; -1 if they're too complex for the form."""
    parts = split(conditions)
    return -1 if parts is None else len(parts[2])
