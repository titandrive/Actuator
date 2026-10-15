"""Config model, saving, conditions, restore preview, copying, YAML checking, keys, apps."""

import os
import stat
import tempfile
import unittest
from pathlib import Path

from helpers import SAMPLE, TempConfig  # noqa: F401  (also sets up Qt)

from actuator import apps, conditions as conds, config as cfg, copying, describe as d, diffing
from actuator import editor, keys, writer, yaml_editor


class LoadingTests(TempConfig):
    def test_scopes(self):
        c = cfg.load(self.path)
        scope = {b.id: (b.scope, b.app) for b in c.bindings}
        self.assertEqual(scope["global_switcher"], ("global", None))
        self.assertEqual(scope["factorio_forward"], ("app", "factorio"))       # matches (?i)
        self.assertEqual(scope["firefox_tabs_up"], ("app", "firefox"))          # ==
        self.assertEqual(scope["firefox_mouse_close"], ("app", "firefox"))      # app + extra condition
        self.assertEqual(scope["two_apps"], ("custom", None))                   # two apps: too complex
        self.assertEqual(c.apps, ["factorio", "firefox"])

    def test_buttons(self):
        c = cfg.load(self.path)
        srcs = {b.src: b.kind for b in c.buttons}
        self.assertEqual(srcs["forward"], "mouse")
        self.assertEqual(srcs["back"], "mouse")
        self.assertEqual(srcs["leftshift+leftmeta+leftbrace"], "shortcut")  # "mouse" in its id

    def test_pointer_and_unknown_kept_in_model(self):
        c = cfg.load(self.path)
        self.assertTrue(any(b.section == "pointer" for b in c.bindings))
        self.assertIn("mystery_top_level", c.data)

    def test_registry_lines(self):
        text = SAMPLE + ("\n# actuator:button forward = Side button 1\n# actuator:app Project Zomboid\n"
                         "# actuator:folder Games = factorio, Project Zomboid\n"
                         "# actuator:foldericon Games = gamepad-2\n# actuator:device Razer Mouse = mouse\n")
        c = cfg.loads(text)
        self.assertEqual(c.button_names["forward"], "Side button 1")
        self.assertIn("Project Zomboid", c.extra_apps)                       # spaces in names
        self.assertEqual(c.folders["Games"], ["factorio", "Project Zomboid"])
        self.assertEqual(c.folder_icons["Games"], "gamepad-2")
        self.assertEqual(c.device_types["Razer Mouse"], "mouse")
        self.assertEqual(c.folder_of("FACTORIO"), "Games")                    # case-insensitive

    def test_same_app_ignores_case(self):
        c = cfg.load(self.path)
        self.assertEqual(c.same_app("Factorio"), "factorio")
        self.assertEqual(len(c.bindings_for("app", "FACTORIO")), 2)


class EditTests(TempConfig):
    def test_remove_app_removes_its_bindings_and_folder_entry(self):
        c = cfg.load(self.path)
        c.folders["Games"] = ["factorio"]
        c.remove_app("factorio")
        self.assertEqual(c.bindings_for("app", "factorio"), [])
        self.assertEqual(c.folders["Games"], [])
        self.assertTrue(any(b.id == "firefox_tabs_up" for b in c.bindings))  # others untouched

    def test_folder_rename_and_delete_carry_the_icon(self):
        c = cfg.load(self.path)
        c.folders["Games"] = ["factorio"]
        c.set_folder_icon("Games", "gamepad-2")
        c.rename_folder("Games", "Play")
        self.assertEqual(c.folder_icons, {"Play": "gamepad-2"})
        c.delete_folder("Play")
        self.assertEqual((c.folders, c.folder_icons), ({}, {}))

    def test_rename_button_back_to_raw_clears_name(self):
        c = cfg.load(self.path)
        c.rename_button("forward", "Side 1")
        self.assertEqual(c.button_names["forward"], "Side 1")
        c.rename_button("forward", "forward")
        self.assertNotIn("forward", c.button_names)


class SavingTests(TempConfig):
    def test_first_save_keeps_original_and_round_trips(self):
        c = cfg.load(self.path)
        writer.save(c)
        self.assertEqual(writer.original_path(self.path).read_text(), SAMPLE)
        self.assertTrue(writer.is_managed(self.path))
        again = cfg.load(self.path)
        self.assertEqual(writer.render(again), self.path.read_text())          # stable on re-save
        self.assertEqual(len(again.bindings), len(c.bindings))

    def test_keeps_what_actuator_does_not_understand(self):
        writer.save(cfg.load(self.path))
        text = self.path.read_text()
        for kept in ("mystery_top_level", "just a string Actuator doesn't understand", "id: corner",
                     "angle_tolerance: 45", "two_apps"):
            self.assertIn(kept, text)

    def test_verify_refuses_to_drop_data(self):
        c = cfg.load(self.path)
        bad = writer.render(c).replace("id: global_switcher", "id: renamed")
        with self.assertRaises(ValueError):
            writer.verify(c, bad)

    def test_atomic_write_keeps_permissions(self):
        os.chmod(self.path, 0o600)
        writer.save(cfg.load(self.path))
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_snapshots_prune_but_keep_locked(self):
        writer.SNAPSHOT_KEEP = 2
        writer.SNAPSHOT_DIR.mkdir(parents=True)
        for i in range(5):
            (writer.SNAPSHOT_DIR / f"config-2026010{i}-000000.yaml").write_text("x: 1\n")
        oldest = writer.snapshots()[-1]
        writer.set_locked(oldest, True)
        writer.prune()
        left = writer.snapshots()
        self.assertIn(oldest, left)
        self.assertEqual(len([x for x in left if not writer.is_locked(x)]), 2)

    def test_original_is_locked_by_default(self):
        orig = writer.original_path(self.path)
        self.assertTrue(writer.is_locked(orig))
        writer.set_locked(orig, False)
        self.assertFalse(writer.is_locked(orig))

    def test_missing_config_is_created_managed(self):
        self.path.unlink()
        self.assertTrue(cfg.ensure_exists(self.path))
        self.assertTrue(writer.is_managed(self.path))
        self.assertEqual(cfg.load(self.path).bindings, [])


class LabelTests(TempConfig):
    def test_labels_are_comments_above_their_binding(self):
        c = cfg.load(self.path)
        c.set_label("factorio_forward", "Opens   the\ninventory")       # tidied to one line
        writer.save(c)
        text = self.path.read_text()
        self.assertIn("# Note: Opens the inventory\n  - id: factorio_forward", text)
        again = cfg.load(self.path)
        self.assertEqual(again.labels, {"factorio_forward": "Opens the inventory"})
        again.set_label("factorio_forward", "")
        writer.save(again)
        self.assertNotIn("Note:", self.path.read_text())

    def test_copy_keeps_label(self):
        c = cfg.load(self.path)
        c.set_label("factorio_forward", "Inventory")
        b = next(x for x in c.bindings if x.id == "factorio_forward")
        copying.paste(c, [(b.section, b.data)], "app", "firefox", replace=False)
        new = next(x for x in c.bindings_for("app", "firefox") if x.button == "forward")
        self.assertEqual(c.labels[new.id], "Inventory")


class ConditionTests(unittest.TestCase):
    ROWS = [
        ("Window", "is fullscreen", ""), ("Window", "is not maximized", ""),
        ("Window title", "contains", "YouTube"), ("Window title", "matches", ".*Video.*"),
        ("Keys held", "include", "ctrl"), ("Keys held", "don't include", "meta"), ("Keys held", "are none", ""),
        ("Pointer", "is at top edge", ""), ("Pointer", "is at right edge", ""),
        ("Screen", "is", "eDP-1"), ("Screen", "is not", "DP-1"),
        ("Overview", "is open", ""), ("Custom", "", "$window_pid > 100"),
    ]

    def test_every_row_round_trips(self):
        for row in self.ROWS:
            with self.subTest(row=row):
                self.assertEqual(conds.from_text(conds.to_text(row)), row)

    def test_split_join_round_trip(self):
        cases = [
            None,
            ["$window_class == firefox"],
            ["$window_class == firefox", "$window_fullscreen"],
            ["$window_class matches (?i)^Factorio$", {"any": ["$keyboard_modifiers contains ctrl",
                                                              "$plasma_overview_active"]}],
            ["$window_title contains YouTube"],
            [{"none": ["$window_fullscreen"]}],
        ]
        for c in cases:
            with self.subTest(c=c):
                parts = conds.split(c)
                self.assertIsNotNone(parts)
                self.assertEqual(conds.join(*parts), c)

    def test_complex_conditions_are_left_alone(self):
        self.assertIsNone(conds.split(["$window_class == a", "$window_class == b"]))
        self.assertIsNone(conds.split([{"all": [{"any": ["$x"]}]}]))
        self.assertEqual(conds.extra_count(["$window_class == a", "$window_class == b"]), -1)

    def test_app_condition(self):
        self.assertEqual(conds.app_condition("firefox"), "$window_class == firefox")
        c = conds.app_condition("Project Zomboid")
        self.assertEqual(cfg.parse_scope([c]), ("app", "Project Zomboid"))
        self.assertEqual(cfg.parse_scope([conds.app_condition("com.anthropic.Claude")]),
                         ("app", "com.anthropic.Claude"))


class DiffTests(TempConfig):
    def test_changes_listed_as_before_after(self):
        cur, other = cfg.load(self.path), cfg.load(self.path)
        self.assertEqual(diffing.changes(cur, other), {})
        b = next(x for x in other.bindings if x.id == "factorio_forward")
        b.data["actions"][0]["input"][0]["keyboard"] = ["q"]
        other.remove_bindings([next(x for x in other.bindings if x.id == "firefox_tabs_up")])
        next(x for x in other.bindings if x.id == "global_switcher").data["fingers"] = 4
        other.data["device_rules"][0]["swipe"]["angle_tolerance"] = 30
        other.folders["Games"] = ["factorio"]
        g = diffing.changes(cur, other)
        self.assertIn(("~", "Forward", "E", "Q"), g["Factorio"])
        self.assertIn(("−", "3-finger swipe up", "Ctrl+PgUp", "removed"), g["Firefox"])
        self.assertTrue(any(m == "~" and "4 fingers" in a for m, _w, _b, a in g["Global"]))  # paired, not -/+
        self.assertIn(("~", "Touchpad: swipe angle tolerance", "45", "30"), g["Device settings"])
        self.assertTrue(any(w == "Folders" for _m, w, _b, _a in g["Other"]))


class CopyTests(TempConfig):
    def test_copy_retargets_and_handles_clashes(self):
        c = cfg.load(self.path)
        items = [(b.section, b.data) for b in c.bindings_for("app", "factorio")]
        added, skipped = copying.paste(c, items, "app", "Steam Game", replace=False)
        self.assertEqual((added, skipped), (2, 0))
        copies = c.bindings_for("app", "Steam Game")
        self.assertTrue(all(b.data["conditions"] == [conds.app_condition("Steam Game")] for b in copies))
        self.assertEqual(len({b.id for b in c.bindings}), len(c.bindings))       # ids unique
        self.assertEqual(copying.paste(c, items, "app", "Steam Game", replace=False), (0, 2))  # clashes skipped
        self.assertEqual(copying.paste(c, items, "app", "Steam Game", replace=True), (2, 0))
        self.assertEqual(len(c.bindings_for("app", "Steam Game")), 2)           # replaced, not doubled

    def test_extra_conditions_come_along_and_complex_is_skipped(self):
        c = cfg.load(self.path)
        close = next(b for b in c.bindings if b.id == "firefox_mouse_close")
        two = next(b for b in c.bindings if b.id == "two_apps")
        added, skipped = copying.paste(c, [(close.section, close.data), (two.section, two.data)],
                                       "global", None, replace=False)
        self.assertEqual((added, skipped), (1, 1))
        new = [b for b in c.bindings_for("global") if b.button == "leftmeta+w"][0]
        self.assertEqual(new.data["conditions"], ["$window_fullscreen"])


class YamlCheckTests(unittest.TestCase):
    def test_messages_point_at_the_right_line(self):
        good = SAMPLE
        self.assertTrue(yaml_editor.check(good)[0])
        lines = good.splitlines()
        i = next(n for n, l in enumerate(lines) if l.startswith("    type: press"))
        cases = {
            "indentation doesn't line up": lines[:i] + [lines[i][1:]] + lines[i + 1:],
            "missing a ':'": lines[:i] + ["    type press"] + lines[i + 1:],
            "tab": lines[:i] + ["\t" + lines[i].lstrip()] + lines[i + 1:],
        }
        for expect, broken in cases.items():
            with self.subTest(expect=expect):
                ok, msg, line = yaml_editor.check("\n".join(broken))
                self.assertFalse(ok)
                self.assertIn(expect, msg)
                self.assertEqual(line, i + 1)

    def test_top_level_must_be_mapping(self):
        self.assertFalse(yaml_editor.check("- a\n- b\n")[0])


class KeyTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(keys.parse_combo("Ctrl+Shift+T"), "leftctrl+leftshift+t")
        self.assertEqual(keys.parse_sequence("Ctrl+C, Ctrl+V"), ["leftctrl+c", "leftctrl+v"])
        self.assertEqual(keys.parse_combo("Meta+["), "leftmeta+leftbrace")
        with self.assertRaises(ValueError):
            keys.parse_sequence("  ")

    def test_actions_round_trip_through_forms(self):
        for mode, values in [("Press keys", {"on": "begin", "keys": "Ctrl+W"}),
                             ("Hold keys", {"keys": "F"}),
                             ("Run command", {"on": "end", "value": "konsole"}),
                             ("Plasma shortcut", {"on": "begin", "value": "kwin,Overview"})]:
            with self.subTest(mode=mode):
                actions = editor.build_actions(mode, values)
                got_mode, _ = editor.detect_mode(actions)
                self.assertEqual(got_mode, mode)

    def test_summary_text(self):
        self.assertEqual(d.token("+f"), "Hold F")
        self.assertEqual(d.token("leftctrl+pageup"), "Ctrl+PgUp")


class AppListTests(unittest.TestCase):
    def test_dedupe_and_dead_launchers(self):
        home = Path(tempfile.mkdtemp())
        apps_dir = home / "applications"
        apps_dir.mkdir()
        real = home / "game"
        real.write_text("")
        (apps_dir / "a.desktop").write_text(f"[Desktop Entry]\nType=Application\nName=Game\nExec={real}\n"
                                            "StartupWMClass=game\n")
        (apps_dir / "b.desktop").write_text(f"[Desktop Entry]\nType=Application\nName=Game\nExec={real}\n"
                                            "StartupWMClass=game\n")
        (apps_dir / "dead.desktop").write_text("[Desktop Entry]\nType=Application\nName=Dead\n"
                                               "Exec=/nonexistent/winapps cmd\n")
        old = (os.environ.get("XDG_DATA_HOME"), os.environ.get("XDG_DATA_DIRS"))
        os.environ["XDG_DATA_HOME"], os.environ["XDG_DATA_DIRS"] = str(home), str(home / "none")
        apps.installed.cache_clear()
        try:
            names = [a.name for a in apps.installed() if a.name in ("Game", "Dead")]
        finally:
            for k, v in zip(("XDG_DATA_HOME", "XDG_DATA_DIRS"), old):
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            apps.installed.cache_clear()
        self.assertEqual(names, ["Game"])


if __name__ == "__main__":
    unittest.main()
