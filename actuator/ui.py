"""Main window."""

import re
import shutil
import subprocess
from datetime import date, datetime
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QDialog, QFormLayout, QSpinBox, QDialogButtonBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
    QCheckBox, QComboBox, QGroupBox, QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu,
    QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy,
    QSplitter, QStackedWidget, QTabWidget, QToolBar, QToolButton, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)
from ruamel.yaml import YAML

from . import config as cfg
from . import copying
from . import describe as d
from . import keys
from . import writer
from . import devices as devices_mod
from .editor import GESTURE_TYPES, BindingEditor, new_button_binding, new_gesture_binding

ROLE = Qt.ItemDataRole.UserRole


def run_ctl(*args: str) -> tuple[bool, str]:
    try:
        p = subprocess.run(["inputactions", *args], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    return p.returncode == 0, (p.stdout + p.stderr).strip()


def run(*cmd: str) -> tuple[bool, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    return p.returncode == 0, (p.stdout + p.stderr).strip()


# InputActions on KDE is the KWin effect "kwin_gestures"; KWin loads it at login when enabled.
PLUGIN_KEY = ("--file", "kwinrc", "--group", "Plugins", "--key", "kwin_gesturesEnabled")


EFFECT = "kwin_gestures"


def login_setting() -> str:
    """'true', 'false', or '' if never set."""
    ok, out = run("kreadconfig6", *PLUGIN_KEY)
    return out.strip() if ok else ""


def starts_at_login() -> bool:
    return login_setting() == "true"


def set_starts_at_login(on: bool) -> tuple[bool, str]:
    # Only the config: KWin reads it at login. Not reconfiguring leaves this session as it is.
    return run("kwriteconfig6", *PLUGIN_KEY, "true" if on else "false")


def effect_loaded() -> bool:
    ok, out = run("qdbus6", "org.kde.KWin", "/Effects", "isEffectLoaded", EFFECT)
    return ok and out.strip() == "true"


def load_effect() -> tuple[bool, str]:
    """Start InputActions for this session, whether or not it starts at login."""
    ok, out = run("qdbus6", "org.kde.KWin", "/Effects", "loadEffect", EFFECT)
    return ok and out.strip() == "true", out


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
    counts = {"Global": len(c.bindings_for("global")) + len(c.bindings_for("custom"))}
    for app in c.apps:
        counts[app.capitalize()] = len(c.bindings_for("app", app))
    return counts


class AddAppDialog(QDialog):
    """Type a window class, or pick it from a window."""

    def __init__(self, parent, folders: list[str]):
        super().__init__(parent)
        self.setWindowTitle("Add app")
        self.folder_box = QComboBox()
        self.folder_box.addItem("No folder", None)
        for f in folders:
            self.folder_box.addItem(f, f)
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
        if folders:
            frow = QHBoxLayout()
            frow.addWidget(QLabel("Folder"))
            frow.addWidget(self.folder_box, 1)
            layout.addLayout(frow)
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

    def folder(self) -> str | None:
        return self.folder_box.currentData()


class SidebarTree(QTreeWidget):
    """Sidebar that lets apps be dragged onto folders. Emits (app, folder or None)."""

    app_dropped = Signal(str, object)

    def __init__(self):
        super().__init__()
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QTreeWidget.DragDropMode.InternalMove)

    def _target_folder(self, pos):
        """Folder under the cursor, or None to take the app out of its folder."""
        item = self.itemAt(pos)
        while item is not None:
            p = item.data(0, ROLE)
            if isinstance(p, tuple) and p[0] == "folder":
                return p[1]
            item = item.parent()
        return None

    def dragMoveEvent(self, event):
        dragged = self.currentItem()
        p = dragged.data(0, ROLE) if dragged else None
        if isinstance(p, tuple) and p[0] == "app":
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        # Don't let Qt move items itself; the sidebar is rebuilt from the config.
        dragged = self.currentItem()
        p = dragged.data(0, ROLE) if dragged else None
        event.setDropAction(Qt.DropAction.IgnoreAction)
        event.accept()
        if isinstance(p, tuple) and p[0] == "app":
            self.app_dropped.emit(p[1], self._target_folder(event.position().toPoint()))


class OptionsDialog(QDialog):
    def __init__(self, parent, tabs: dict[str, bool], autostart: bool, keep: int):
        super().__init__(parent)
        self.setWindowTitle("Options")
        self.autostart = QCheckBox("Start InputActions when I log in")
        self.autostart.setChecked(autostart)
        info = QToolButton(icon=QIcon.fromTheme("help-about-symbolic", QIcon.fromTheme("dialog-information")))
        info.setAutoRaise(True)
        info.setToolTip("How autostart works")
        info.clicked.connect(self._explain_autostart)
        ia = QGroupBox("Autostart")
        il = QHBoxLayout(ia)
        il.addWidget(self.autostart)
        il.addStretch(1)
        il.addWidget(info)
        self.keep = QSpinBox(minimum=1, maximum=200)
        self.keep.setValue(keep)
        backups = QGroupBox("Backups")
        bl = QFormLayout(backups)
        bl.addRow("Recent saves to keep", self.keep)
        self.boxes = {}
        group = QGroupBox("Show these tabs")
        gl = QVBoxLayout(group)
        for title, on in tabs.items():
            box = QCheckBox(title)
            box.setChecked(on)
            box.toggled.connect(self._keep_one)
            gl.addWidget(box)
            self.boxes[title] = box
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        devices = QPushButton(QIcon.fromTheme("input-mouse"), "Device settings…")
        devices.clicked.connect(parent.open_device_settings)
        dev_note = QLabel("Swipe angles, thresholds and timeouts. Most people never need these.")
        dev_note.setWordWrap(True)
        dev_note.setStyleSheet("color: palette(placeholder-text);")
        adv = QGroupBox("Advanced")
        al = QVBoxLayout(adv)
        al.addWidget(devices, 0, Qt.AlignmentFlag.AlignLeft)
        al.addWidget(dev_note)
        layout = QVBoxLayout(self)
        layout.addWidget(ia)
        layout.addWidget(backups)
        layout.addWidget(group)
        layout.addWidget(adv)
        layout.addStretch(1)
        layout.addWidget(buttons)
        self.resize(320, 0)

    def _explain_autostart(self):
        box = QMessageBox(self)
        box.setWindowTitle("How autostart works")
        box.setText(
            "InputActions runs inside KWin as a desktop effect, so there's no separate program to start.\n\n"
            "This checkbox is the same as its switch in System Settings → Desktop Effects. When it's on, "
            "KWin loads InputActions every time you log in.\n\n"
            "Turning it off only affects your next login. InputActions keeps running until then, and the "
            "Start button in the toolbar can load it for the current session any time.\n\n"
            "Clicking Apply in Desktop Effects while autostart is off can stop it for the current session."
        )
        open_effects = box.addButton("Open Desktop Effects", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        if box.clickedButton() is open_effects:
            subprocess.Popen(["systemsettings", "kcm_kwin_effects"], start_new_session=True)

    def _keep_one(self):
        # At least one tab has to stay visible.
        on = [b for b in self.boxes.values() if b.isChecked()]
        for b in self.boxes.values():
            b.setEnabled(not (len(on) == 1 and b in on))

    def tabs(self) -> dict[str, bool]:
        return {t: b.isChecked() for t, b in self.boxes.items()}


class CaptureDialog(QDialog):
    """Press a mouse button, or the key combo a mouse button sends, to add it."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Add button")
        self.src: str | None = None
        self.kind = "mouse"
        self.prompt = QLabel("Press the button on your mouse here.\n\n"
                             "Buttons that send a key combo work too.")
        self.prompt.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.prompt.setMinimumSize(380, 140)
        self.prompt.setFrameShape(QFrame.Shape.StyledPanel)
        self.found = QLabel()
        self.found.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.manual = QLineEdit(placeholderText="Or type it: forward, extra1, Shift+Meta+[")
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        self.error = QLabel()
        self.error.setStyleSheet("color: #d24545;")
        layout = QVBoxLayout(self)
        for w in (self.prompt, self.found, self.manual, self.error, self.buttons):
            layout.addWidget(w)
        self.prompt.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.prompt.setFocus()
        self.prompt.installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj is self.prompt:
            if event.type() == event.Type.MouseButtonPress:
                name = keys.MOUSE_BUTTONS.get(event.button())
                if name in ("left", "right"):
                    self.found.setText(f"That's the {name} button. Binding it can make your mouse hard to use.")
                self._set(name or f"button{int(event.button())}", "mouse")
                return True
            if event.type() == event.Type.KeyPress:
                combo = keys.from_key_event(event)
                if combo:
                    self._set(combo, "shortcut")
                return True
        return super().eventFilter(obj, event)

    def _set(self, src: str, kind: str):
        self.src, self.kind = src, kind
        self.manual.clear()
        if not self.found.text().startswith("That's"):
            self.found.clear()
        self.prompt.setText(f"Got it: {d.button_label(src)}\n\nPress another to change it.")

    def _accept(self):
        text = self.manual.text().strip()
        if text:
            try:
                if "+" in text:
                    self.src, self.kind = keys.parse_combo(text), "shortcut"
                else:
                    self.src, self.kind = keys.to_name(text), "mouse"
            except ValueError as e:
                self.error.setText(str(e))
                return
        if not self.src:
            self.error.setText("Press a button first, or type it.")
            return
        self.accept()


class BindingsPage(QWidget):
    """A list of rows, an add bar under it, and the selected row's editor below."""

    def __init__(self, headers: list[str], config: cfg.Config):
        super().__init__()
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(headers)
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.currentItemChanged.connect(self._selected)
        self.add_bar = QHBoxLayout()
        self.add_bar.addStretch(1)
        top = QWidget()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.addWidget(self.tree)
        tl.addLayout(self.add_bar)
        self.editor = BindingEditor(config)
        scroll = QScrollArea(widgetResizable=True, frameShape=QFrame.Shape.NoFrame)
        scroll.setWidget(self.editor)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(top)
        split.addWidget(scroll)
        split.setSizes([240, 420])
        QVBoxLayout(self).addWidget(split)

    def add_button(self, widget: QWidget):
        self.add_bar.insertWidget(self.add_bar.count() - 1, widget)

    def add_row(self, columns: list[str], payload: dict) -> QTreeWidgetItem:
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

    def rows(self):
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.flags() & Qt.ItemFlag.ItemIsSelectable:
                yield item

    def finish(self, empty_text: str, select=None):
        header = self.tree.header()
        # Every column but the last keeps fitting its text as rows change; the last fills the rest.
        for i in range(self.tree.columnCount() - 1):
            header.setSectionResizeMode(i, header.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        rows = list(self.rows())
        target = next((r for r in rows if select and select(r.data(0, ROLE))), rows[0] if rows else None)
        if target:
            self.tree.setCurrentItem(target)
        else:
            self.editor.show_message(empty_text)

    def _selected(self, item, _prev):
        if not item:
            return
        p = item.data(0, ROLE)
        if p.get("binding"):
            self.editor.show_binding(p["title"], p["binding"])
        else:
            self.editor.show_message(p["message"], p.get("bind"))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowIcon(QIcon.fromTheme("input-mouse"))
        self.resize(1000, 720)
        self.settings = QSettings("Actuator", "Actuator")
        writer.SNAPSHOT_KEEP = self.settings.value("snapshots/keep", 10, type=int)
        self.config: cfg.Config | None = None
        self.dirty = False
        if login_setting() == "":
            set_starts_at_login(True)  # Actuator's default: start with Plasma
        self.loaded = effect_loaded()  # real state, from KWin
        self.suspended = False  # InputActions can't report this; assume not suspended
        last = self.settings.value("last/page", "global|", type=str).split("|", 1)
        self.current = (last[0], last[1] or None) if last[0] in ("global", "app") else ("global", None)
        self._issues = ""
        self._last_written: str | None = None
        self._clipboard: list[tuple[str, dict]] = []

        self._build_toolbar()
        self.sidebar = SidebarTree()
        self.sidebar.app_dropped.connect(self._app_dropped)
        self.sidebar.setHeaderHidden(True)
        self.sidebar.setRootIsDecorated(False)
        self.sidebar.setIndentation(14)
        self.sidebar.itemClicked.connect(self._sidebar_clicked)
        self.sidebar.itemExpanded.connect(lambda i: self._folder_toggled(i, True))
        self.sidebar.itemCollapsed.connect(lambda i: self._folder_toggled(i, False))
        self.sidebar.currentItemChanged.connect(self._sidebar_changed)
        self.sidebar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.sidebar.customContextMenuRequested.connect(self._sidebar_menu)
        self.pages = QStackedWidget()
        side = QWidget()
        side.setMaximumWidth(240)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.addWidget(self.sidebar)
        add_row = QHBoxLayout()
        add_app = QPushButton(QIcon.fromTheme("list-add"), "Add app")
        add_app.clicked.connect(self.add_app)
        new_folder = QPushButton(QIcon.fromTheme("folder-new"), "New folder")
        new_folder.clicked.connect(lambda: self.new_folder())
        add_row.addWidget(add_app)
        add_row.addWidget(new_folder)
        sl.addLayout(add_row)
        split = QSplitter()
        split.addWidget(side)
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

        bar.addAction(QIcon.fromTheme("configure"), "Options", self.show_options)
        self._update_toggle()

    def _menu_button(self, text: str, icon: str, menu: QMenu) -> QToolButton:
        b = QToolButton(text=text, icon=QIcon.fromTheme(icon))
        b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        b.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        b.setMenu(menu)
        return b

    def show_options(self):
        autostart = starts_at_login()
        dialog = OptionsDialog(self, {t: self.tab_visible(t) for t in cfg.DEVICES}, autostart,
                               writer.SNAPSHOT_KEEP)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if dialog.keep.value() != writer.SNAPSHOT_KEEP:
            dropping = len(writer.snapshots()[dialog.keep.value():])
            if dropping:
                answer = QMessageBox.question(
                    self, "Actuator", f"Keeping {dialog.keep.value()} deletes your {dropping} oldest "
                    f"saved version{'s' if dropping != 1 else ''}. Continue?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
            writer.SNAPSHOT_KEEP = dialog.keep.value()
            self.settings.setValue("snapshots/keep", writer.SNAPSHOT_KEEP)
            writer.prune()
        if dialog.autostart.isChecked() != autostart:
            ok, out = set_starts_at_login(dialog.autostart.isChecked())
            if not ok:
                QMessageBox.warning(self, "Actuator", f"Couldn't change the InputActions setting.\n\n{out}")
        for title, on in dialog.tabs().items():
            self.settings.setValue(f"tabs/{title}", on)
        self._fill_sidebar()

    def open_device_settings(self):
        if not devices_mod.confirm_open(self, self.settings):
            return
        dialog = devices_mod.DeviceSettingsDialog(self, self.config)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.dirty:
            self.apply_now(dialog.apply_to, take_snapshot=True)

    def _fill_recent(self):
        self.recent.clear()
        orig = writer.original_path(cfg.CONFIG_PATH)
        snaps = writer.snapshots()
        for snap in snaps:
            stamp = datetime.strptime(snap.stem.removeprefix("config-"), "%Y%m%d-%H%M%S")
            self.recent.addAction(f"Before save at {stamp:%b %d, %H:%M:%S}", lambda s=snap: self.restore(s))
        if orig.exists():
            if snaps:
                self.recent.addSeparator()
            self.recent.addAction("Original (before Actuator)", lambda: self.restore(orig))
        if self.recent.isEmpty():
            self.recent.addAction("No saved versions yet").setEnabled(False)

    def tab_visible(self, title: str) -> bool:
        return self.settings.value(f"tabs/{title}", True, type=bool)


    def _update_toggle(self):
        self.toggle_action.setText("Stop" if self.running else "Start")
        self.toggle_action.setIcon(QIcon.fromTheme(
            "media-playback-pause" if self.running else "media-playback-start"))
        self._update_status()

    def _update_status(self):
        state = ("<span style='color:#2e9e5b'>● Running</span>" if self.running
                 else "<span style='color:#c58a00'>● Stopped</span>" if self.loaded
                 else "<span style='color:#c58a00'>● Not loaded</span>")
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

    @property
    def running(self) -> bool:
        return self.loaded and not self.suspended

    def toggle_running(self):
        if self.running:
            ok, out = run_ctl("suspend")
            if ok:
                self.suspended = True
        else:
            ok, out = True, ""
            if not self.loaded:
                ok, out = load_effect()
                self.loaded = effect_loaded()
                if self.loaded:
                    self.suspended = False  # a fresh load isn't suspended
                    self.load()  # it can now report config issues
            if ok and self.suspended:
                ok, out = run_ctl("resume")
                if ok:
                    self.suspended = False
        if not ok:
            QMessageBox.warning(self, "Actuator", f"InputActions didn't respond.\n\n{out}")
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

    def _confirm_first_write(self) -> bool:
        path = cfg.CONFIG_PATH
        if not path.exists() or writer.is_managed(path) or writer.original_path(path).exists():
            return True
        answer = QMessageBox.question(
            self, "Reorganize your config?",
            "This first save rewrites config.yaml in Actuator's layout. Hand-written "
            f"comments won't be kept.\n\nYour current file is kept as {writer.original_path(path).name}.",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Save

    def apply_now(self, change, take_snapshot: bool = False) -> bool:
        """Apply an organizing change (apps, folders, button names) and write it at once.

        It's applied to the file as it is on disk, so unsaved binding edits stay unsaved.
        """
        if not self._confirm_first_write():
            return False
        try:
            disk = cfg.load()
            change(disk)
            disk.reindex()
            self._last_written = writer.render(disk)
            writer.save(disk, take_snapshot)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Actuator", f"Couldn't save that change. Your config wasn't changed.\n\n{e}")
            return False
        change(self.config)
        self.config.reindex()
        run_ctl("config", "reload")
        self._check_issues()
        return True

    def save(self):
        if not self._confirm_first_write():
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

    def _fill_sidebar(self, select=None):
        keep = self.current
        self._pending_select = select
        collapsed = set(self.settings.value("sidebar/collapsed", [], type=list) or [])
        self.sidebar.blockSignals(True)
        self.sidebar.clear()

        def header(text):
            item = QTreeWidgetItem([text])
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            font = item.font(0)
            font.setPointSizeF(font.pointSizeF() * 0.9)
            item.setFont(0, font)
            self.sidebar.addTopLevelItem(item)

        def entry(text, icon, payload, parent=None):
            item = QTreeWidgetItem([text])
            item.setIcon(0, QIcon.fromTheme(icon, QIcon.fromTheme("application-x-executable")))
            item.setData(0, ROLE, payload)
            flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            if isinstance(payload, tuple) and payload[0] == "app":
                flags |= Qt.ItemFlag.ItemIsDragEnabled
            item.setFlags(flags)
            (parent.addChild if parent else self.sidebar.addTopLevelItem)(item)
            return item

        items = {("global", None): entry("Global", "globe", ("global", None))}
        header("Apps")
        apps = self.config.apps
        for folder, members in self.config.folders.items():
            shown = [a for a in members if a in apps]
            f = entry(f"{folder}  ({len(shown)})", "folder", ("folder", folder))
            f.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsDropEnabled)  # not a page
            font = f.font(0)
            font.setBold(True)
            f.setFont(0, font)
            for app in shown:
                items[("app", app)] = entry(app.capitalize(), app, ("app", app), f)
            f.setExpanded(folder not in collapsed)
            f.setIcon(0, QIcon.fromTheme("folder-open" if f.isExpanded() else "folder"))
        for app in apps:
            if not self.config.folder_of(app):
                items[("app", app)] = entry(app.capitalize(), app, ("app", app))
        self.sidebar.blockSignals(False)
        self.sidebar.setCurrentItem(items.get(keep, items[("global", None)]))

    def _folder_toggled(self, item, expanded: bool):
        p = item.data(0, ROLE)
        if not (isinstance(p, tuple) and p[0] == "folder"):
            return
        item.setIcon(0, QIcon.fromTheme("folder-open" if expanded else "folder"))
        collapsed = set(self.settings.value("sidebar/collapsed", [], type=list) or [])
        (collapsed.discard if expanded else collapsed.add)(p[1])
        self.settings.setValue("sidebar/collapsed", sorted(collapsed))

    def _sidebar_changed(self, item, prev):
        if not item:
            return
        select, self._pending_select = getattr(self, "_pending_select", None), None
        payload = item.data(0, ROLE)
        if isinstance(payload, tuple) and payload[0] != "folder":
            self._show_scope(*payload, select=select)

    def _app_dropped(self, app: str, folder):
        if self.config.folder_of(app) != folder:
            self.move_app(app, folder)

    def _sidebar_clicked(self, item, _col):
        p = item.data(0, ROLE)
        if isinstance(p, tuple) and p[0] == "folder" and QApplication.mouseButtons() != Qt.MouseButton.RightButton:
            item.setExpanded(not item.isExpanded())

    def _sidebar_menu(self, pos):
        item = self.sidebar.itemAt(pos)
        payload = item.data(0, ROLE) if item else None
        menu = QMenu(self)
        if isinstance(payload, tuple) and payload[0] == "app":
            app = payload[1]
            move = menu.addMenu(QIcon.fromTheme("folder"), "Move to folder")
            current = self.config.folder_of(app)
            for folder in self.config.folders:
                a = move.addAction(folder, lambda f=folder: self.move_app(app, f))
                a.setCheckable(True)
                a.setChecked(folder == current)
            if self.config.folders:
                move.addSeparator()
            move.addAction(QIcon.fromTheme("folder-new"), "New folder…", lambda: self.new_folder(app))
            if current:
                menu.addAction(f"Take out of {current}", lambda: self.move_app(app, None))
            self._add_copy_all(menu, ("app", app))
            menu.addSeparator()
            menu.addAction(QIcon.fromTheme("list-remove"), f"Remove {app.capitalize()}…",
                           lambda: self.remove_app(app))
        elif payload == ("global", None):
            self._add_copy_all(menu, ("global", None))
        elif isinstance(payload, tuple) and payload[0] == "folder":
            folder = payload[1]
            menu.addAction(QIcon.fromTheme("edit-rename"), "Rename…", lambda: self.rename_folder(folder))
            menu.addAction(QIcon.fromTheme("edit-delete"), "Delete folder", lambda: self.delete_folder(folder))
        else:
            menu.addAction(QIcon.fromTheme("list-add"), "Add app…", self.add_app)
            menu.addAction(QIcon.fromTheme("folder-new"), "New folder…", lambda: self.new_folder())
        menu.exec(self.sidebar.viewport().mapToGlobal(pos))

    def _add_copy_all(self, menu: QMenu, source):
        items = [(b.section, d.plain(b.data)) for b in self.config.bindings_for(*source)]
        to = menu.addMenu(QIcon.fromTheme("edit-copy"), "Copy all bindings to")
        to.setEnabled(bool(items))
        for scope, app, label in self._targets(source):
            to.addAction(label, lambda s=scope, a=app: self._copy_into(items, s, a))
        to.addSeparator()
        to.addAction(QIcon.fromTheme("list-add"), "New app…", lambda: self._copy_to_new_app(items))

    def _ask_folder_name(self, title: str, text: str = "") -> str | None:
        name, ok = QInputDialog.getText(self, title, "Folder name:", text=text)
        name = name.strip().replace("=", "-") if ok else ""
        if not name:
            return None
        if name != text and name in self.config.folders:
            QMessageBox.information(self, "Actuator", f"There's already a folder called {name}.")
            return None
        return name

    def new_folder(self, app: str | None = None):
        name = self._ask_folder_name("New folder")
        if not name:
            return
        def change(c):
            c.folders.setdefault(name, [])
            if app:
                c.move_to_folder(app, name)
        if self.apply_now(change):
            self._fill_sidebar()

    def move_app(self, app: str, folder: str | None):
        if self.apply_now(lambda c: c.move_to_folder(app, folder)):
            self._fill_sidebar()

    def rename_folder(self, folder: str):
        name = self._ask_folder_name("Rename folder", folder)
        if name and name != folder:
            if self.apply_now(lambda c: c.rename_folder(folder, name)):
                self._fill_sidebar()

    def delete_folder(self, folder: str):
        n = len(self.config.folders.get(folder, []))
        if n:
            answer = QMessageBox.question(
                self, "Actuator", f"Delete the {folder} folder? Its {n} app{'s' if n != 1 else ''} "
                "stay, just outside the folder.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        if self.apply_now(lambda c: c.folders.pop(folder, None)):
            self._fill_sidebar()

    def add_app(self):
        dialog = AddAppDialog(self, list(self.config.folders))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        app, folder = cfg.app_key(dialog.value()), dialog.folder()

        def change(c):
            if app not in c.apps:
                c.add_app(app)
            if folder:
                c.move_to_folder(app, folder)
        if self.apply_now(change):
            self.current = ("app", app)
            self._fill_sidebar()

    def remove_app(self, app: str):
        n = len(self.config.bindings_for("app", app))
        detail = f"This deletes its {n} binding{'s' if n != 1 else ''}." if n else "It has no bindings."
        answer = QMessageBox.question(
            self, "Actuator", f"Remove {app.capitalize()}? {detail}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if not self.apply_now(lambda c: c.remove_app(app), take_snapshot=True):
            return
        if self.current == ("app", app):
            self.current = ("global", None)
        self._fill_sidebar()

    # Pages -----------------------------------------------------------------

    def _set_page(self, widget: QWidget):
        while self.pages.count():
            old = self.pages.widget(0)
            self.pages.removeWidget(old)
            old.deleteLater()
        self.pages.addWidget(widget)

    def _show_scope(self, scope: str, app: str | None, select=None):
        self.current = (scope, app)
        self.settings.setValue("last/page", f"{scope}|{app or ''}")
        tabs = QTabWidget()
        for title, section in cfg.DEVICES.items():
            if self.tab_visible(title):
                page = self._mouse_page(scope, app, select) if section == "mouse" else \
                    self._gesture_page(scope, app, section, select)
                tabs.addTab(page, title)
        # Stay on the same device tab when switching apps, and across restarts.
        last_tab = self.settings.value("last/tab", "Mouse", type=str)
        tabs.setCurrentIndex(next((i for i in range(tabs.count()) if tabs.tabText(i) == last_tab), 0))
        tabs.currentChanged.connect(lambda i: self.settings.setValue("last/tab", tabs.tabText(i)))
        self._set_page(tabs)

    def _refresh(self, select=None):
        """Rebuild the current page after bindings were added, removed or moved."""
        self.mark_dirty()
        if self.current[0] == "app" and self.current[1] not in self.config.apps:
            self.current = ("global", None)
        self._fill_sidebar(select)

    def _wire(self, page: BindingsPage, row_text):
        """Keep a page's rows and the dirty flag in step with its editor."""
        def changed():
            self.mark_dirty()
            item = page.tree.currentItem()
            if item:
                p = item.data(0, ROLE)
                for col, text in enumerate(row_text(p)):
                    item.setText(col, text)
        page.editor.changed.connect(changed)
        page.editor.structure_changed.connect(lambda: self._refresh())
        page.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        page.tree.customContextMenuRequested.connect(lambda pos: self._binding_menu(page, pos))
        QShortcut(QKeySequence.StandardKey.Copy, page.tree, lambda: self._copy_binding(page.tree.currentItem()))
        QShortcut(QKeySequence.StandardKey.Paste, page.tree, self._paste_here)

    # Copy and paste ---------------------------------------------------------

    def _targets(self, exclude) -> list[tuple[str, str | None, str]]:
        out = [("global", None, "Global")] + [("app", a, a.capitalize()) for a in self.config.apps]
        return [t for t in out if (t[0], t[1]) != exclude]

    def _binding_menu(self, page: BindingsPage, pos):
        item = page.tree.itemAt(pos)
        if item and item.flags() & Qt.ItemFlag.ItemIsSelectable:
            page.tree.setCurrentItem(item)
        p = item.data(0, ROLE) if item else None
        binding = p.get("binding") if isinstance(p, dict) else None
        menu = QMenu(self)
        if binding:
            menu.addAction(QIcon.fromTheme("edit-copy"), "Copy", lambda: self._copy_binding(item))
            to = menu.addMenu(QIcon.fromTheme("go-next"), "Copy to")
            for scope, app, label in self._targets(self.current):
                to.addAction(label, lambda s=scope, a=app: self._copy_into([(binding.section, binding.data)], s, a))
            to.addSeparator()
            to.addAction(QIcon.fromTheme("list-add"), "New app…",
                         lambda: self._copy_to_new_app([(binding.section, binding.data)]))
        elif isinstance(p, dict) and p.get("bind"):
            menu.addAction(QIcon.fromTheme("list-add"), "Bind an action", p["bind"])
        if self._clipboard:
            menu.addAction(QIcon.fromTheme("edit-paste"), "Paste", self._paste_here)
        if menu.isEmpty():
            menu.addAction("Nothing to copy here").setEnabled(False)
        if binding:
            menu.addSeparator()
            menu.addAction(QIcon.fromTheme("edit-delete"), "Delete", page.editor._delete)
        menu.exec(page.tree.viewport().mapToGlobal(pos))

    def _copy_binding(self, item):
        p = item.data(0, ROLE) if item else None
        b = p.get("binding") if isinstance(p, dict) else None
        if b:
            self._clipboard = [(b.section, d.plain(b.data))]
            self.statusBar().showMessage("Binding copied. Paste it into another app with Ctrl+V.", 4000)

    def _paste_here(self):
        if self._clipboard:
            self._copy_into(self._clipboard, *self.current)

    def _copy_to_new_app(self, items):
        dialog = AddAppDialog(self, list(self.config.folders))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        app, folder = cfg.app_key(dialog.value()), dialog.folder()

        def change(c):
            if app not in c.apps:
                c.add_app(app)
            if folder:
                c.move_to_folder(app, folder)
        if self.apply_now(change):
            self._copy_into(items, "app", app)

    def _copy_into(self, items, scope: str, app: str | None):
        where = app.capitalize() if app else "Global"
        clash = copying.clashes(self.config, items, scope, app)
        replace = False
        if clash:
            box = QMessageBox(QMessageBox.Icon.Question, "Already set up",
                              f"{where} already has {len(clash)} binding{'s' if len(clash) != 1 else ''} "
                              "for the same button or gesture:\n\n"
                              + "\n".join(f"• {d.gesture_title(b.data)} → {d.summary(b)}" for b in clash[:8]),
                              parent=self)
            rep = box.addButton("Replace them", QMessageBox.ButtonRole.AcceptRole)
            skip = box.addButton("Skip those", QMessageBox.ButtonRole.ActionRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.exec()
            if box.clickedButton() not in (rep, skip):
                return
            replace = box.clickedButton() is rep
        added, skipped = copying.paste(self.config, items, scope, app, replace)
        if added:
            self.mark_dirty()
        msg = f"Copied {added} binding{'s' if added != 1 else ''} to {where}"
        msg += f", skipped {skipped}." if skipped else "."
        self.statusBar().showMessage(msg + (" Save to keep it." if added else ""), 6000)
        self.current = (scope, app)
        self._fill_sidebar()

    def _bindings(self, scope, app, section=None):
        """Bindings for a page. Global also shows the ones whose conditions are too complex
        for the editor, so nothing is hidden; their conditions are edited as YAML."""
        found = self.config.bindings_for(scope, app, section)
        if scope == "global":
            found += self.config.bindings_for("custom", None, section)
        return found

    def _mouse_page(self, scope, app, select=None) -> QWidget:
        where = scope_title(scope, app)
        page = BindingsPage(["Input", "Action"], self.config)
        page.tree.headerItem().setToolTip(0, "Double-click a name to rename it. Renaming only changes "
                                             "the name shown here.")
        page.tree.headerItem().setToolTip(1, f"What it does in {where}")
        bindings = self._bindings(scope, app)
        bound = [(btn, b) for btn in self.config.buttons
                 if (b := next((x for x in bindings if x.button == btn.src), None))]
        gestures = [b for b in bindings if b.section == "mouse" and not b.button]
        both = bool(bound and gestures)  # headings only help when there are two kinds to tell apart
        if both:
            page.add_group("Buttons")
        for btn, b in bound:  # only buttons that do something here
            name = button_name(btn)
            item = page.add_row([name, d.summary(b)], {"title": f"{name} in {where}", "binding": b, "src": btn.src})
            item.setToolTip(0, f"Sends {d.button_label(btn.src)}. Double-click to rename.")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        if gestures:
            if both:
                page.add_group("Gestures")
            for b in gestures:
                title = d.gesture_title(b.data)
                page.add_row([title, d.summary(b)], {"title": f"{title} in {where}", "binding": b})
        menu = QMenu(self)
        unbound = [btn for btn in self.config.buttons if btn.src not in {x.src for x, _b in bound}]
        for btn in unbound:
            menu.addAction(f"{button_name(btn)}  ({d.button_label(btn.src)})" if button_name(btn) != d.button_label(btn.src)
                           else button_name(btn), lambda btn=btn: self._bind_button(btn, scope, app))
        if unbound:
            menu.addSeparator()
        menu.addAction(QIcon.fromTheme("input-mouse"), "Press a new button…", lambda: self.add_button(scope, app))
        page.add_button(self._menu_button("Add button binding", "list-add", menu))
        page.add_button(self._gesture_menu_button("mouse", scope, app))
        page.finish(f"No mouse bindings for {where} yet. Use Add button binding.", select)
        page.tree.setEditTriggers(QTreeWidget.EditTrigger.NoEditTriggers)
        page.tree.itemDoubleClicked.connect(
            lambda item, col: page.tree.editItem(item, 0) if item.flags() & Qt.ItemFlag.ItemIsEditable else None)
        page.tree.itemChanged.connect(lambda item, col: self._renamed(page, item) if col == 0 else None)
        self._wire(page, lambda p: [page.tree.currentItem().text(0), d.summary(p["binding"])])
        return page

    def _gesture_menu_button(self, section, scope, app) -> QToolButton:
        menu = QMenu(self)
        for t in GESTURE_TYPES[section]:
            if section == "mouse" and t == "press":
                continue  # buttons are added with Add button
            menu.addAction(t.capitalize(), lambda t=t: self._add_gesture(section, t, scope, app))
        b = self._menu_button("Add gesture", "list-add", menu)
        return b

    def _bind_button(self, btn: cfg.Button, scope, app):
        section, data = new_button_binding(self.config, btn, scope, app)
        self.config.add_binding(section, data)
        self._refresh(lambda p: p.get("src") == btn.src)

    def _add_gesture(self, section, trigger_type, scope, app):
        data = new_gesture_binding(self.config, section, trigger_type, scope, app)
        self.config.add_binding(section, data)
        self._refresh(lambda p: p.get("binding") is not None and p["binding"].data is data)

    def add_button(self, scope, app):
        """Learn a new button by pressing it, then bind it here."""
        dialog = CaptureDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        src = dialog.src
        if not any(b.src == src for b in self.config.buttons):
            if not self.apply_now(lambda c: c.add_button(src, d.button_label(src))):
                return
        btn = next(b for b in self.config.buttons if b.src == src)
        if any(x.button == src for x in self._bindings(scope, app)):
            self._show_scope(scope, app, select=lambda p: p.get("src") == src)  # already bound: just show it
        else:
            self._bind_button(btn, scope, app)

    def _renamed(self, page: BindingsPage, item: QTreeWidgetItem):
        p = item.data(0, ROLE)
        if not (isinstance(p, dict) and "src" in p):
            return
        src = p["src"]
        btn = next(b for b in self.config.buttons if b.src == src)
        new = item.text(0).strip() or d.button_label(src)  # empty resets to the raw input
        if new == button_name(btn):
            return
        self.apply_now(lambda c: c.rename_button(src, new if new != d.button_label(src) else src))
        self._show_scope(*self.current, select=lambda q: q.get("src") == src)

    def _gesture_page(self, scope, app, section, select=None) -> QWidget:
        where = scope_title(scope, app)
        page = BindingsPage(["Gesture", "Action"], self.config)
        for b in self._bindings(scope, app, section):
            if b.button:  # mouse-emitted shortcuts live on the Mouse tab
                continue
            title = d.gesture_title(b.data)
            page.add_row([title, d.summary(b)], {"title": f"{title} in {where}", "binding": b})
        page.add_button(self._gesture_menu_button(section, scope, app))
        page.finish(f"No {section} bindings for {where} yet.", select)
        self._wire(page, lambda p: [d.gesture_title(p["binding"].data), d.summary(p["binding"])])
        return page
