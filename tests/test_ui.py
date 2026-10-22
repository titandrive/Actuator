"""The main window, driven the way a user would: editing, saving, undo, folders, copy/paste,
conditions, restore, broken configs, recording, options, device settings and git."""

import os
import subprocess
import unittest
from unittest import mock

from helpers import APP, AppTest

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QInputDialog, QLineEdit, QMessageBox

from actuator import config as cfg, devices, iconpicker, versions, writer, yaml_editor


class StartupTests(AppTest):
    def test_sidebar_and_tabs(self):
        labels = self.sidebar_labels()
        self.assertEqual(labels[:2], ["Global", "Apps"])
        self.assertIn("Firefox", labels)
        self.assertIn("Factorio", labels)
        tabs = self.w.pages.widget(0)
        titles = [tabs.tabBar().tabData(i) for i in range(tabs.count())]
        self.assertEqual(titles, ["Mouse", "Keyboard", "Touchpad", "Touchscreen"])  # no Pointer

    def test_lists_show_only_bound_buttons(self):
        self.pick("Global")
        self.assertEqual(list(self.tab("Mouse").rows()), [])
        self.pick("Factorio")
        self.assertEqual([r.text(0) for r in self.tab("Mouse").rows()], ["Forward", "Back"])

    def test_complex_bindings_appear_under_global(self):
        self.pick("Global")
        rows = [(r.text(0), r.text(1)) for r in self.tab("Touchpad").rows()]
        self.assertTrue(any("conditions in YAML" in a for _g, a in rows))


class EditingTests(AppTest):
    def edit_firefox_close(self, keys_text):
        self.pick("Firefox")
        page = self.tab("Mouse")
        self.select_row(page, "Meta+W")
        ed = page.editor
        ed.value.setFocus()
        ed.value.selectAll()
        QTest.keyClicks(ed.value, keys_text)
        APP.processEvents()
        return page

    def test_edit_save_and_undo(self):
        self.edit_firefox_close("Ctrl+T")
        self.assertTrue(self.w.dirty)
        self.assertTrue(self.w.save_button.isEnabled())
        QTest.qWait(600)                       # undo groups quick edits
        self.w.undo()
        self.assertFalse(self.w.dirty)
        self.assertFalse(self.w.save_button.isEnabled())
        self.w.redo()
        self.assertTrue(self.w.dirty)
        self.w.save()
        saved = next(b for b in self.disk().bindings if b.id == "firefox_mouse_close")
        self.assertEqual(saved.data["actions"][0]["input"][0]["keyboard"], ["leftctrl+t"])
        self.assertEqual(saved.data["conditions"], ["$window_class == firefox", "$window_fullscreen"])
        self.assertIn(("config", "reload"), self.ctl_calls)
        self.assertEqual(len(writer.snapshots()), 1)

    def test_instant_change_does_not_save_pending_edits(self):
        self.edit_firefox_close("Ctrl+T")
        QInputDialog.getText = staticmethod(lambda *a, **k: ("Games", True))
        self.w.new_folder("factorio")
        disk = self.disk()
        self.assertEqual(disk.folders, {"Games": ["factorio"]})
        close = next(b for b in disk.bindings if b.id == "firefox_mouse_close")
        self.assertEqual(close.data["actions"][0]["input"][0]["keyboard"], ["leftctrl+w"])  # not saved yet
        self.assertTrue(self.w.dirty)
        QTest.qWait(600)
        self.w.undo()                          # undoing the edit keeps the saved folder
        self.assertEqual(self.w.config.folders, {"Games": ["factorio"]})

    def test_add_gesture_change_type_and_delete(self):
        self.pick("Firefox")
        self.w._add_gesture("touchpad", "swipe", "app", "firefox")
        page = self.tab("Touchpad")
        ed = page.editor
        self.assertEqual(ed.binding.type, "swipe")
        ed.type_box.setCurrentIndex(ed.type_box.findData("pinch"))
        self.assertEqual((ed.binding.data["type"], ed.binding.data["direction"]), ("pinch", "in"))
        ed.value.setText("Ctrl+Plus")
        ed._do_edited()
        self.assertIn("Ctrl+Plus".lower().replace("plus", "plus"), str(ed.binding.data["actions"]).lower())
        ed._delete()
        APP.processEvents()
        self.assertFalse(any(b.type == "pinch" for b in self.w.config.bindings_for("app", "firefox")))

    def test_add_keyboard_shortcut_like_a_gesture(self):
        self.pick("Firefox")
        self.w._add_shortcut("app", "firefox")
        page = self.tab("Keyboard")
        ed = page.editor
        self.assertEqual(page.tree.currentItem().text(0), "New shortcut")
        self.assertFalse(ed.keys_error.text())       # no nagging before keys are set
        warned = []
        with mock.patch.object(QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a[2]))):
            self.w.save()
        self.assertTrue(warned)                      # no keys: nothing written
        self.assertFalse(any(b.type == "shortcut" and not b.button for b in self.disk().bindings))
        ed.keys_field.setText("Ctrl+A")
        ed._keys_edited()
        self.assertEqual(ed.binding.data["shortcut"], ["leftctrl", "a"])
        self.assertEqual(page.tree.currentItem().text(0), "Ctrl+A")
        ed.keys_field.setText("Meta+W")              # what the mouse's side button sends
        ed._keys_edited()
        self.assertEqual(ed.binding.data["shortcut"], ["leftctrl", "a"])
        self.assertIn("Mouse tab", ed.keys_error.text())

    def test_reset_button_name(self):
        self.w.apply_now(lambda c: c.rename_button("forward", "Side 1"))
        self.pick("Factorio")
        self.assertIn("Side 1", [r.text(0) for r in self.tab("Mouse").rows()])
        self.w._reset_button_name("forward")
        self.assertNotIn("forward", self.disk().button_names)
        self.assertIn("Forward", [r.text(0) for r in self.tab("Mouse").rows()])

    def test_rename_button_saves_instantly(self):
        self.pick("Factorio")
        page = self.tab("Mouse")
        row = self.select_row(page, "Forward")
        row.setText(0, "Side button 1")
        APP.processEvents()
        self.assertEqual(self.disk().button_names.get("forward"), "Side button 1")
        self.assertFalse(self.w.dirty)


class LabelColumnTests(AppTest):
    def test_label_column_edits_save_instantly(self):
        self.pick("Factorio")
        page = self.tab("Mouse")
        self.assertEqual([page.tree.headerItem().text(i) for i in range(3)], ["Trigger", "Action", "Note"])
        row = self.select_row(page, "Forward")
        row.setText(2, "Opens the inventory")
        APP.processEvents()
        self.assertEqual(self.disk().labels, {"factorio_forward": "Opens the inventory"})
        self.assertFalse(self.w.dirty)
        self.pick("Factorio")
        self.assertEqual(next(r for r in self.tab("Mouse").rows() if r.text(0) == "Forward").text(2),
                         "Opens the inventory")


class ConditionTests(AppTest):
    def test_builder_writes_conditions_that_keep_the_scope(self):
        self.w.settings.setValue("dev_mode", True)
        self.w.settings.setValue("dev/only_if", True)
        self.pick("Firefox")
        page = self.tab("Touchpad")
        self.select_row(page, "3-finger swipe up")
        cb = page.editor.conditions
        self.assertTrue(page.editor.cond_box.isVisibleTo(page))
        cb._add()                                            # Window is fullscreen
        cb._add()
        cb._set_kind(1, "Modifier key")                      # is held ctrl
        cb.mode.setCurrentIndex(cb.mode.findData("any"))
        APP.processEvents()
        self.assertIn("when any of", cb.summary.text())
        self.w.save()
        b = next(x for x in self.disk().bindings if x.id == "firefox_tabs_up")
        self.assertEqual(b.data["conditions"], ["$window_class == firefox",
                                                {"any": ["$window_fullscreen", "$keyboard_modifiers contains ctrl"]}])
        self.assertEqual((b.scope, b.app), ("app", "firefox"))   # still listed under Firefox
        self.pick("Firefox")
        row = next(r for r in self.tab("Touchpad").rows() if r.text(0) == "3-finger swipe up")
        self.assertIn("(only if +2)", row.text(1))

    def test_hidden_when_option_off(self):
        self.pick("Firefox")
        page = self.tab("Touchpad")
        self.select_row(page, "3-finger swipe up")
        self.assertFalse(page.editor.cond_box.isVisibleTo(page))


class CopyPasteTests(AppTest):
    def test_copy_binding_to_another_app(self):
        self.pick("Factorio")
        page = self.tab("Mouse")
        self.w._copy_binding(self.select_row(page, "Forward"))
        self.pick("Firefox")
        self.w._paste_here()
        self.assertTrue(any(b.button == "forward" for b in self.w.config.bindings_for("app", "firefox")))
        self.assertTrue(self.w.dirty)
        self.w.save()
        self.assertTrue(any(b.button == "forward" for b in self.disk().bindings_for("app", "firefox")))


class FolderTests(AppTest):
    def setUp(self):
        super().setUp()
        QInputDialog.getText = staticmethod(lambda *a, **k: ("Games", True))
        self.w.new_folder("factorio")
        APP.processEvents()

    def folder(self):
        return next(self.w.sidebar.topLevelItem(i) for i in range(self.w.sidebar.topLevelItemCount())
                    if self.w.sidebar.topLevelItem(i).data(0, self.ui.ROLE) == ("folder", "Games"))

    def click(self, item, button=Qt.MouseButton.LeftButton):
        QTest.mouseClick(self.w.sidebar.viewport(), button, pos=self.w.sidebar.visualItemRect(item).center())
        APP.processEvents()

    def test_collapse_survives_redraws_and_icon_changes(self):
        self.pick("Factorio")                                 # selected app inside the folder
        self.click(self.folder())
        self.assertFalse(self.folder().isExpanded())
        self.click(self.folder(), Qt.MouseButton.RightButton)
        self.assertFalse(self.folder().isExpanded())          # right-click doesn't toggle
        iconpicker.IconPicker.exec = lambda self: (setattr(self, "chosen", "gamepad-2"), 1)[1]
        self.w.change_folder_icon("Games")
        self.assertFalse(self.folder().isExpanded())
        self.w.move_app("firefox", None)                      # any rebuild
        self.assertFalse(self.folder().isExpanded())
        self.assertEqual(self.disk().folder_icons, {"Games": "gamepad-2"})

    def test_inline_rename(self):
        self.w.rename_folder("Games")
        APP.processEvents()
        ed = self.w.sidebar.findChild(QLineEdit)
        ed.selectAll()
        QTest.keyClicks(ed, "Play")
        QTest.keyClick(ed, Qt.Key.Key_Return)
        QTest.qWait(50)
        self.assertEqual(self.disk().folders, {"Play": ["factorio"]})

    def test_drag_highlight_targets(self):
        sb = self.w.sidebar
        firefox = next(sb.topLevelItem(i) for i in range(sb.topLevelItemCount())
                       if sb.topLevelItem(i).data(0, self.ui.ROLE) == ("app", "firefox"))
        sb.setCurrentItem(firefox)
        self.assertIsNotNone(sb._drop_highlight(sb.visualItemRect(self.folder()).center()))
        self.assertIsNone(sb._drop_highlight(sb.visualItemRect(firefox).center()))


class SafetyTests(AppTest):
    def test_broken_config_blocks_writes_and_recovers(self):
        good = self.path.read_text()
        broken = good.replace("    type: press", "   type: press", 1)
        self.path.write_text(broken)
        QTest.qWait(800)
        self.assertIsNotNone(self.w.broken)
        self.assertIn("indentation", self.w.broken)
        self.assertFalse(self.w.body.isEnabled())
        self.assertFalse(self.w.apply_now(lambda c: c.folders.setdefault("X", [])))
        self.assertEqual(self.path.read_text(), broken)       # untouched
        self.path.write_text(good)
        QTest.qWait(800)
        self.assertIsNone(self.w.broken)
        self.assertTrue(self.w.body.isEnabled())

    def test_restore_from_versions_window(self):
        before = self.path.read_text()
        self.w.apply_now(lambda c: c.remove_app("factorio"), take_snapshot=True)
        self.assertFalse(self.disk().bindings_for("app", "factorio"))
        dlg = versions.VersionsDialog(self.w, self.w.config)
        self.assertGreaterEqual(dlg.list.count(), 1)
        dlg.list.setCurrentRow(0)
        self.assertIn("Factorio", dlg.view.toPlainText())    # preview shows what comes back
        self.w.restore(dlg._path(), previewed=True)
        self.assertEqual(len(self.disk().bindings_for("app", "factorio")), 2)
        self.assertNotEqual(before, "")

    def test_yaml_editor_save_path(self):
        self.w.settings.setValue("dev_mode", True)
        self.w.settings.setValue("dev/yaml_editor", True)
        yaml_editor.YamlEditor.exec = lambda self: (self.edit.setPlainText(self.text().replace(
            "angle_tolerance: 45", "angle_tolerance: 30")), self._validate(), 1)[2]
        self.w.open_yaml_editor()
        self.assertIn("angle_tolerance: 30", self.path.read_text())
        self.assertEqual(len(writer.snapshots()), 1)


class RecordingTests(AppTest):
    def test_record_alt_f4_with_shortcuts_paused(self):
        from actuator import pause
        calls = []
        pause._run = lambda *a: (calls.append(" ".join(a[-2:]) if "qdbus6" in a else " ".join(a)), True)[1]

        def press():
            dlg = APP.activeModalWidget()
            QTest.keyClick(dlg.prompt, Qt.Key.Key_F4, Qt.KeyboardModifier.AltModifier)
        QTimer.singleShot(200, press)
        self.pick("Firefox")
        self.w.add_button("app", "firefox")
        self.assertTrue(any(b.button == "leftalt+f4" for b in self.w.config.bindings_for("app", "firefox")))
        self.assertEqual(calls[0], "org.kde.KGlobalAccel.blockGlobalShortcuts true")
        self.assertIn("org.kde.KGlobalAccel.blockGlobalShortcuts false", calls)
        self.assertIsNone(pause._active)


class OptionsAndDevicesTests(AppTest):
    def test_device_settings_dialog_applies_rules(self):
        devices.connected_devices = lambda: []
        devices.DeviceSettingsDialog.exec = lambda self: (devices.set_value(
            self.copy, ("type", "mouse"), "motion_threshold", 25), setattr(self, "dirty", True), 1)[2]
        self.w.open_device_settings()
        rules = self.disk().data["device_rules"]
        self.assertIn({"conditions": "$mouse", "motion_threshold": 25}, [dict(r) for r in rules])

    def test_options_hide_tabs(self):
        dlg = self.ui.OptionsDialog(self.w, {t: True for t in cfg.DEVICE_TABS}, True, 10)
        dlg.boxes["Touchscreen"].setChecked(False)
        for t, on in dlg.tabs().items():
            self.w.settings.setValue(f"tabs/{t}", on)
        self.w._fill_sidebar()
        tabs = self.w.pages.widget(0)
        self.assertNotIn("Touchscreen", [tabs.tabBar().tabData(i) for i in range(tabs.count())])


class RebuildTests(AppTest):
    def test_status_and_start_explain_a_needed_rebuild(self):
        shown = []
        self.w.loaded, self.w.suspended, self.w.rebuild = False, False, ("6.8.0", "6.8.1")
        self.w._explain_rebuild = lambda: shown.append(True)
        self.w._update_status()
        self.assertIn("Needs rebuild", self.w.status.text())
        self.w.toggle_running()                               # Start explains instead of trying
        self.assertEqual(shown, [True])

    def test_clicking_the_problem_explains_it(self):
        from PySide6.QtTest import QTest
        shown = []
        self.w.loaded, self.w.suspended, self.w.rebuild = False, False, ("6.8.0", "6.8.1")
        self.w._explain_rebuild = lambda: shown.append("rebuild")
        self.w._show_issues = lambda: shown.append("issues")
        self.w._issues = "206:7: error: something"
        self.w._update_status()
        QTest.mouseClick(self.w.status, Qt.MouseButton.LeftButton)
        QTest.mouseClick(self.w.issues_label, Qt.MouseButton.LeftButton)
        self.assertEqual(shown, ["rebuild", "issues"])

    def test_detects_version_mismatch(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            plugin = Path(tmp) / "kwin_gestures.so"
            plugin.write_bytes(b"\0junk org.kde.kwin.EffectPluginFactory6.8.0\0more")
            self.patch_attr(self.ui, "PLUGIN_FILES", (str(plugin),))
            self.patch_attr(self.ui, "needs_rebuild", self.real_needs_rebuild)
            self.patch_attr(self.ui, "run", lambda *a: (True, "kwin 6.8.1"))
            self.assertEqual(self.ui.needs_rebuild(), ("6.8.0", "6.8.1"))
            self.patch_attr(self.ui, "run", lambda *a: (True, "kwin 6.8.0"))
            self.assertIsNone(self.ui.needs_rebuild())


class StoredPreferencesTests(AppTest):
    def test_options_saved_in_config_and_restored_from_it(self):
        self.w.settings.setValue("dev_mode", True)
        self.w.settings.setValue("dev/store_prefs", True)
        self.w.settings.setValue("tabs/Touchscreen", False)
        self.w._store_prefs()
        prefs = self.disk().prefs
        self.assertEqual(prefs["tabs"], "Mouse, Keyboard, Touchpad")
        self.assertIn("store_prefs", prefs["developer_options"])
        self.assertIn("# actuator:pref developer_mode = on", self.path.read_text())
        self.assertFalse(self.w.dirty)                        # written at once, not pending
        # A fresh install: its own settings are empty, the restored config brings them back.
        for key in ("tabs/Touchscreen", "dev_mode", "dev/store_prefs"):
            self.w.settings.remove(key)
        self.w.load()
        self.assertFalse(self.w.tab_visible("Touchscreen"))
        self.assertTrue(self.w.feature("store_prefs"))
        # Turned off: the lines come out of the config.
        self.w.settings.setValue("dev/store_prefs", False)
        self.w._store_prefs()
        self.assertEqual(self.disk().prefs, {})


class GitIntegrationTests(AppTest):
    def test_auto_commit_on_every_change(self):
        env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
               "GIT_COMMITTER_EMAIL": "t@t"}
        os.environ.update(env)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.dir)
        subprocess.run(["git", "add", "config.yaml"], cwd=self.dir)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=self.dir)
        for k, v in (("dev_mode", True), ("dev/git_autocommit", True), ("git/push", False)):
            self.w.settings.setValue(k, v)
        QInputDialog.getText = staticmethod(lambda *a, **k: ("Games", True))
        self.w.new_folder("factorio")                          # instant change
        b = next(x for x in self.w.config.bindings if x.id == "factorio_forward")
        b.data["actions"][0]["input"][0]["keyboard"] = ["q"]
        self.w.mark_dirty()
        self.w.save()                                          # save
        log = subprocess.run(["git", "log", "--format=%s"], cwd=self.dir, capture_output=True,
                             text=True).stdout.splitlines()
        self.assertEqual(log[0], "Actuator: Factorio: ~ Forward: E → Q")
        self.assertIn("Folders", log[1])
        self.assertIn("Committed", self.w.git_status.text())


if __name__ == "__main__":
    unittest.main()
