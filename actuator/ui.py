"""Main window. Read-only for now: shows the config, never writes it."""

import shutil
import subprocess
from datetime import date
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QGridLayout, QLabel, QListWidget, QListWidgetItem,
    QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
    QSplitter, QStackedWidget, QTabWidget, QToolBar, QToolButton, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from . import config as cfg
from . import describe as d

ROLE = Qt.ItemDataRole.UserRole


def run_ctl(*args: str) -> tuple[bool, str]:
    try:
        p = subprocess.run(["inputactions", *args], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    return p.returncode == 0, (p.stdout + p.stderr).strip()


def button_name(b: cfg.Button) -> str:
    return b.name if b.name != b.src else d.button_label(b.src)


def scope_title(scope: str, app: str | None) -> str:
    return app.capitalize() if scope == "app" else scope.capitalize()


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
        if isinstance(payload, tuple):  # (title, binding or None, fallback message)
            title, binding, message = payload
            if binding:
                self.view.show_binding(title, binding)
            else:
                self.view.show_message(message)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Actuator")
        self.setWindowIcon(QIcon.fromTheme("input-mouse"))
        self.resize(1000, 720)
        self.settings = QSettings("Actuator", "Actuator")
        self.config: cfg.Config | None = None
        self.running = True  # InputActions has no status query; assume running at start.
        self.current = ("global", None)

        self._build_toolbar()
        self.sidebar = QListWidget()
        self.sidebar.setMaximumWidth(200)
        self.sidebar.currentItemChanged.connect(self._sidebar_changed)
        self.pages = QStackedWidget()
        split = QSplitter()
        split.addWidget(self.sidebar)
        split.addWidget(self.pages)
        split.setStretchFactor(1, 1)
        self.setCentralWidget(split)

        self.watcher = QFileSystemWatcher()
        self.watcher.fileChanged.connect(lambda _p: QTimer.singleShot(300, self.load))
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
        spacer.setSizePolicy(spacer.sizePolicy().horizontalPolicy().Expanding,
                             spacer.sizePolicy().verticalPolicy().Preferred)
        bar.addWidget(spacer)

        self.toggle_action = QAction(self)
        self.toggle_action.triggered.connect(self.toggle_running)
        bar.addAction(self.toggle_action)
        bar.addAction(QIcon.fromTheme("view-refresh"), "Reload", self.reload)
        bar.addAction(QIcon.fromTheme("document-save"), "Save", self.save)

        backup = QMenu(self)
        backup.addAction(QIcon.fromTheme("document-export"), "Export YAML…", self.export_yaml)
        backup.addAction(QIcon.fromTheme("document-import"), "Restore from file…", self.not_yet)
        backup.addSeparator()
        backup.addAction("Recent versions…", self.not_yet)
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

    def tab_visible(self, title: str) -> bool:
        return self.settings.value(f"tabs/{title}", True, type=bool)

    def set_tab_visible(self, title: str, on: bool):
        self.settings.setValue(f"tabs/{title}", on)
        self._show_scope(*self.current)

    def _update_toggle(self):
        self.toggle_action.setText("Stop" if self.running else "Start")
        self.toggle_action.setIcon(QIcon.fromTheme(
            "media-playback-pause" if self.running else "media-playback-start"))
        self._update_status()

    def _update_status(self, issues: str | None = None):
        if issues is not None:
            self._issues = issues
        state = ("<span style='color:#2e9e5b'>● Running</span>" if self.running
                 else "<span style='color:#c58a00'>● Stopped</span>")
        issues = getattr(self, "_issues", "")
        tail = ("No issues" if not issues
                else f"<span style='color:#d24545'>{len(issues.splitlines())} issue line(s)</span>")
        self.status.setText(f"<b>InputActions</b> &nbsp; {state} &nbsp; · &nbsp; {tail}"
                            " &nbsp; · &nbsp; <i>read-only preview</i>")
        self.status.setToolTip(issues or "")

    def toggle_running(self):
        ok, out = run_ctl("suspend" if self.running else "resume")
        if not ok:
            QMessageBox.warning(self, "Actuator", f"InputActions didn't respond.\n\n{out}")
            return
        self.running = not self.running
        self._update_toggle()

    def reload(self):
        ok, out = run_ctl("config", "reload")
        if not ok:
            QMessageBox.warning(self, "Actuator", f"Reload failed.\n\n{out}")
        self.load()

    def save(self):
        QMessageBox.information(self, "Actuator",
                                "This version is read-only. Saving comes next.")

    def not_yet(self):
        QMessageBox.information(self, "Actuator", "Not in this version yet.")

    def export_yaml(self):
        default = Path.home() / f"inputactions-{date.today().isoformat()}.yaml"
        target, _ = QFileDialog.getSaveFileName(self, "Export YAML", str(default), "YAML (*.yaml *.yml)")
        if target:
            shutil.copy2(cfg.CONFIG_PATH, target)

    # Loading ---------------------------------------------------------------

    def load(self):
        path = str(cfg.CONFIG_PATH)
        if path not in self.watcher.files() and cfg.CONFIG_PATH.exists():
            self.watcher.addPath(path)  # re-add: editors that replace the file drop the watch
        try:
            self.config = cfg.load()
        except Exception as e:  # noqa: BLE001 - any parse error should be shown, not crash
            QMessageBox.critical(self, "Actuator", f"Couldn't read {path}\n\n{e}")
            return
        ok, out = run_ctl("config", "issues")
        self._update_status(out if ok else f"Couldn't check issues: {out}")
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
        header("Advanced")
        items[("custom", None)] = entry("Custom rules", "view-filter", ("custom", None))
        items[("devices", None)] = entry("Device settings", "configure", ("devices", None))
        self.sidebar.blockSignals(False)
        self.sidebar.setCurrentItem(items.get(keep, items[("global", None)]))

    def _sidebar_changed(self, item, _prev):
        if item and item.data(ROLE):
            self._show_scope(*item.data(ROLE))

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
        for btn in self.config.buttons:
            b = next((x for x in bindings if x.button == btn.src), None)
            name = button_name(btn)
            page.add_row(
                [name, d.button_label(btn.src), d.summary(b) if b else "Default"],
                (f"{name} in {where}", b, f"{name} does its normal thing in {where}."),
            )
        gestures = [b for b in bindings if b.section == "mouse" and not b.button]
        if gestures:
            page.add_group("Gestures")
            for b in gestures:
                title = d.gesture_title(b.data)
                page.add_row([title, "", d.summary(b)], (f"{title} in {where}", b, ""))
        page.finish("No mouse buttons found in your config yet.")
        return page

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
