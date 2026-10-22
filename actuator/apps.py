"""Installed apps, from .desktop entries: names, icons, and the window class to match."""

import os
import re
import shlex
import shutil
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class App:
    name: str
    window_class: str  # what InputActions sees as $window_class
    icon: str
    steam_id: str | None = None  # a Steam game's app id (its launcher runs steam://rungameid/<id>)


def _dirs() -> list[Path]:
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    data_dirs = os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":")
    dirs = [data_home / "applications", data_home / "flatpak/exports/share/applications",
            Path("/var/lib/flatpak/exports/share/applications")]
    dirs += [Path(d) / "applications" for d in data_dirs]
    return dirs


def _parse(path: Path) -> dict[str, str]:
    """The [Desktop Entry] group's untranslated keys."""
    entry, in_group = {}, False
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["):
            in_group = line == "[Desktop Entry]"
        elif in_group and "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            if "[" not in key:
                entry.setdefault(key.strip(), value.strip())
    return entry


def _runnable(entry: dict[str, str]) -> bool:
    """False for launchers left behind by uninstalled software (their program is gone)."""
    try_exec = entry.get("TryExec", "")
    if try_exec and not (Path(try_exec).exists() if "/" in try_exec else shutil.which(try_exec)):
        return False
    exec_line = entry.get("Exec", "").strip()
    try:
        program = shlex.split(exec_line)[0] if exec_line else ""
    except ValueError:  # malformed quoting: don't hide the app over it
        return True
    if program.startswith("/") and not Path(program).exists():
        return False
    return True


def window_class(desktop_id: str, startup_wm_class: str) -> str:
    """What InputActions will see as $window_class. Native Wayland apps report their app ID,
    which for reverse-DNS launchers (org.kde.dolphin, Flatpaks) is the launcher's name, even
    when StartupWMClass says otherwise (that's for X11). Otherwise StartupWMClass is the better
    guess (games and other X11 apps), then the launcher's name."""
    if "." in desktop_id:
        return desktop_id
    return startup_wm_class or desktop_id


@lru_cache(maxsize=1)
def installed() -> tuple[App, ...]:
    seen: dict[str, App] = {}
    for d in _dirs():
        if not d.is_dir():
            continue
        for path in sorted(d.glob("*.desktop")):
            desktop_id = path.stem
            if desktop_id in seen:
                continue  # earlier directories take precedence, as in the XDG spec
            e = _parse(path)
            if (e.get("Type") != "Application" or e.get("NoDisplay") == "true"
                    or e.get("Hidden") == "true" or not e.get("Name") or not _runnable(e)):
                continue
            steam = re.search(r"steam://rungameid/(\d+)", e.get("Exec", ""))
            seen[desktop_id] = App(e["Name"], window_class(desktop_id, e.get("StartupWMClass", "")), e.get("Icon", ""),
                                   steam.group(1) if steam else None)
    # Some apps ship several launchers for the same window (an installer's entry plus a manual one).
    # Bindings match the window class, so those are one app: keep the first of each.
    unique: dict[str, App] = {}
    for app in seen.values():
        unique.setdefault(app.window_class.lower(), app)
    return tuple(sorted(unique.values(), key=lambda a: a.name.lower()))


def by_class(window_class: str) -> App | None:
    wc = window_class.lower()
    return next((a for a in installed() if a.window_class.lower() == wc), None)


def steam_id(app: str) -> str | None:
    """The Steam app id of an app that's an installed Steam game, by its window class or name."""
    key = app.lower()
    found = next((a for a in installed() if a.steam_id and (a.window_class.lower() == key or a.name.lower() == key)),
                 None)
    return found.steam_id if found else None


def display_name(window_class: str) -> str:
    app = by_class(window_class)
    if app:
        return app.name
    return window_class.capitalize() if window_class.islower() else window_class


def icon_name(window_class: str) -> str:
    app = by_class(window_class)
    return app.icon if app and app.icon else window_class
