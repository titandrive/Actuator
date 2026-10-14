"""Pausing Plasma's global shortcuts (and InputActions) while recording a key or button,
so the press is recorded instead of triggering whatever it's bound to."""

import atexit
import subprocess

_active: "ShortcutPause | None" = None
# Set by the main window: whether InputActions is running (so a pause never resumes it if you'd paused it).
inputactions_running = lambda: False  # noqa: E731


def _run(*cmd: str) -> bool:
    try:
        return subprocess.run(cmd, capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def block_global_shortcuts(on: bool) -> bool:
    """The same switch System Settings uses while you record a new shortcut."""
    return _run("qdbus6", "org.kde.kglobalaccel", "/kglobalaccel",
                "org.kde.KGlobalAccel.blockGlobalShortcuts", "true" if on else "false")


class ShortcutPause:
    def __init__(self, suspend_inputactions: bool | None = None):
        self.suspend_inputactions = inputactions_running() if suspend_inputactions is None else suspend_inputactions
        self.suspended = False
        self.blocked = False

    def start(self):
        global _active
        if _active is not None:
            return
        self.blocked = block_global_shortcuts(True)
        if self.suspend_inputactions:
            self.suspended = _run("inputactions", "suspend")
        _active = self

    def stop(self):
        global _active
        if _active is not self:
            return
        if self.blocked:
            block_global_shortcuts(False)
        if self.suspended:
            _run("inputactions", "resume")
        self.blocked = self.suspended = False
        _active = None


@atexit.register
def _release():
    if _active is not None:
        _active.stop()


def recover():
    """Called at startup: if Actuator ever crashed mid-recording, shortcuts may still be blocked."""
    block_global_shortcuts(False)
