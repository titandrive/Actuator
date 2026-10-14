"""Main window."""

import re
import shutil
import subprocess
from datetime import date, datetime
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu,
    QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy,
    QSplitter, QStackedWidget, QTabWidget, QToolBar, QToolButton, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)
from ruamel.yaml import YAML

from . import config as cfg
from . import describe as d
from . import writer

ROLE = Qt.ItemDataRole.UserRole
ADD_APP = "add-app"


def run_ctl(*args: str) -> tuple[bool, str]:
    try:
        p = subprocess.run(["inputactions", *args], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    return p.returncode == 0, (p.stdout + p.stderr).strip()


def active_window_class() -> str | None:
    ok, out = run_ctl("variables", "list")
    if ok:
        m = re.search(r"^window_class:\s*(.+)$", out, re.MULTILINE)
        if m and m.group(1).strip() != "<null>":
            return m.group(1).strip()
    return None


def button_name(b: cfg.Button) -> str:
    return b.name if b.name != b.src else d.button_label(b.src)


def scope_title(scope: str, app: str | None) -> str:
    return app.capitalize() if scope == "app" else scope.capitalize()


def app_counts(c: cfg.Config) -> dict[str, int]:
    counts = {"Global": len(c.bindings_for("global")), "Custom rules": len(c.bindings_for("custom"))}
    for app in c.apps:
        counts[app.capitalize()] = len(c.bindings_for("app", app))
    return counts


class AddAppDialog(QDialog):
    """Type a window class, or pick it from a window."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Add app")
        self.edit = QLineEdit(placeholderText="steam")
        self.pick = QPushButton(QIcon.fromTheme("crosshairs"), "Pick window")
        self.pick.clicked.connect(self._start_pick)
        self.hint = QLabel("Window class of the app. Pick window reads it from the window you click.")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: palette(placeholder-text);")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        row = QHBoxLayout()
        row.addWidget(self.edit, 1)
        row.addWidget(self.pick)
        layout = QVBoxLayout(self)
        layout.addLayout(row)
        layout.addWidget(self.hint)
        layout.addWidget(buttons)
        self.resize(420, 0)
        self._left = 0

    def _start_pick(self):
        self._left = 3
        self.pick.setEnabled(False)
        self._tick()

    def _tick(self):
        if self._left:
            self.hint.setText(f"Click the app's window. Reading in {self._left}…")
            self._left -= 1
            QTimer.singleShot(1000, self._tick)
            return
        wc = active_window_class()
        self.pick.setEnabled(True)
        self.raise_()
        self.activateWindow()
        if wc and wc.lower() != "actuator":
            self.edit.setText(wc)
            self.hint.setText(f"Picked {wc}.")
        else:
            self.hint.setText("Couldn't read a window. Try again, or type the class.")

    def _accept(self):
        if self.edit.text().strip():
            self.accept()
        else:
            self.hint.setText("Enter a window class first.")

    def value(self) -> str:
        return self.edit.text().strip()


class BindingView(QWidget):
    """When / Only if / Do summary of one binding, with its raw YAML on demand."""

    def __init__(self):
        super().__init__()
        self.title = QLabel()
        self.title.setStyleSheet("font-weight: 600;")
        self.yaml_button = QPushButton(QIcon.fromTheme("text-x-script"), "YAML")
        self.yaml_button.setCheckable(True)
        self.yaml_button.toggled.connect(lambda on: self.yaml.setVisible(on))
        self.when, self.only_if, self.do = QLabel(), QLabel(), QLabel()
        for label in (self.when, self.only_if, self.do):
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.yaml = QPlainTextEdit(readOnly=True)
        self.yaml.setVisible(False)
        self.yaml.setMinimumHeight(160)

        grid = QGridLayout(self)
        grid.addWidget(self.title, 0, 0, 1, 2)
        grid.addWidget(self.yaml_button, 0, 2, Qt.AlignmentFlag.AlignRight)
        for row, (name, widget) in enumerate(
            (("When", self.when), ("Only if", self.only_if), ("Do", self.do)), start=1
        ):
            head = QLabel(name)
            head.setStyleSheet("color: palette(placeholder-text);")
            grid.addWidget(head, row, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(widget, row, 1, 1, 2)
        grid.addWidget(self.yaml, 4, 0, 1, 3)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(5, 1)
        self.show_message("Select a binding to see what it does.")

    def show_message(self, text: str):
        self.title.setText(text)
        for w in (self.when, self.only_if, self.do, self.yaml_button, self.yaml):
            w.setVisible(False)

    def show_binding(self, title: str, b: cfg.Binding):
        self.title.setText(title)
        self.when.setText(" · ".join(d.trigger_parts(b.data)))
        conds = d.conditions(b.data.get("conditions"))
        self.only_if.setText("<br>".join(conds) if conds else "Always")
        rows = "".join(
            f"<tr><td style='padding-right:12px'><i>{stage}</i></td><td>{' → '.join(steps)}</td></tr>"
            for stage, steps in d.stages(b.data.get("actions"))
        )
        self.do.setText(f"<table>{rows}</table>" if rows else "Nothing")
        self.yaml.setPlainText(d.to_yaml(b.data))
        for w in (self.when, self.only_if, self.do, self.yaml_button):
            w.setVisible(True)
        self.yaml.setVisible(self.yaml_button.isChecked())


class BindingsPage(QWidget):
    """A list of rows on top and the selected row's details below."""

    def __init__(self, headers: list[str]):
        super().__init__()
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(headers)
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.currentItemChanged.connect(self._selected)
        self.view = BindingView()
        scroll = QScrollArea(widgetResizable=True, frameShape=QFrame.Shape.NoFrame)
        scroll.setWidget(self.view)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.tree)
        split.addWidget(scroll)
        split.setSizes([260, 300])
        QVBoxLayout(self).addWidget(split)

    def add_row(self, columns: list[str], payload) -> QTreeWidgetItem:
        item = QTreeWidgetItem(columns)
        item.setData(0, ROLE, payload)
        self.tree.addTopLevelItem(item)
        return item

    def add_group(self, title: str):
        item = QTreeWidgetItem([title])
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        font = item.font(0)
        font.setBold(True)
        item.setFont(0, font)
        self.tree.addTopLevelItem(item)

    def finish(self, empty_text: str):
        for i in range(self.tree.columnCount()):
            self.tree.resizeColumnToContents(i)
        first = next(
            (self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())
             if self.tree.topLevelItem(i).flags() & Qt.ItemFlag.ItemIsSelectable),
            None,
        )
        if first:
            self.tree.setCurrentItem(first)
        else:
            self.view.show_message(empty_text)

    def _selected(self, item, _prev):
        if not item:
            return
        payload = item.data(0, ROLE)
        if isinstance(payload, tuple):  # (title, binding or None, fallback message, ...)
            title, binding, message = payload[:3]
            if binding:
                self.view.show_binding(title, binding)
            else:
                self.view.show_message(message)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowIcon(QIcon.fromTheme("input-mouse"))
        self.resize(1000, 720)
        self.settings = QSettings("Actuator", "Actuator")
        self.config: cfg.Config | None = None
        self.dirty = False
        self.running = True  # InputActions has no status query; assume running at start.
        self.current = ("global", None)
        self._issues = ""
        self._last_written: str | None = None

        self._build_toolbar()
        self.sidebar = QListWidget()
        self.sidebar.setMaximumWidth(200)
        self.sidebar.currentItemChanged.connect(self._sidebar_changed)
        self.sidebar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.sidebar.customContextMenuRequested.connect(self._sidebar_menu)
        self.pages = QStackedWidget()
        split = QSplitter()
        split.addWidget(self.sidebar)
        split.addWidget(self.pages)
        split.setStretchFactor(1, 1)
        self.setCentralWidget(split)

        self.watcher = QFileSystemWatcher()
        self.watcher.fileChanged.connect(lambda _p: QTimer.singleShot(300, self._file_changed))
        self.load()

    # Toolbar ---------------------------------------------------------------

    def _build_toolbar(self):
        bar = QToolBar(movable=False)
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(bar)
        self.status = QLabel()
        self.status.setContentsMargins(8, 0, 12, 0)
        bar.addWidget(self.status)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        bar.addWidget(spacer)

        self.toggle_action = QAction(self)
        self.toggle_action.triggered.connect(self.toggle_running)
        bar.addAction(self.toggle_action)
        bar.addAction(QIcon.fromTheme("view-refresh"), "Reload", self.reload)
        self.save_action = bar.addAction(QIcon.fromTheme("document-save"), "Save", self.save)
        self.save_action.setShortcut("Ctrl+S")

        backup = QMenu(self)
        backup.addAction(QIcon.fromTheme("document-export"), "Export YAML…", self.export_yaml)
        backup.addAction(QIcon.fromTheme("document-import"), "Restore from file…", self.restore_from_file)
        backup.addSeparator()
        self.recent = backup.addMenu(QIcon.fromTheme("document-open-recent"), "Recent versions")
        self.recent.aboutToShow.connect(self._fill_recent)
        bar.addWidget(self._menu_button("Backup", "folder-backup", backup))

        self.options = QMenu(self)
        self.options.aboutToShow.connect(self._fill_options)
        bar.addWidget(self._menu_button("Options", "configure", self.options))
        self._update_toggle()

    def _menu_button(self, text: str, icon: str, menu: QMenu) -> QToolButton:
        b = QToolButton(text=text, icon=QIcon.fromTheme(icon))
        b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        b.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        b.setMenu(menu)
        return b

    def _fill_options(self):
        self.options.clear()
        self.options.addSection("Show tabs")
        for title in cfg.DEVICES:
            a = self.options.addAction(title)
            a.setCheckable(True)
            a.setChecked(self.tab_visible(title))
            a.toggled.connect(lambda on, t=title: self.set_tab_visible(t, on))
        self.options.addSeparator()
        a = self.options.addAction("Reload InputActions after save")
        a.setCheckable(True)
        a.setChecked(self.reload_after_save())
        a.toggled.connect(lambda on: self.settings.setValue("reload_after_save", on))

    def _fill_recent(self):
        self.recent.clear()
        orig = writer.original_path(cfg.CONFIG_PATH)
        snaps = writer.snapshots()[:15]
        for snap in snaps:
            stamp = datetime.strptime(snap.stem.removeprefix("config-"), "%Y%m%d-%H%M%S")
            self.recent.addAction(f"{stamp:%b %d, %H:%M:%S}", lambda s=snap: self.restore(s))
        if orig.exists():
            if snaps:
                self.recent.addSeparator()
            self.recent.addAction("Original (before Actuator)", lambda: self.restore(orig))
        if self.recent.isEmpty():
            self.recent.addAction("No saved versions yet").setEnabled(False)

    def tab_visible(self, title: str) -> bool:
        return self.settings.value(f"tabs/{title}", True, type=bool)

    def set_tab_visible(self, title: str, on: bool):
        self.settings.setValue(f"tabs/{title}", on)
        self._show_scope(*self.current)

    def reload_after_save(self) -> bool:
        return self.settings.value("reload_after_save", True, type=bool)

    def _update_toggle(self):
        self.toggle_action.setText("Stop" if self.running else "Start")
        self.toggle_action.setIcon(QIcon.fromTheme(
            "media-playback-pause" if self.running else "media-playback-start"))
        self._update_status()

    def _update_status(self):
        state = ("<span style='color:#2e9e5b'>● Running</span>" if self.running
                 else "<span style='color:#c58a00'>● Stopped</span>")
        tail = ("No issues" if not self._issues
                else f"<span style='color:#d24545'>Config issues (hover)</span>")
        self.status.setText(f"<b>InputActions</b> &nbsp; {state} &nbsp; · &nbsp; {tail}")
        self.status.setToolTip(self._issues)
        self.save_action.setText("Save •" if self.dirty else "Save")
        self.setWindowTitle(("* " if self.dirty else "") + "Actuator")

    def _check_issues(self):
        ok, out = run_ctl("config", "issues")
        self._issues = out if ok else f"Couldn't check issues: {out}"
        self._update_status()

    def mark_dirty(self):
        self.dirty = True
        self._update_status()

    def toggle_running(self):
        ok, out = run_ctl("suspend" if self.running else "resume")
        if not ok:
            QMessageBox.warning(self, "Actuator", f"InputActions didn't respond.\n\n{out}")
            return
        self.running = not self.running
        self._update_toggle()

    # Disk ------------------------------------------------------------------

    def _ask_discard(self, what: str) -> bool:
        if not self.dirty:
            return True
        answer = QMessageBox.question(
            self, "Actuator", f"You have unsaved changes. Discard them and {what}?",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Discard

    def reload(self):
        if not self._ask_discard("reload from disk"):
            return
        ok, out = run_ctl("config", "reload")
        if not ok:
            QMessageBox.warning(self, "Actuator", f"InputActions reload failed.\n\n{out}")
        self.load()

    def _file_changed(self):
        self._watch()
        try:
            text = cfg.CONFIG_PATH.read_text()
        except OSError:
            return
        if text == self._last_written:
            return  # our own save
        if self.dirty:
            answer = QMessageBox.question(
                self, "Actuator",
                "config.yaml was changed outside Actuator.\n\n"
                "Load the new file (your unsaved changes are lost), or keep editing yours?",
                QMessageBox.StandardButton.Open | QMessageBox.StandardButton.Ignore,
            )
            if answer != QMessageBox.StandardButton.Open:
                return
        self.load()

    def save(self):
        path = cfg.CONFIG_PATH
        first = path.exists() and not writer.is_managed(path) and not writer.original_path(path).exists()
        if first:
            answer = QMessageBox.question(
                self, "Reorganize your config?",
                "This first save rewrites config.yaml in Actuator's layout. Hand-written "
                f"comments won't be kept.\n\nYour current file is kept as {writer.original_path(path).name}.",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Save:
                return
        try:
            self._last_written = writer.render(self.config)
            previous = writer.save(self.config)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Actuator", f"Couldn't save. Your config wasn't changed.\n\n{e}")
            return
        self.dirty = False
        self._after_write(previous)

    def restore(self, source: Path):
        if not self._ask_discard("restore"):
            return
        try:
            text = Path(source).read_text()
            new = YAML(typ="rt").load(text)
            if not isinstance(new, dict):
                raise ValueError("That file isn't an InputActions config.")
            before = app_counts(self.config)
            after = app_counts(cfg.load(Path(source)))
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Actuator", f"Couldn't read {source}\n\n{e}")
            return
        lines = [
            f"{name}: {before.get(name, 0)} → {after.get(name, 0)}"
            if before.get(name, 0) != after.get(name, 0) else f"{name}: unchanged"
            for name in sorted(set(before) | set(after))
        ]
        answer = QMessageBox.question(
            self, "Restore this version?",
            "Bindings per app:\n\n" + "\n".join(lines) + "\n\nYour current config is saved to Recent versions first.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._last_written = text
        previous = writer.restore(cfg.CONFIG_PATH, Path(source))
        self.dirty = False
        self._after_write(previous)

    def _after_write(self, previous: Path | None):
        if self.reload_after_save():
            run_ctl("config", "reload")
        self.load()
        if self._issues and previous:
            answer = QMessageBox.warning(
                self, "Actuator",
                f"InputActions reported problems with the new config:\n\n{self._issues}\n\nUndo?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                self._last_written = previous.read_text()
                writer.restore(cfg.CONFIG_PATH, previous)
                run_ctl("config", "reload")
                self.load()

    def restore_from_file(self):
        source, _ = QFileDialog.getOpenFileName(self, "Restore from file", str(Path.home()), "YAML (*.yaml *.yml)")
        if source:
            self.restore(Path(source))

    def export_yaml(self):
        default = Path.home() / f"inputactions-{date.today().isoformat()}.yaml"
        target, _ = QFileDialog.getSaveFileName(self, "Export YAML", str(default), "YAML (*.yaml *.yml)")
        if target:
            shutil.copy2(cfg.CONFIG_PATH, target)

    def closeEvent(self, event):
        if self._ask_discard("quit"):
            event.accept()
        else:
            event.ignore()

    # Loading ---------------------------------------------------------------

    def _watch(self):
        path = str(cfg.CONFIG_PATH)
        if path not in self.watcher.files() and cfg.CONFIG_PATH.exists():
            self.watcher.addPath(path)  # re-add: replacing the file drops the watch

    def load(self):
        self._watch()
        try:
            self.config = cfg.load()
        except Exception as e:  # noqa: BLE001 - any parse error should be shown, not crash
            QMessageBox.critical(self, "Actuator", f"Couldn't read {cfg.CONFIG_PATH}\n\n{e}")
            return
        self.dirty = False
        self._check_issues()
        self._fill_sidebar()

    def _fill_sidebar(self):
        keep = self.current
        self.sidebar.blockSignals(True)
        self.sidebar.clear()

        def header(text):
            item = QListWidgetItem(text)
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.sidebar.addItem(item)

        def entry(text, icon, payload):
            item = QListWidgetItem(QIcon.fromTheme(icon, QIcon.fromTheme("application-x-executable")), text)
            item.setData(ROLE, payload)
            self.sidebar.addItem(item)
            return item

        header("Applies to")
        items = {("global", None): entry("Global", "globe", ("global", None))}
        for app in self.config.apps:
            items[("app", app)] = entry(app.capitalize(), app, ("app", app))
        add = QListWidgetItem(QIcon.fromTheme("list-add"), "Add app…")
        add.setData(ROLE, ADD_APP)
        self.sidebar.addItem(add)
        header("Advanced")
        items[("custom", None)] = entry("Custom rules", "view-filter", ("custom", None))
        items[("devices", None)] = entry("Device settings", "configure", ("devices", None))
        self.sidebar.blockSignals(False)
        self.sidebar.setCurrentItem(items.get(keep, items[("global", None)]))

    def _sidebar_changed(self, item, prev):
        if not item:
            return
        payload = item.data(ROLE)
        if payload == ADD_APP:
            self.sidebar.blockSignals(True)
            self.sidebar.setCurrentItem(prev)
            self.sidebar.blockSignals(False)
            QTimer.singleShot(0, self.add_app)
        elif payload:
            self._show_scope(*payload)

    def _sidebar_menu(self, pos):
        item = self.sidebar.itemAt(pos)
        payload = item.data(ROLE) if item else None
        if not (isinstance(payload, tuple) and payload[0] == "app"):
            return
        menu = QMenu(self)
        menu.addAction(QIcon.fromTheme("list-remove"), f"Remove {payload[1].capitalize()}…",
                       lambda: self.remove_app(payload[1]))
        menu.exec(self.sidebar.mapToGlobal(pos))

    def add_app(self):
        dialog = AddAppDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        app = cfg.app_key(dialog.value())
        if app not in self.config.apps:
            self.config.add_app(app)
            self.mark_dirty()
        self.current = ("app", app)
        self._fill_sidebar()

    def remove_app(self, app: str):
        n = len(self.config.bindings_for("app", app))
        detail = f"This deletes its {n} binding{'s' if n != 1 else ''}." if n else "It has no bindings."
        answer = QMessageBox.question(
            self, "Actuator", f"Remove {app.capitalize()}? {detail}\n\nNothing is written until you save.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.config.remove_app(app)
        if self.current == ("app", app):
            self.current = ("global", None)
        self.mark_dirty()
        self._fill_sidebar()

    # Pages -----------------------------------------------------------------

    def _set_page(self, widget: QWidget):
        while self.pages.count():
            old = self.pages.widget(0)
            self.pages.removeWidget(old)
            old.deleteLater()
        self.pages.addWidget(widget)

    def _show_scope(self, scope: str, app: str | None):
        self.current = (scope, app)
        if scope == "devices":
            self._set_page(self._device_settings_page())
            return
        if scope == "custom":
            self._set_page(self._custom_page())
            return
        tabs = QTabWidget()
        for title, section in cfg.DEVICES.items():
            if self.tab_visible(title):
                page = self._mouse_page(scope, app) if section == "mouse" else \
                    self._gesture_page(scope, app, section)
                tabs.addTab(page, title)
        self._set_page(tabs)

    def _mouse_page(self, scope, app) -> QWidget:
        where = scope_title(scope, app)
        page = BindingsPage(["Button", "Input", f"In {where}"])
        bindings = self.config.bindings_for(scope, app)
        page.add_group("Buttons")
        page.tree.headerItem().setToolTip(0, "Double-click a name to rename it")
        page.tree.headerItem().setText(0, "Button (double-click to rename)")
        for btn in self.config.buttons:
            b = next((x for x in bindings if x.button == btn.src), None)
            name = button_name(btn)
            item = page.add_row(
                [name, d.button_label(btn.src), d.summary(b) if b else "Default"],
                (f"{name} in {where}", b, f"{name} does its normal thing in {where}.", btn.src),
            )
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        gestures = [b for b in bindings if b.section == "mouse" and not b.button]
        if gestures:
            page.add_group("Gestures")
            for b in gestures:
                title = d.gesture_title(b.data)
                page.add_row([title, "", d.summary(b)], (f"{title} in {where}", b, ""))
        page.finish("No mouse buttons found in your config yet.")
        # Only the name column is editable.
        page.tree.itemDoubleClicked.connect(
            lambda item, col: page.tree.editItem(item, 0) if item.flags() & Qt.ItemFlag.ItemIsEditable else None)
        page.tree.setEditTriggers(QTreeWidget.EditTrigger.NoEditTriggers)
        page.tree.itemChanged.connect(lambda item, col: self._renamed(page, item) if col == 0 else None)
        return page

    def _renamed(self, page: BindingsPage, item: QTreeWidgetItem):
        payload = item.data(0, ROLE)
        if not (isinstance(payload, tuple) and len(payload) == 4):
            return
        src = payload[3]
        btn = next(b for b in self.config.buttons if b.src == src)
        new = item.text(0).strip()
        if not new:  # empty resets to the raw input
            new = d.button_label(src)
        if new == button_name(btn):
            return
        self.config.rename_button(src, new if new != d.button_label(src) else src)
        self.mark_dirty()
        page.tree.blockSignals(True)
        item.setText(0, new)
        page.tree.blockSignals(False)
        page._selected(page.tree.currentItem(), None)

    def _gesture_page(self, scope, app, section) -> QWidget:
        where = scope_title(scope, app)
        page = BindingsPage(["Gesture", "Does"])
        for b in self.config.bindings_for(scope, app, section):
            if b.button:  # mouse-emitted shortcuts live on the Mouse tab
                continue
            title = d.gesture_title(b.data)
            page.add_row([title, d.summary(b)], (f"{title} in {where}", b, ""))
        page.finish(f"No {section} bindings for {where} yet.")
        return page

    def _custom_page(self) -> QWidget:
        page = BindingsPage(["Rule", "Device", "Does"])
        for b in self.config.bindings_for("custom"):
            title = d.gesture_title(b.data)
            page.add_row([b.id, b.section.capitalize(), d.summary(b)], (f"{b.id} — {title}", b, ""))
        page.finish("No custom rules. These are bindings with conditions other than a single app.")
        return page

    def _device_settings_page(self) -> QWidget:
        tree = QTreeWidget()
        tree.setHeaderLabels(["Setting", "Value"])
        for rule in self.config.device_rules:
            conds = d.conditions(rule.get("conditions")) or ["All devices"]
            top = QTreeWidgetItem([" and ".join(c.lstrip("$") for c in conds)])
            tree.addTopLevelItem(top)

            def add(parent, node, prefix=""):
                for k, v in node.items():
                    if k == "conditions":
                        continue
                    if isinstance(v, dict):
                        add(parent, v, f"{prefix}{k} ")
                    else:
                        parent.addChild(QTreeWidgetItem([f"{prefix}{k}".replace("_", " "), str(v)]))

            add(top, rule)
            top.setExpanded(True)
        if not self.config.device_rules:
            tree.addTopLevelItem(QTreeWidgetItem(["No device rules. Everything uses defaults."]))
        tree.resizeColumnToContents(0)
        return tree
