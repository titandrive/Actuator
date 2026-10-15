"""Committing (and optionally pushing) config.yaml to the git repo it lives in, if any."""

import os
import subprocess
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from . import config as cfg
from . import diffing


def _git(folder: Path, *args: str) -> tuple[bool, str]:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")  # never hang waiting for a password
    try:
        p = subprocess.run(["git", "-C", str(folder), *args], capture_output=True, text=True,
                           timeout=15, env=env)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    return p.returncode == 0, (p.stdout + p.stderr).strip()


def repo_root(path: Path) -> Path | None:
    ok, out = _git(path.parent, "rev-parse", "--show-toplevel")
    return Path(out) if ok and out else None


def status(path: Path) -> dict:
    """What's set up, for the indicator in Options."""
    import shutil

    if not shutil.which("git"):
        return {"git": False, "repo": None, "remote": None}
    root = repo_root(path)
    remote = None
    if root:
        ok, out = _git(root, "remote", "-v")
        if ok and out:
            name, url = out.splitlines()[0].split()[:2]
            remote = f"{name} ({url})"
    return {"git": True, "repo": root, "remote": remote}


def web_url(folder: Path) -> str | None:
    """The remote as a browser link: git@github.com:me/repo.git → https://github.com/me/repo."""
    ok, url = _git(folder, "remote", "get-url", "origin")
    if not ok or not url:
        return None
    url = url.strip().removesuffix(".git")
    if url.startswith("git@"):
        host, _, path = url[4:].partition(":")
        url = f"https://{host}/{path}"
    elif url.startswith("ssh://"):
        url = "https://" + url[6:].split("@", 1)[-1]
    return url if url.startswith("http") else None


def commit_url(folder: Path) -> str | None:
    """The latest commit on the remote's website (GitHub and GitLab both accept /commit/<sha>)."""
    base = web_url(folder)
    ok, sha = _git(folder, "rev-parse", "HEAD")
    return f"{base}/commit/{sha.strip()}" if base and ok and sha else base


def message(old_text: str | None, new_text: str) -> str:
    """A commit message describing the change, e.g. "Actuator: Firefox: Meta+W Ctrl+W → Ctrl+T"."""
    try:
        old = cfg.loads(old_text) if old_text else None
        groups = diffing.changes(old, cfg.loads(new_text)) if old else None
    except Exception:  # noqa: BLE001 - a message is never worth failing a commit over
        groups = None
    if not groups:
        return "Actuator: update config"
    lines = [f"{group}: {mark} {what}: {before} → {after}"
             for group, items in groups.items() for mark, what, before, after in items]
    subject = lines[0] if len(lines) == 1 else f"{lines[0]} (+{len(lines) - 1} more)"
    return f"Actuator: {subject}\n\n" + "\n".join(lines)


def commit(path: Path, msg: str, extra: list[Path] = ()) -> tuple[bool, str]:
    """Commit just this file (and any extra ones, like a generated README). Other changes in the
    repo are left alone."""
    folder = path.parent
    names = [path.name] + [str(x.relative_to(folder)) for x in extra]
    ok, out = _git(folder, "add", "--", *names)
    if not ok:
        return False, out
    ok, out = _git(folder, "diff", "--cached", "--quiet", "--", *names)
    if ok:
        return True, "nothing to commit"  # no difference from the last commit
    return _git(folder, "commit", "-m", msg, "--", *names)


INPUTACTIONS_URL = "https://github.com/InputActions"


def _cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def readme(config: cfg.Config) -> str:
    """A readable summary of every binding, for the repository's front page."""
    from datetime import date

    from . import apps
    from . import conditions as conds
    from . import describe as d

    def label(b):
        if b.button:
            return config.button_names.get(b.button) or d.button_label(b.button)
        return d.gesture_title(b.data)

    def extra(b):
        parts = conds.split(b.data.get("conditions"))
        if parts is None:
            return "custom conditions"
        _app, mode, rows = parts
        texts = [conds.describe_row(r) for r in rows]
        if not texts:
            return ""
        return (" and " if mode == "all" else " or " if mode == "any" else ", none of: ").join(texts)

    def action(b) -> str:
        from .editor import detect_mode

        mode, values = detect_mode(b.data.get("actions"))
        if mode == "Hold keys":
            return f"Hold {values['keys']}"
        stages = d.stages(b.data.get("actions"))
        if not stages:
            return "Nothing"
        if len(stages) == 1:
            return ", then ".join(stages[0][1])
        return "; ".join(f"{stage.lower()}: {', '.join(steps)}" for stage, steps in stages)

    def table(bindings) -> list[str]:
        out = []
        for section_title, section in cfg.DEVICE_TABS.items():
            rows = [b for b in bindings if (b.section == section and not b.button) or
                    (section == "mouse" and b.button)]
            rows = [b for b in rows if not (section == "keyboard" and b.button)]
            if not rows:
                continue
            show_if = any(extra(b) for b in rows)
            show_label = any(config.labels.get(b.id) for b in rows)
            out += [f"**{section_title}**", ""]
            out.append("| Trigger | Action |" + (" Only if |" if show_if else "") + (" Note |" if show_label else ""))
            out.append("|---|---|" + ("---|" if show_if else "") + ("---|" if show_label else ""))
            for b in rows:
                out.append(f"| {_cell(label(b))} | {_cell(action(b))} |"
                           + (f" {_cell(extra(b))} |" if show_if else "")
                           + (f" {_cell(config.labels.get(b.id, ''))} |" if show_label else ""))
            out.append("")
        return out

    from . import REPO_URL

    lines = [f"> This README was automatically generated by [Actuator]({REPO_URL}), "
             f"a GUI for [InputActions]({INPUTACTIONS_URL}).", "",
             "# InputActions configuration", "", f"Last updated {date.today():%B %d, %Y}.", ""]
    globals_ = config.bindings_for("global") + config.bindings_for("custom")
    if globals_:
        lines += ["## Global", ""] + table(globals_)
    ordered = [a for f in config.folders.values() for a in f if a in config.apps]
    ordered += [a for a in config.apps if a not in ordered]
    for app in ordered:
        bindings = config.bindings_for("app", app)
        if bindings:
            folder = config.folder_of(app)
            lines += [f"## {apps.display_name(app)}" + (f" · {folder}" if folder else ""), ""] + table(bindings)
    rules = config.data.get("device_rules") or []
    if rules:
        lines += ["## Device settings", ""]
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            who = " and ".join(c.lstrip("$") for c in d.conditions(rule.get("conditions"))) or "all devices"
            flat = []
            def walk(node, prefix=""):
                for k, v in node.items():
                    if k == "conditions":
                        continue
                    if isinstance(v, dict):
                        walk(v, f"{prefix}{k} ")
                    else:
                        flat.append(f"{prefix}{k}".replace("_", " ") + f": {v}")
            walk(rule)
            lines.append(f"- **{who}**: " + ", ".join(flat))
        lines.append("")
    lines += ["The full configuration is in [config.yaml](config.yaml)."]
    return "\n".join(lines) + "\n"


class Pusher(QObject):
    """Pushes in the background, so a slow or failing push never blocks the app."""

    done = Signal(bool, str)

    def push(self, folder: Path):
        proc = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("GIT_TERMINAL_PROMPT", "0")
        proc.setProcessEnvironment(env)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.finished.connect(lambda code, _status: self.done.emit(
            code == 0, bytes(proc.readAll()).decode(errors="replace").strip()))
        proc.start("git", ["-C", str(folder), "push"])
