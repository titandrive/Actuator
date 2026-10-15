"""Rendering the config in Actuator's layout, and saving it safely."""

import io
import json
import os
import shutil
import re
from datetime import datetime
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedSeq
from ruamel.yaml.scalarstring import SingleQuotedScalarString

from . import apps
from . import config as cfg
from . import describe as d

HEADER = cfg.MANAGED_HEADER
SNAPSHOT_DIR = Path.home() / ".local" / "share" / "actuator" / "snapshots"
SNAPSHOT_KEEP = 10  # set from the user's options

# Order sections are written in. Unknown top-level keys are kept, after these.
SECTION_ORDER = ["mouse", "keyboard", "touchpad", "touchscreen", "pointer"]

# Only lists of plain tokens like keys and button names go inline.
INLINE_TOKEN = re.compile(r"^[A-Za-z0-9_+\-]+$")


def _styled(node):
    """Plain data → nodes that dump readably: short scalar lists inline, 'on' quoted."""
    if isinstance(node, dict):
        return {
            (SingleQuotedScalarString(k) if k in ("on", "off", "yes", "no") else k): _styled(v)
            for k, v in node.items()
        }
    if isinstance(node, list):
        if node and all(isinstance(v, (str, int)) and INLINE_TOKEN.match(str(v)) for v in node):
            seq = CommentedSeq(node)
            seq.fa.set_flow_style()
            return seq
        return [_styled(v) for v in node]
    return node


def _dump(node, indent: str = "") -> list[str]:
    y = YAML()
    y.indent(mapping=2, sequence=2, offset=0)
    y.width = 4096
    buf = io.StringIO()
    y.dump(_styled(node), buf)
    return [indent + line if line else line for line in buf.getvalue().rstrip("\n").split("\n")]


def _scope_order(config: cfg.Config, b: cfg.Binding) -> tuple:
    rank = {"global": 0, "app": 1, "custom": 2}[b.scope]
    return rank, b.app or ""


def _scope_header(b: cfg.Binding) -> str:
    if b.scope == "app":
        return apps.display_name(b.app)
    return "Global" if b.scope == "global" else "Custom rules"


def render(config: cfg.Config) -> str:
    out = [HEADER, "# Actuator rewrites this file in this layout every time it saves.", ""]

    # Every known button is recorded, so detection never depends on guessing again.
    names = {b.src: b.name if b.name != b.src else d.button_label(b.src) for b in config.buttons}
    if names or config.extra_apps or config.folders or config.device_types or config.folder_icons:
        out.append("# Button names, apps, folders and devices used by Actuator. Safe to edit.")
        out += [f"# actuator:button {src} = {name}" for src, name in sorted(names.items())]
        out += [f"# actuator:app {app}" for app in sorted(config.extra_apps)]
        out += [f"# actuator:folder {f} = {', '.join(apps)}" for f, apps in config.folders.items()]
        out += [f"# actuator:foldericon {f} = {i}" for f, i in config.folder_icons.items() if f in config.folders]
        out += [f"# actuator:device {n} = {t}" for n, t in config.device_types.items()]
        out.append("")
    if config.prefs:
        out.append("# Actuator's options (Options → Store Preferences in Config). Safe to edit.")
        out += [f"# actuator:pref {k} = {v}" for k, v in config.prefs.items()]
        out.append("")

    data = d.plain(config.data)
    sections = [s for s in SECTION_ORDER if s in data]
    for section in sections:
        body = data[section] or {}
        title = section.upper()
        if section == "keyboard":
            title += " (includes mouse buttons that send shortcuts)"
        out += [f"# {'=' * 76}", f"# {title}", f"# {'=' * 76}", f"{section}:"]
        for key, value in body.items():
            if key != "gestures":
                out += _dump({key: value}, "  ")
        bindings = sorted(
            (b for b in config.bindings if b.section == section),
            key=lambda b: (_scope_order(config, b), b.index),
        )
        has_any = bindings or any(not isinstance(g, dict) for g in (body.get("gestures") or []))
        out.append("  gestures:" if has_any else "  gestures: []")
        last_header = None
        for b in bindings:
            header = _scope_header(b)
            if header != last_header:
                out += ["", f"  # {header}", f"  # {'-' * 74}"]
                last_header = header
            else:
                out.append("")
            if b.button:
                out.append(f"  # {names.get(b.button) or d.button_label(b.button)}")
            if config.labels.get(b.id):
                out.append(f"  # Note: {config.labels[b.id]}")
            out += _dump([d.plain(b.data)], "  ")
        unknown = [g for g in (body.get("gestures") or []) if not isinstance(g, dict)]
        if unknown:
            out += ["", "  # Not understood by Actuator, kept as is", f"  # {'-' * 74}"]
            out += _dump(unknown, "  ")
        out.append("")

    rest = {k: v for k, v in data.items() if k not in sections}
    if "device_rules" in rest:
        out += [f"# {'=' * 76}", "# DEVICE SETTINGS", f"# {'=' * 76}"]
        out += _dump({"device_rules": rest.pop("device_rules")})
        out.append("")
    for key, value in rest.items():
        out += _dump({key: value})
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


def _normalized(data) -> dict:
    """Data in an order-independent form: gestures sorted, empty sections made explicit,
    so two configs compare equal exactly when they hold the same settings."""
    data = d.plain(YAML(typ="safe").load(_roundtrip_plain(d.plain(data))) or {})
    for section in SECTION_ORDER:
        if section in data:
            body = data[section] if isinstance(data[section], dict) else {}
            gestures = body.get("gestures") or []
            body["gestures"] = sorted(gestures, key=lambda g: json.dumps(g, sort_keys=True, default=str))
            data[section] = body
    return data


def verify(config: cfg.Config, text: str) -> None:
    """The rendered file must hold exactly the settings it was made from — nothing dropped,
    nothing changed — compared against the original data, not Actuator's view of it."""
    if _normalized(YAML(typ="safe").load(text)) != _normalized(config.data):
        raise ValueError("Rendered config doesn't match the data it was made from. Nothing was written.")


def _roundtrip_plain(data) -> str:
    buf = io.StringIO()
    YAML(typ="safe").dump(data, buf)
    return buf.getvalue()


def is_managed(path: Path) -> bool:
    try:
        return path.read_text().startswith(HEADER)
    except OSError:
        return False


def original_path(path: Path) -> Path:
    return path.with_name(path.name + ".original")


def snapshot(path: Path) -> Path | None:
    if not path.exists():
        return None
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
    target = SNAPSHOT_DIR / f"config-{stamp}.yaml"
    n = 2
    while target.exists():  # two in one second must not overwrite each other
        target = SNAPSHOT_DIR / f"config-{stamp}-{n}.yaml"
        n += 1
    shutil.copy2(path, target)
    prune()
    return target


def prune() -> None:
    """Delete the oldest versions beyond the limit. Locked ones are kept and don't count."""
    unlocked = [x for x in snapshots() if not is_locked(x)]
    for old in unlocked[SNAPSHOT_KEEP:]:
        old.unlink()


# Locked versions, listed in a small file next to the snapshots. The pre-Actuator original
# lives with the config, and is locked unless someone unlocks it.
def _locks_file() -> Path:
    return SNAPSHOT_DIR / "locked.json"


def _locks() -> dict:
    try:
        data = json.loads(_locks_file().read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _lock_key(path: Path) -> str:
    return "original" if path.name.endswith(".original") else path.name


def is_locked(path: Path) -> bool:
    key, locks = _lock_key(path), _locks()
    if key == "original":
        return locks.get("original", True)
    return key in locks.get("snapshots", [])


def set_locked(path: Path, on: bool) -> None:
    key, locks = _lock_key(path), _locks()
    if key == "original":
        locks["original"] = on
    else:
        names = set(locks.get("snapshots", []))
        (names.add if on else names.discard)(key)
        locks["snapshots"] = sorted(names)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    _locks_file().write_text(json.dumps(locks, indent=2) + "\n")


def snapshots() -> list[Path]:
    """Newest first."""
    if not SNAPSHOT_DIR.exists():
        return []
    return sorted(SNAPSHOT_DIR.glob("config-*.yaml"), reverse=True)


def write_atomic(path: Path, text: str) -> None:
    """Replace in one step, so InputActions never sees a missing or half-written file."""
    tmp = path.with_name(f".{path.name}.actuator-tmp")
    tmp.write_text(text)
    if path.exists():
        shutil.copymode(path, tmp)
    os.replace(tmp, path)


def save(config: cfg.Config, take_snapshot: bool = True) -> Path | None:
    """Render, verify, keep the original on first save, snapshot, then write.

    Returns the snapshot of the previous file, for undo.
    """
    text = render(config)
    verify(config, text)
    path = config.path
    orig = original_path(path)
    if path.exists() and not is_managed(path) and not orig.exists():
        shutil.copy2(path, orig)
    previous = snapshot(path) if take_snapshot else None
    write_atomic(path, text)
    return previous


def restore(path: Path, source: Path) -> Path | None:
    """Put a backup or export in place. The current file is snapshotted first."""
    text = Path(source).read_text()  # read before snapshotting, in case the source is a snapshot
    previous = snapshot(path)
    write_atomic(path, text)
    return previous
