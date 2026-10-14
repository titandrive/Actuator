"""What would change between two configs, in words, for the restore preview."""

from . import apps
from . import config as cfg
from . import describe as d
from .copying import signature


def _label(c: cfg.Config, b: cfg.Binding) -> str:
    if b.button:
        name = c.button_names.get(b.button)
        return name or d.button_label(b.button)
    return d.gesture_title(b.data)


def _where(b: cfg.Binding) -> str:
    if b.scope == "app":
        return apps.display_name(b.app)
    return "Global" if b.scope == "global" else "Complex rules"


def _keyed(c: cfg.Config) -> dict[tuple, cfg.Binding]:
    """Bindings by where they apply and what triggers them."""
    out = {}
    for b in c.bindings:
        scope = (b.scope, (b.app or "").lower()) if b.scope != "custom" else ("custom", b.id)
        out.setdefault(scope + signature(b.section, b.data), b)
    return out


def _flatten(node, prefix="") -> dict[str, object]:
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            out.update(_flatten(v, f"{prefix}{k}."))
        return out
    return {prefix.rstrip("."): node}


def _device_settings(c: cfg.Config) -> dict[str, object]:
    out = {}
    for rule in c.data.get("device_rules") or []:
        if not isinstance(rule, dict):
            continue
        who = d.conditions(rule.get("conditions")) or ["all devices"]
        who = " and ".join(x.lstrip("$") for x in who)
        for key, value in _flatten({k: v for k, v in rule.items() if k != "conditions"}).items():
            out[f"{who}: {key.replace('_', ' ').replace('.', ' ')}"] = value
    return out


def _details(a: dict, b: dict) -> tuple[str, str]:
    """What differs between two versions of a binding besides its action, as (before, after)."""
    ta, tb = d.trigger_parts(a), d.trigger_parts(b)
    ca, cb = d.conditions(a.get("conditions")), d.conditions(b.get("conditions"))
    before, after = [], []
    if ta != tb:
        before.append(" · ".join(x for x in ta if x not in tb) or "trigger")
        after.append(" · ".join(x for x in tb if x not in ta) or "trigger")
    if ca != cb:
        before.append("only if: " + (", ".join(x for x in ca if x not in cb) or "—"))
        after.append("only if: " + (", ".join(x for x in cb if x not in ca) or "—"))
    if not before:
        return "other settings", "changed"
    return "; ".join(before), "; ".join(after)


def changes(current: cfg.Config, other: cfg.Config) -> dict[str, list[tuple[str, str, str, str]]]:
    """{group: [(mark, what, before, after)]} for everything restoring `other` would change.
    mark is + (added), − (removed) or ~ (changed). Empty if nothing changes."""
    groups: dict[str, list[tuple[str, str, str, str]]] = {}
    added: dict[str, list[cfg.Binding]] = {}
    removed: dict[str, list[cfg.Binding]] = {}
    now, then = _keyed(current), _keyed(other)
    for key in sorted(set(now) | set(then), key=lambda k: (k[0] != "global", str(k[1]).lower())):
        a, b = now.get(key), then.get(key)
        if a and b:
            if d.plain(a.data) == d.plain(b.data):
                continue
            before, after = d.summary(a), d.summary(b)
            if before == after:
                before, after = _details(d.plain(a.data), d.plain(b.data))
            groups.setdefault(_where(b), []).append(("~", _label(other, b), before, after))
        elif b:
            added.setdefault(_where(b), []).append(b)
        else:
            removed.setdefault(_where(a), []).append(a)
    # A removed and an added binding of the same kind doing the same thing is really one edit,
    # e.g. a swipe changed from 4 to 3 fingers. Show it as a change.
    for where in sorted(set(added) | set(removed)):
        adds, rems = added.get(where, []), removed.get(where, [])
        for r in list(rems):
            match = next((x for x in adds if x.section == r.section and x.type == r.type
                          and x.button == r.button and d.summary(x) == d.summary(r)), None)
            if match:
                adds.remove(match)
                rems.remove(r)
                before, after = _details(d.plain(r.data), d.plain(match.data))
                groups.setdefault(where, []).append(("~", _label(other, match), before, after))
        for b in adds:
            groups.setdefault(where, []).append(("+", _label(other, b), "not set", d.summary(b)))
        for a in rems:
            groups.setdefault(where, []).append(("−", _label(current, a), d.summary(a), "removed"))
    ds_now, ds_then = _device_settings(current), _device_settings(other)
    for key in sorted(set(ds_now) | set(ds_then)):
        a, b = ds_now.get(key), ds_then.get(key)
        if a == b:
            continue
        mark = "+" if a is None else "−" if b is None else "~"
        groups.setdefault("Device settings", []).append(
            (mark, key, "default" if a is None else str(a), "default" if b is None else str(b)))

    def named(c):  # only names someone chose; the rest are just the raw input, written for bookkeeping
        return {k: v for k, v in c.button_names.items() if v != d.button_label(k)}
    for f in sorted(set(current.folder_icons) | set(other.folder_icons)):
        x, y = current.folder_icons.get(f), other.folder_icons.get(f)
        if x != y and f in other.folders:
            groups.setdefault("Other", []).append(("~", f"Icon of {f}", x or "folder", y or "folder"))
    if current.folders != other.folders:
        groups.setdefault("Other", []).append(
            ("~", "Folders", ", ".join(current.folders) or "none", ", ".join(other.folders) or "none"))
    for src in sorted(set(named(current)) | set(named(other))):
        x, y = named(current).get(src), named(other).get(src)
        if x != y:
            groups.setdefault("Other", []).append(
                ("~", f"Name of {d.button_label(src)}", x or d.button_label(src), y or d.button_label(src)))
    return groups
