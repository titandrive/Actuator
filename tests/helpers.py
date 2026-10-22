"""Shared test setup: a sample config, temp folders, and a sandboxed app (no real InputActions,
KWin, git remote, settings or config are ever touched)."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from actuator import config as cfg  # noqa: E402
from actuator import apps, devices, pause, writer  # noqa: E402

APP = QApplication.instance() or QApplication([])

SAMPLE = """\
# A hand-written config, the way InputActions users write them.
mouse:
  gestures:
  - id: factorio_forward
    type: press
    mouse_buttons: [forward]
    instant: true
    conditions:
    - $window_class matches (?i)factorio
    actions:
    - on: begin
      input:
      - keyboard: [e]
  - id: factorio_back_hold
    type: press
    mouse_buttons: [back]
    conditions:
    - $window_class matches (?i)factorio
    actions:
    - on: begin
      input:
      - keyboard: [+f]
    - on: end_cancel
      input:
      - keyboard: [-f]
keyboard:
  gestures:
  - id: firefox_mouse_tabs_left
    type: shortcut
    shortcut: [leftshift, leftmeta, leftbrace]
    clear_modifiers: true
    conditions:
    - $window_class == firefox
    actions:
    - on: begin
      input:
      - keyboard: [leftctrl+pageup]
  - id: firefox_mouse_close
    type: shortcut
    shortcut: [leftmeta, w]
    conditions:
    - $window_class == firefox
    - $window_fullscreen
    actions:
    - on: begin
      input:
      - keyboard: [leftctrl+w]
touchpad:
  gestures:
  - id: global_switcher
    type: swipe
    fingers: 3
    direction: left_right
    actions:
    - on: begin
      input:
      - keyboard: [+leftalt, tab]
    - on: update
      interval: -75
      input:
      - keyboard: [leftshift+tab]
    - on: end_cancel
      input:
      - keyboard: [-leftalt]
  - id: firefox_tabs_up
    type: swipe
    fingers: 3
    direction: up
    conditions:
    - $window_class == firefox
    actions:
    - on: begin
      input:
      - keyboard: [leftctrl+pageup]
  - id: two_apps
    type: tap
    fingers: 4
    conditions:
    - $window_class == firefox
    - $window_class == chromium
    actions:
    - on: end
      command: notify-send hi
  - just a string Actuator doesn't understand
pointer:
  gestures:
  - id: corner
    type: hover
    actions:
    - command: echo corner
device_rules:
- conditions: $touchpad
  swipe:
    angle_tolerance: 45
mystery_top_level: {kept: true}
"""


class TempConfig(unittest.TestCase):
    """Each test gets its own config.yaml, snapshot folder and settings file."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="actuator-test-"))
        self.path = self.dir / "config.yaml"
        self.path.write_text(SAMPLE)
        self._saved = (cfg.CONFIG_PATH, cfg.SYSTEM_CONFIG_PATH, writer.SNAPSHOT_DIR, writer.SNAPSHOT_KEEP)
        cfg.CONFIG_PATH = self.path
        cfg.SYSTEM_CONFIG_PATH = self.dir / "no-system-config.yaml"
        writer.SNAPSHOT_DIR = self.dir / "snapshots"
        writer.SNAPSHOT_KEEP = 10

    def tearDown(self):
        cfg.CONFIG_PATH, cfg.SYSTEM_CONFIG_PATH, writer.SNAPSHOT_DIR, writer.SNAPSHOT_KEEP = self._saved
        shutil.rmtree(self.dir, ignore_errors=True)


class AppTest(TempConfig):
    """A MainWindow wired to the temp config, with every outside effect stubbed out."""

    def setUp(self):
        super().setUp()
        from actuator import ui

        self.ui = ui
        self.ctl_calls = []
        self._patches = []

        def patch(obj, name, value):
            self._patches.append((obj, name, getattr(obj, name)))
            setattr(obj, name, value)

        self.patch_attr = patch  # for tests to stub more, undone at tearDown like these
        self.real_needs_rebuild = ui.needs_rebuild

        patch(ui, "run_ctl", lambda *a: (self.ctl_calls.append(a), (True, ""))[1])
        patch(ui, "effect_loaded", lambda: True)
        patch(ui, "needs_rebuild", lambda: None)
        patch(ui, "kwin_pid", lambda: "1")
        patch(pause, "_run", lambda *a: True)
        patch(devices, "touchpad_access_missing", lambda: False)
        patch(apps, "steam_id", lambda app: None)  # the Steam games installed here aren't the sample's
        settings_file = str(self.dir / "settings.ini")
        patch(ui, "QSettings", lambda *a: QSettings(settings_file, QSettings.Format.IniFormat))
        self.answers = {}  # dialog title/text fragment -> button to press
        default = QMessageBox.StandardButton.Yes

        def answer(*args, **_kw):
            text = " ".join(str(a) for a in args[1:3])
            for fragment, button in self.answers.items():
                if fragment in text:
                    return button
            return QMessageBox.StandardButton.Save if "Reorganize" in text else default
        patch(QMessageBox, "question", staticmethod(answer))
        patch(QMessageBox, "warning", staticmethod(answer))
        patch(QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
        patch(QMessageBox, "critical", staticmethod(lambda *a, **k: self.fail(f"error dialog: {a[2]}")))
        self.w = ui.MainWindow()
        self.w.show()
        APP.processEvents()

    def tearDown(self):
        self.w.dirty = False
        self.w.close()
        self.w.deleteLater()
        APP.processEvents()
        for obj, name, value in reversed(self._patches):
            setattr(obj, name, value)
        super().tearDown()

    # helpers
    def sidebar_labels(self):
        from PySide6.QtWidgets import QTreeWidgetItemIterator
        out, it = [], QTreeWidgetItemIterator(self.w.sidebar)
        while it.value():
            out.append(it.value().text(0))
            it += 1
        return out

    def pick(self, label):
        from PySide6.QtWidgets import QTreeWidgetItemIterator
        it = QTreeWidgetItemIterator(self.w.sidebar)
        while it.value():
            if it.value().text(0).split("  (")[0] == label:
                self.w.sidebar.setCurrentItem(it.value())
                APP.processEvents()
                return it.value()
            it += 1
        self.fail(f"no sidebar item {label!r}: {self.sidebar_labels()}")

    def tab(self, title):
        tabs = self.w.pages.widget(0)
        for i in range(tabs.count()):
            if tabs.tabBar().tabData(i) == title:
                tabs.setCurrentIndex(i)
                APP.processEvents()
                return tabs.widget(i).build()
        self.fail(f"no tab {title}")

    def select_row(self, page, text):
        for r in page.rows():
            if r.text(0) == text:
                page.tree.setCurrentItem(r)
                APP.processEvents()
                return r
        self.fail(f"no row {text!r} in {[r.text(0) for r in page.rows()]}")

    def disk(self):
        return cfg.load(self.path)
