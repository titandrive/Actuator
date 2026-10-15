"""Copying bindings between apps."""

import copy

from . import conditions as conds
from . import config as cfg
from . import describe as d
from .editor import slug, unique_id


def retarget(data: dict, scope: str, app: str | None) -> dict | None:
    """A copy of a binding pointed at another app (or Global). None if its conditions
    are too complex to move."""
    parts = conds.split(data.get("conditions"))
    if parts is None:
        return None
    _old_app, mode, rows = parts
    new = copy.deepcopy(d.plain(data))
    value = conds.join(conds.app_condition(app) if scope == "app" else None, mode, rows)
    if value is None:
        new.pop("conditions", None)
    else:
        new["conditions"] = value
    return new


def signature(section: str, data: dict) -> tuple:
    """What a binding responds to, ignoring where and what it does. Equal signatures clash."""
    keys = ("type", "mouse_buttons", "shortcut", "fingers", "direction", "speed")
    return (section,) + tuple(str(data.get(k)) for k in keys)


def clashes(config: cfg.Config, items: list[tuple[str, dict]], scope: str, app: str | None):
    existing = {signature(b.section, b.data): b for b in config.bindings_for(scope, app)}
    return [existing[signature(s, data)] for s, data in items if signature(s, data) in existing]


def paste(config: cfg.Config, items: list[tuple[str, dict]], scope: str, app: str | None,
          replace: bool) -> tuple[int, int]:
    """Add copies of (section, data) bindings to the target. Returns (added, skipped)."""
    where = app or "global"
    existing = {signature(b.section, b.data): b for b in config.bindings_for(scope, app)}
    to_remove, added, skipped = [], 0, 0
    new_items = []
    for section, data in items:
        new = retarget(data, scope, app)
        if new is None:
            skipped += 1
            continue
        clash = existing.get(signature(section, new))
        if clash is not None:
            if not replace:
                skipped += 1
                continue
            to_remove.append(clash)
        new_items.append((section, new))
        added += 1
    if to_remove:
        config.remove_bindings(to_remove)
    prefixes = sorted({f"{slug(b.app)}_" for b in config.bindings if b.app} | {"global_"}, key=len, reverse=True)
    for section, new in new_items:
        base = str(new.get("id", "binding"))
        base = next((base[len(p):] for p in prefixes if base.startswith(p)), base)
        old_id = str(new.get("id", ""))
        new["id"] = unique_id(config, f"{slug(where)}_{base}")  # added one at a time, so ids stay unique
        if config.labels.get(old_id):
            config.labels[new["id"]] = config.labels[old_id]  # a copied binding keeps its label
        config.add_binding(section, new)
    return added, skipped
