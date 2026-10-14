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


def commit(path: Path, msg: str) -> tuple[bool, str]:
    """Commit just this file. Other changes in the repo are left alone."""
    folder = path.parent
    ok, out = _git(folder, "add", "--", path.name)
    if not ok:
        return False, out
    ok, out = _git(folder, "diff", "--cached", "--quiet", "--", path.name)
    if ok:
        return True, "nothing to commit"  # no difference from the last commit
    return _git(folder, "commit", "-m", msg, "--", path.name)


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
