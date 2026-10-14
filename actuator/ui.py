"""Main window."""

import html
import re
import shutil
import subprocess
from datetime import date, datetime
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QPoint, QSettings, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor, QDesktopServices, QFontDatabase, QIcon, QKeySequence, QPainter, QPixmap, QShortcut,
)
from PySide6.QtWidgets import (
    QApplication, QDialog, QTextBrowser, QListWidget, QListWidgetItem, QToolTip, QFormLayout, QSpinBox, QDialogButtonBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
    QCheckBox, QComboBox, QGroupBox, QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu,
    QMessageBox, QPushButton, QScrollArea, QSizePolicy,
    QSplitter, QStackedWidget, QTabWidget, QToolBar, QToolButton, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)
from PySide6.QtSvg import QSvgRenderer
from ruamel.yaml import YAML

from . import REPO_URL, __version__
from . import apps as apps_mod
from . import config as cfg
from . import copying
from . import diffing
from . import gitsync
from . import describe as d
from . import keys
from . import pause
from . import versions
from . import writer
from . import yaml_editor
from . import devices as devices_mod
from .editor import GESTURE_TYPES, BindingEditor, new_button_binding, new_gesture_binding, record_stroke

ROLE = Qt.ItemDataRole.UserRole


def _dimmable(icon: QIcon, size: int = 22) -> QIcon:
    """The icon with a clearly faded version for its disabled state."""
    out = QIcon()
    normal = icon.pixmap(size, size)
    faded = QPixmap(normal.size())
    faded.setDevicePixelRatio(normal.devicePixelRatio())
    faded.fill(Qt.GlobalColor.transparent)
    painter = QPainter(faded)
    painter.setOpacity(0.35)
    painter.drawPixmap(0, 0, normal)
    painter.end()
    out.addPixmap(normal, QIcon.Mode.Normal)
    out.addPixmap(faded, QIcon.Mode.Disabled)
    return out


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


def kwin_pid() -> str:
    ok, out = run("pidof", "kwin_wayland")
    if not ok:
        ok, out = run("pidof", "kwin_x11")
    return out.split()[0] if ok and out else ""


INSTALL_URL = "https://wiki.inputactions.org/main/getting-started/installation/index.html"


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
    return apps_mod.display_name(app) if scope == "app" else scope.capitalize()


class AddAppDialog(QDialog):
    """Pick an installed app from a searchable list, pick a window, or type a window class."""

    def __init__(self, parent, folders: list[str], existing: list[str] = ()):
        super().__init__(parent)
        self.setWindowTitle("Add app")
        existing = {e.lower() for e in existing}
        self.search = QLineEdit(placeholderText="Search apps")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        self.list = QListWidget()
        self.list.setIconSize(self.list.iconSize().expandedTo(QSize(24, 24)))
        for app in apps_mod.installed():
            item = QListWidgetItem(QIcon.fromTheme(app.icon, QIcon.fromTheme("application-x-executable")), app.name)
            item.setData(Qt.ItemDataRole.UserRole, app.window_class)
            item.setToolTip(f"Window class: {app.window_class}")
            if app.window_class.lower() in existing:
                item.setText(f"{app.name}  (already added)")
                item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(item)
        self.list.itemDoubleClicked.connect(lambda *_: self._accept())
        self.list.currentItemChanged.connect(lambda item, _p: item and self.edit.clear())
        self.pick = QPushButton(QIcon.fromTheme("crosshairs"), "Pick window")
        self.pick.setToolTip("Click a running app's window to use exactly what it reports. "
                             "Most reliable for games.")
        self.pick.clicked.connect(self._start_pick)
        self.edit = QLineEdit(placeholderText="Or type a window class")
        self.edit.textEdited.connect(lambda _t: self.list.setCurrentItem(None))
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: palette(placeholder-text);")
        self.folder_box = QComboBox()
        self.folder_box.addItem("No folder", None)
        for f in folders:
            self.folder_box.addItem(f, f)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        top = QHBoxLayout()
        top.addWidget(self.search, 1)
        top.addWidget(self.pick)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.edit)
        layout.addWidget(self.hint)
        if folders:
            frow = QHBoxLayout()
            frow.addWidget(QLabel("Folder"))
            frow.addWidget(self.folder_box, 1)
            layout.addLayout(frow)
        layout.addWidget(buttons)
        self.resize(440, 560)
        self.search.setFocus()
        self._left = 0

    def _filter(self, text: str):
        text = text.lower()
        first = None
        for i in range(self.list.count()):
            item = self.list.item(i)
            hit = text in item.text().lower() or text in str(item.data(Qt.ItemDataRole.UserRole)).lower()
            item.setHidden(not hit)
            if hit and first is None and item.flags() & Qt.ItemFlag.ItemIsSelectable:
                first = item
        if text and first:
            self.list.setCurrentItem(first)  # Enter adds the top match

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
        if not wc or wc.lower() == "actuator":
            self.hint.setText("Couldn't read a window. Try again, or pick from the list.")
            return
        self.list.setCurrentItem(None)
        self.edit.setText(wc)
        app = apps_mod.by_class(wc)
        self.hint.setText(f"Picked {app.name if app else wc} ({wc}).")

    def _accept(self):
        if self.value():
            self.accept()
        else:
            self.hint.setText("Pick an app first.")

    def value(self) -> str:
        if self.edit.text().strip():
            return self.edit.text().strip()
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item and not item.isHidden() else ""

    def folder(self) -> str | None:
        return self.folder_box.currentData()


class RestorePreview(QDialog):
    """Lists only what restoring a version would change: + added, − removed, ~ changed."""

    def __init__(self, parent, groups: dict[str, list[tuple[str, str, str, str]]] | None):
        super().__init__(parent)
        self.setWindowTitle("Restore this version?")
        body = versions.changes_html(groups, self.palette())
        intro = QLabel("Restoring makes these changes to your config:" if groups else "")
        intro.setVisible(bool(groups))
        view = QTextBrowser()
        view.setHtml(body)
        view.setOpenLinks(False)
        note = QLabel("Your current config is kept as a version first, so this can be undone.")
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(placeholder-text);")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        restore = buttons.addButton("Restore", QDialogButtonBox.ButtonRole.AcceptRole)
        restore.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(view, 1)
        layout.addWidget(note)
        layout.addWidget(buttons)
        self.resize(560, 400)


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


DEV_FEATURES = [  # (settings key, label, tooltip) — all off by default
    ("device_settings", "Enable Device Settings", "Swipe angles, thresholds and timeouts for your devices."),
    ("yaml_editor", "Enable Full YAML Editor", "Adds a YAML button to the toolbar to edit config.yaml directly."),
    ("only_if", "Enable Only If Conditions", "Shows the condition builder in the binding editor."),
    ("git_autocommit", "Enable Git Auto-Commit",
     "Commits config.yaml to its git repository whenever Actuator saves it. See the README for setup."),
]


# GitHub's mark (from github.com/logos), drawn in the theme's text color.
GITHUB_MARK = (
    "M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53"
    "-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33"
    ".66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67"
    "-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56"
    ".82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8"
    ".013 8.013 0 0016 8c0-4.42-3.58-8-8-8z")


def github_icon(color: QColor, size: int = 16) -> QPixmap:
    svg = (f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'>"
           f"<path fill='{color.name()}' d='{GITHUB_MARK}'/></svg>").encode()
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(svg).render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return pixmap


def _heading(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet("font-weight: 600; margin-top: 6px;")
    return label


class OptionsDialog(QDialog):
    """Options, laid out like KDE's settings pages: small headings, aligned rows, no boxes."""

    def __init__(self, parent, tabs: dict[str, bool], autostart: bool, keep: int):
        super().__init__(parent)
        self.settings = parent.settings
        self._version_clicks = 0
        self.setWindowTitle("Options")
        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        layout.setContentsMargins(18, 14, 18, 14)

        # General
        layout.addWidget(_heading("General"))
        # Plain left-aligned rows, like the sections below (a form would push the checkbox right).
        self.autostart = QCheckBox("Start InputActions when I log in")
        self.autostart.setChecked(autostart)
        info = QToolButton(icon=QIcon.fromTheme("help-about-symbolic", QIcon.fromTheme("dialog-information")))
        info.setAutoRaise(True)
        info.setToolTip("How autostart works")
        info.clicked.connect(self._explain_autostart)
        auto_row = QHBoxLayout()
        auto_row.addWidget(self.autostart)
        auto_row.addWidget(info)
        auto_row.addStretch(1)
        layout.addLayout(auto_row)
        self.keep = QSpinBox(minimum=1, maximum=200)
        self.keep.setValue(keep)
        keep_row = QHBoxLayout()
        keep_label = QLabel("How many saves to keep:")
        keep_label.setContentsMargins(3, 0, 0, 0)  # line up with the checkbox text above
        keep_row.addWidget(keep_label)
        keep_row.addWidget(self.keep)
        keep_row.addStretch(1)
        layout.addLayout(keep_row)

        # Tabs, in two columns
        layout.addWidget(_heading("Device tabs"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        self.boxes = {}
        for i, (title, on) in enumerate(tabs.items()):
            box = QCheckBox(title)
            box.setChecked(on)
            box.toggled.connect(self._keep_one)
            grid.addWidget(box, i // 2, i % 2)
            self.boxes[title] = box
        grid.setColumnStretch(2, 1)
        layout.addLayout(grid)

        # Developer (only once turned on)
        self.dev_box = QWidget()
        self.dev_layout = QVBoxLayout(self.dev_box)
        self.dev_layout.setContentsMargins(0, 0, 0, 0)
        self.dev_layout.addWidget(_heading("Developer Options"))
        self.features = {}
        for key, label, note in DEV_FEATURES:
            box = QCheckBox(label)
            box.setChecked(self.settings.value(f"dev/{key}", False, type=bool))
            box.setToolTip(note)
            self.dev_layout.addWidget(box)
            self.features[key] = box
        # Git auto-commit settings, shown under its toggle.
        self.git_box = QWidget()
        gl = QVBoxLayout(self.git_box)
        gl.setContentsMargins(26, 0, 0, 4)
        when_row = QHBoxLayout()
        when_row.addWidget(QLabel("Commit:"))
        self.git_when = QComboBox()
        self.git_when.addItem("When I save", "saves")
        self.git_when.addItem("Every change, including folders and apps", "all")
        self.git_when.setCurrentIndex(max(self.git_when.findData(
            self.settings.value("git/when", "saves", type=str)), 0))
        when_row.addWidget(self.git_when)
        when_row.addStretch(1)
        gl.addLayout(when_row)
        self.git_push = QCheckBox("Push after committing")
        self.git_push.setChecked(self.settings.value("git/push", False, type=bool))
        gl.addWidget(self.git_push)
        # Setup checklist: what's ready, with a link to the instructions if something isn't.
        st = gitsync.status(cfg.CONFIG_PATH)
        ok_mark = "<span style='color:#2e9e5b'>✓</span>"
        bad_mark = "<span style='color:#d24545'>✗</span>"
        warn_mark = "<span style='color:#c58a00'>⚠</span>"
        rows = [f"{ok_mark if st['git'] else bad_mark} Git is installed"]
        if st["git"]:
            rows.append(f"{ok_mark} Config folder is a git repository: {html.escape(str(st['repo']))}"
                        if st["repo"] else f"{bad_mark} The config folder isn't a git repository yet")
        if st["repo"]:
            rows.append(f"{ok_mark} Pushes to {html.escape(st['remote'])}" if st["remote"]
                        else f"{warn_mark} No remote to push to (only needed for pushing)")
        if not st["repo"] or not st["remote"]:
            rows.append(f"<a href='{REPO_URL}#git-auto-commit-developer-mode'>How to set it up</a>")
        checklist = QLabel("<br>".join(rows))
        checklist.setWordWrap(True)
        checklist.setOpenExternalLinks(True)
        checklist.setTextFormat(Qt.TextFormat.RichText)
        gl.addWidget(checklist)
        git_toggle = self.features["git_autocommit"]
        self.dev_layout.insertWidget(self.dev_layout.indexOf(git_toggle) + 1, self.git_box)
        self.git_box.setVisible(git_toggle.isChecked())
        git_toggle.toggled.connect(lambda on: (self.git_box.setVisible(on), self.adjustSize()))

        dev_buttons = QHBoxLayout()
        devices = QPushButton(QIcon.fromTheme("input-mouse"), "Device settings…")
        devices.clicked.connect(parent.open_device_settings)
        devices.setVisible(self.features["device_settings"].isChecked())
        self.features["device_settings"].toggled.connect(devices.setVisible)
        dev_off = QPushButton("Turn off developer mode")
        dev_off.clicked.connect(lambda: self._set_dev_mode(False))
        dev_buttons.addWidget(devices)
        dev_buttons.addWidget(dev_off)
        dev_buttons.addStretch(1)
        self.dev_layout.addLayout(dev_buttons)
        self.dev_box.setVisible(self.settings.value("dev_mode", False, type=bool))
        layout.addWidget(self.dev_box)
        layout.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
                                   | QDialogButtonBox.StandardButton.RestoreDefaults)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        reset = buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults)
        reset.setText("Reset to defaults")
        reset.clicked.connect(self._reset_defaults)
        layout.addSpacing(8)
        layout.addWidget(buttons)
        # Start with focus on OK; otherwise the first checkbox gets it, and some themes underline it.
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setDefault(True)
        QTimer.singleShot(0, ok.setFocus)

        # Footer, centered: GitHub link, then the version. Clicking the version ten
        # times turns on developer mode.
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: palette(mid);")
        layout.addSpacing(6)
        layout.addWidget(line)
        logo = QLabel()
        logo.setPixmap(github_icon(self.palette().text().color()))
        links = QLabel(f"<a href='{REPO_URL}'>GitHub</a>")
        links.setOpenExternalLinks(True)
        link_row = QHBoxLayout()
        link_row.addStretch(1)
        link_row.addWidget(logo)
        link_row.addWidget(links)
        link_row.addStretch(1)
        layout.addLayout(link_row)
        self.version = QLabel(f"Version {__version__}")
        self.version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.version.setStyleSheet("color: palette(placeholder-text);")
        self.version.mousePressEvent = self._version_clicked
        layout.addWidget(self.version)
        self.setMinimumWidth(420)
        self.resize(420, 0)

    def _reset_defaults(self):
        """Back to how Actuator starts out. Applied on OK, like any other change here."""
        self.autostart.setChecked(True)
        self.keep.setValue(10)
        for box in self.boxes.values():
            box.setChecked(True)
        if self.dev_box.isVisible():
            for box in self.features.values():
                box.setChecked(False)
            self.git_when.setCurrentIndex(self.git_when.findData("saves"))
            self.git_push.setChecked(False)

    def _toast(self, text: str):
        """A small Android-style message just above the version label."""
        pos = self.version.mapToGlobal(self.version.rect().topLeft())
        QToolTip.showText(pos - QPoint(0, self.version.height() * 2), text, self.version, self.version.rect(), 1500)

    def _version_clicked(self, _event):
        if self.settings.value("dev_mode", False, type=bool):
            self._toast("No need, you're already a developer.")
            return
        self._version_clicks += 1
        left = 10 - self._version_clicks
        if left <= 0:
            self._set_dev_mode(True)
            self._toast("You're now a developer!")
        elif left <= 5:
            self._toast(f"You're {left} step{'s' if left != 1 else ''} away from being a developer.")

    def _set_dev_mode(self, on: bool):
        self.settings.setValue("dev_mode", on)
        self._version_clicks = 0
        self.dev_box.setVisible(on)
        self.adjustSize()

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
    """Records a mouse button, or the key combo a mouse button sends, as soon as it opens.
    Plasma's global shortcuts and InputActions are paused meanwhile, so e.g. a button that
    sends Alt+F4 is recorded instead of closing the window."""

    def __init__(self, parent, known: list[cfg.Button]):
        super().__init__(parent)
        self.setWindowTitle("Record button")
        self.src: str | None = None
        self.kind = "mouse"
        self.pause = pause.ShortcutPause()
        self.prompt = QLabel("Press the mouse button you want to bind.\n\n"
                             "Plasma shortcuts are paused while this is open.")
        self.prompt.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.prompt.setMinimumSize(400, 140)
        self.prompt.setFrameShape(QFrame.Shape.StyledPanel)
        self.prompt.setStyleSheet("QLabel { border: 2px solid #d24545; border-radius: 6px; }")
        self.found = QLabel()
        self.found.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(self.prompt)
        layout.addWidget(self.found)
        if known:
            layout.addWidget(QLabel("Or pick one you've used before:"))
            row = QHBoxLayout()
            for btn in known:
                b = QPushButton(button_name(btn))
                b.setToolTip(f"Sends {d.button_label(btn.src)}")
                b.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # keep key presses going to the recorder
                b.clicked.connect(lambda _=False, btn=btn: self._done(btn.src, btn.kind))
                row.addWidget(b)
            row.addStretch(1)
            layout.addLayout(row)
        type_row = QHBoxLayout()
        self.manual = QLineEdit(placeholderText="Or type it: forward, extra1, Shift+Meta+[")
        self.manual.returnPressed.connect(self._typed)
        use = QPushButton("Use")
        use.clicked.connect(self._typed)
        type_row.addWidget(self.manual, 1)
        type_row.addWidget(use)
        layout.addLayout(type_row)
        self.error = QLabel()
        self.error.setStyleSheet("color: #d24545;")
        layout.addWidget(self.error)
        cancel = QPushButton("Cancel")
        cancel.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        cancel.clicked.connect(self.reject)
        layout.addWidget(cancel, 0, Qt.AlignmentFlag.AlignRight)
        self.prompt.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.prompt.installEventFilter(self)
        self._left_right: str | None = None

    def showEvent(self, event):
        super().showEvent(event)
        self.pause.start()
        self.prompt.setFocus()

    def done(self, result):
        self.pause.stop()  # however the window closes
        super().done(result)

    def eventFilter(self, obj, event):
        if obj is self.prompt:
            if event.type() == event.Type.MouseButtonPress:
                name = keys.MOUSE_BUTTONS.get(event.button())
                if name in ("left", "right"):
                    # Probably just clicking the window; only take it if clicked twice on purpose.
                    if self._left_right == name:
                        self._done(name, "mouse")
                    else:
                        self._left_right = name
                        self.found.setText(f"That's the {name} button, which you use all the time. "
                                           f"{name.capitalize()}-click again here to bind it anyway.")
                else:
                    self._done(name or f"button{int(event.button())}", "mouse")
                return True
            if event.type() == event.Type.KeyPress:
                if event.key() == Qt.Key.Key_Escape and not event.modifiers():
                    self.reject()
                    return True
                combo = keys.from_key_event(event)
                if combo:
                    self._done(combo, "shortcut")
                return True
        return super().eventFilter(obj, event)

    def _typed(self):
        text = self.manual.text().strip()
        if not text:
            self.error.setText("Type a button or key combo first.")
            return
        try:
            if "+" in text:
                self._done(keys.parse_combo(text), "shortcut")
            else:
                self._done(keys.to_name(text), "mouse")
        except ValueError as e:
            self.error.setText(str(e))

    def _done(self, src: str, kind: str):
        self.src, self.kind = src, kind
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
        # InputActions can't report whether it's paused, so remember pausing it ourselves. KWin
        # restarts with every login, which unpauses it, so the memory only counts for the same KWin.
        self.suspended = self.loaded and bool(kwin_pid()) and \
            self.settings.value("suspended_kwin_pid", "", type=str) == kwin_pid()
        last = self.settings.value("last/page", "global|", type=str).split("|", 1)
        self.current = (last[0], last[1] or None) if last[0] in ("global", "app") else ("global", None)
        self._issues = ""
        self._last_written: str | None = None
        self._clipboard: list[tuple[str, dict]] = []
        self.broken: str | None = None  # the YAML error, while config.yaml can't be read
        self._pusher = gitsync.Pusher(self)
        self._pusher.done.connect(self._pushed)
        # Always-visible status bar, with a lasting git result on the right when auto-commit is on.
        self.git_status = QLabel()
        self.git_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.git_status.setOpenExternalLinks(True)  # a successful result links to the repo
        self.statusBar().addWidget(self.git_status, 1)  # stretch: centered across the bar
        self.git_status.setVisible(False)
        self._git_hide = QTimer(self, singleShot=True, interval=10_000)
        self._git_hide.timeout.connect(lambda: self.git_status.setVisible(False))
        # Undo history of unsaved changes: each entry is the whole config as text.
        # history[0] is what's saved; the last entry is the current state.
        self.history: list[str] = []
        self.future: list[str] = []
        self._record_timer = QTimer(self, singleShot=True, interval=500)
        self._record_timer.timeout.connect(self._record)

        self._build_toolbar()
        self.sidebar = SidebarTree()
        self.sidebar.setColumnCount(2)  # name, binding count
        self.sidebar.header().setStretchLastSection(False)
        self.sidebar.header().setSectionResizeMode(0, self.sidebar.header().ResizeMode.Stretch)
        self.sidebar.header().setSectionResizeMode(1, self.sidebar.header().ResizeMode.ResizeToContents)
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
        self.body = split
        central = QWidget()
        cl = QVBoxLayout(central)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(self._build_broken_bar())
        cl.addWidget(split, 1)
        self.setCentralWidget(central)

        self._apply_features()
        QTimer.singleShot(0, self._warn_system_config)
        QTimer.singleShot(0, self._check_installed)
        pause.recover()  # in case a previous run crashed while shortcuts were paused
        pause.inputactions_running = lambda: self.running
        self.watcher = QFileSystemWatcher()
        self.watcher.fileChanged.connect(lambda _p: QTimer.singleShot(300, self._file_changed))
        self.load()

    # Toolbar ---------------------------------------------------------------

    def _build_toolbar(self):
        bar = QToolBar(movable=False)
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        # Some themes draw disabled toolbar buttons almost like enabled ones; make it obvious.
        bar.setStyleSheet("QToolButton:disabled { color: palette(placeholder-text); }")
        self.addToolBar(bar)

        # Left: InputActions itself — its status, and a clear button to stop or start it.
        self.status = QLabel()
        self.status.setContentsMargins(8, 0, 8, 0)
        bar.addWidget(self.status)
        self.toggle_action = bar.addAction("", self.toggle_running)
        bar.widgetForAction(self.toggle_action).setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.issues_label = QLabel()  # only shown when InputActions reports a problem
        self.issues_action = bar.addWidget(self.issues_label)  # toolbar widgets hide via their action
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        bar.addWidget(spacer)

        # Right: undo, redo and reload as icons; everything occasional in the ☰ menu; Save last.
        self.undo_action = bar.addAction(_dimmable(QIcon.fromTheme("edit-undo")), "Undo", self.undo)
        self.undo_action.setToolTip("Undo the last unsaved change (Ctrl+Z)")
        self.redo_action = bar.addAction(_dimmable(QIcon.fromTheme("edit-redo")), "Redo", self.redo)
        self.redo_action.setToolTip("Redo (Ctrl+Shift+Z)")
        # Window-wide, so a focused text box still gets its own Ctrl+Z first.
        QShortcut(QKeySequence.StandardKey.Undo, self, self.undo)
        QShortcut(QKeySequence.StandardKey.Redo, self, self.redo)
        reload = bar.addAction(QIcon.fromTheme("view-refresh"), "Reload", self.reload)
        reload.setToolTip("Reload config.yaml and InputActions")
        bar.widgetForAction(reload).setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        bar.addSeparator()

        menu = QMenu(self)
        menu.addAction(QIcon.fromTheme("configure"), "Options…", self.show_options)
        self.yaml_action = menu.addAction(QIcon.fromTheme("text-x-script"), "Edit YAML…", self.open_yaml_editor)
        menu.addSeparator()
        menu.addAction(QIcon.fromTheme("document-export"), "Export YAML…", self.export_yaml)
        menu.addAction(QIcon.fromTheme("document-revert"), "Restore…", self.open_versions)
        hamburger = QToolButton(icon=QIcon.fromTheme("application-menu", QIcon.fromTheme("open-menu-symbolic")))
        hamburger.setToolTip("Menu")
        hamburger.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        hamburger.setMenu(menu)
        hamburger.setStyleSheet("QToolButton::menu-indicator { image: none; }")
        bar.addWidget(hamburger)

        self.save_button = QPushButton(QIcon.fromTheme("document-save"), "Save")
        self.save_button.setToolTip("Save binding changes and reload InputActions (Ctrl+S)")
        # Highlighted only when there's something to save; otherwise it's a plain, disabled button.
        self.save_button.setStyleSheet(
            "QPushButton { padding: 4px 16px; margin: 0 6px; }"
            "QPushButton:enabled { background: palette(highlight); color: palette(highlighted-text); "
            "font-weight: 600; border-radius: 4px; }")
        self.save_button.clicked.connect(self.save)
        bar.addWidget(self.save_button)
        QShortcut(QKeySequence("Ctrl+S"), self, lambda: self.dirty and self.save())
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
        for key, box in dialog.features.items():
            self.settings.setValue(f"dev/{key}", box.isChecked())
        self.settings.setValue("git/when", dialog.git_when.currentData())
        self.settings.setValue("git/push", dialog.git_push.isChecked())
        self._apply_features()
        self._fill_sidebar()

    def _check_installed(self):
        if shutil.which("inputactions"):
            return
        box = QMessageBox(QMessageBox.Icon.Warning, "InputActions isn't installed",
                          "Actuator edits the configuration of InputActions, which doesn't seem to be "
                          "installed: the <code>inputactions</code> command wasn't found.<br><br>"
                          "You can still edit your config, but Start/Stop, Reload and Pick window won't "
                          f"work until it is. <a href='{INSTALL_URL}'>How to install InputActions</a>",
                          QMessageBox.StandardButton.Ok, self)
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        box.exec()

    def _warn_system_config(self):
        if not cfg.SYSTEM_CONFIG_PATH.exists():
            return
        QMessageBox.warning(
            self, "InputActions is using a system-wide config",
            f"InputActions is reading {cfg.SYSTEM_CONFIG_PATH}, which takes priority over your own "
            f"config at {cfg.CONFIG_PATH}.\n\nActuator edits your own config, so changes here won't "
            f"take effect until {cfg.SYSTEM_CONFIG_PATH} is moved or removed. That needs admin rights, "
            "for example:\n\n    sudo mv /etc/inputactions/config.yaml /etc/inputactions/config.yaml.bak")

    def feature(self, key: str) -> bool:
        """A developer feature is on only while developer mode is on too."""
        return (self.settings.value("dev_mode", False, type=bool)
                and self.settings.value(f"dev/{key}", False, type=bool))

    def _apply_features(self):
        self.yaml_action.setVisible(self.feature("yaml_editor"))
        if not self.feature("git_autocommit"):
            self.git_status.setVisible(False)

    # A config.yaml that can't be read -------------------------------------

    def _build_broken_bar(self) -> QWidget:
        """A KDE-style error banner: tinted, rounded, and readable in light and dark themes."""
        holder = QWidget()
        outer = QVBoxLayout(holder)
        outer.setContentsMargins(8, 8, 8, 0)
        bar = QFrame()
        bar.setObjectName("broken")
        bar.setStyleSheet("QFrame#broken { background: rgba(218, 68, 83, 0.12); "
                          "border: 1px solid rgba(218, 68, 83, 0.65); border-radius: 6px; }"
                          "QFrame#broken QLabel { background: transparent; border: none; }")
        outer.addWidget(bar)
        icon = QLabel()
        icon.setPixmap(QIcon.fromTheme("dialog-error").pixmap(22, 22))
        title = QLabel("config.yaml can't be read. Nothing will be saved until it's fixed.")
        title.setStyleSheet("font-weight: 600;")
        self.broken_label = QLabel()
        self.broken_label.setWordWrap(True)
        self.broken_label.setTextFormat(Qt.TextFormat.RichText)
        self.broken_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        edit = QPushButton(QIcon.fromTheme("text-x-script"), "Edit in YAML editor")
        edit.clicked.connect(lambda: self.open_yaml_editor(force=True))
        ext = QPushButton(QIcon.fromTheme("document-edit"), "Open in text editor")
        ext.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(cfg.CONFIG_PATH))))
        restore = QPushButton(QIcon.fromTheme("document-open-recent"), "Restore a previous version")
        restore.clicked.connect(self.open_versions)

        text = QVBoxLayout()
        text.setSpacing(4)
        text.addWidget(title)
        text.addWidget(self.broken_label)
        top = QHBoxLayout()
        top.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        top.addLayout(text, 1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        for b in (edit, ext, restore):
            buttons.addWidget(b)
        inner = QVBoxLayout(bar)
        inner.setContentsMargins(12, 10, 12, 10)
        inner.setSpacing(8)
        inner.addLayout(top)
        inner.addLayout(buttons)
        holder.setVisible(False)
        self.broken_bar = holder
        return holder

    def _set_broken(self, error):
        """Show or clear the "can't read config.yaml" state. While broken, nothing is written,
        so the file on disk (with someone's half-finished edit) is never overwritten."""
        if error is None:
            self.broken = None
        else:
            try:
                ok, message, _line = yaml_editor.check(cfg.CONFIG_PATH.read_text())
            except OSError as e:
                ok, message = False, str(e)
            self.broken = str(error) if ok else message
        self.broken_bar.setVisible(self.broken is not None)
        if self.broken:
            message, _, line = self.broken.partition("\n")
            mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()
            self.broken_label.setText(
                html.escape(message) + (f"<br><span style='font-family:\"{mono}\"; white-space:pre'>"
                                        f"{html.escape(line.strip())}</span>" if line.strip() else ""))
        else:
            self.broken_label.clear()
        self.body.setEnabled(self.broken is None and self.config is not None)
        self._update_status()

    def _blocked(self) -> bool:
        if self.broken is None:
            return False
        QMessageBox.warning(self, "Actuator", "config.yaml has an error, so Actuator won't write to it "
                                              "until it's fixed. Use the red bar above to fix or restore it.")
        return True

    def open_yaml_editor(self, force: bool = False):
        if not self._ask_discard("edit the YAML"):
            return
        path = cfg.CONFIG_PATH
        editor = yaml_editor.YamlEditor(self, path, path.read_text())
        if editor.exec() != QDialog.DialogCode.Accepted:
            return
        text = editor.text()
        old = self._disk_text()
        self._last_written = text
        previous = writer.snapshot(path)
        writer.write_atomic(path, text)
        self.dirty = False
        self._after_write(previous, old)

    def open_device_settings(self):
        if not devices_mod.confirm_open(self, self.settings):
            return
        dialog = devices_mod.DeviceSettingsDialog(self, self.config)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.dirty:
            self.apply_now(dialog.apply_to, take_snapshot=True)

    def tab_visible(self, title: str) -> bool:
        return self.settings.value(f"tabs/{title}", True, type=bool)


    def _update_toggle(self):
        self.toggle_action.setText("Stop" if self.running else "Start")
        self.toggle_action.setIcon(QIcon.fromTheme(
            "media-playback-pause" if self.running else "media-playback-start"))
        self.toggle_action.setToolTip("Pause InputActions" if self.running else "Start InputActions")
        self._update_status()

    def _update_status(self):
        state = ("<span style='color:#2e9e5b'>● Running</span>" if self.running
                 else "<span style='color:#c58a00'>● Stopped</span>" if self.loaded
                 else "<span style='color:#c58a00'>● Not loaded</span>")
        self.status.setText(f"<b>InputActions</b> &nbsp; {state}")
        self.issues_action.setVisible(bool(self._issues))  # only worth showing when something's wrong
        self.issues_label.setText("<span style='color:#d24545'>⚠ Config issues</span>")
        self.issues_label.setToolTip(self._issues)
        self.save_button.setEnabled(self.dirty and getattr(self, "broken", None) is None)
        self.save_button.setToolTip("Save binding changes and reload InputActions (Ctrl+S)" if self.dirty
                                    else "Nothing to save")
        self.setWindowTitle(("* " if self.dirty else "") + "Actuator")

    def _check_issues(self):
        ok, out = run_ctl("config", "issues")
        self._issues = out if ok else f"Couldn't check issues: {out}"
        self._update_status()

    def mark_dirty(self):
        self.dirty = True
        self._record_timer.start()  # quick successive edits become one undo step
        self._update_undo()
        self._update_status()

    # Undo ------------------------------------------------------------------

    def _reset_history(self):
        self._record_timer.stop()
        self.history = [writer.render(self.config)] if self.config else []
        self.future = []
        self._update_undo()

    def _record(self):
        if not self.config or not self.history:
            return
        state = writer.render(self.config)
        if state != self.history[-1]:
            self.history.append(state)
            self.future.clear()
        self._update_undo()

    def _go_to(self, state: str):
        self.config = cfg.loads(state, cfg.CONFIG_PATH)
        self.dirty = len(self.history) > 1
        self._fill_sidebar()
        self._update_status()

    def undo(self):
        if self._record_timer.isActive():  # an edit is still being grouped: record it first
            self._record_timer.stop()
            self._record()
        if len(self.history) > 1:
            self.future.append(self.history.pop())
            self._go_to(self.history[-1])
            self.statusBar().showMessage("Undone." if self.dirty else "Undone. Back to what's saved.", 3000)
        self._update_undo()

    def redo(self):
        if self.future:
            self.history.append(self.future.pop())
            self._go_to(self.history[-1])
            self.statusBar().showMessage("Redone.", 3000)
        self._update_undo()

    def _update_undo(self):
        if hasattr(self, "undo_action"):
            self.undo_action.setEnabled(len(self.history) > 1 or self._record_timer.isActive())
            self.redo_action.setEnabled(bool(self.future))

    @property
    def running(self) -> bool:
        return self.loaded and not self.suspended

    def toggle_running(self):
        if self.running:
            ok, out = run_ctl("suspend")
            if ok:
                self.suspended = True
                self.settings.setValue("suspended_kwin_pid", kwin_pid())
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
            if ok:
                self.settings.remove("suspended_kwin_pid")
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
        if self._blocked():
            return False
        if not self._confirm_first_write():
            return False
        old = self._disk_text()
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
        # Organizing changes are saved, not undoable; carry them through the undo history
        # so undoing a binding edit later doesn't bring back an old folder or app list.
        def carried(state: str) -> str:
            c = cfg.loads(state, cfg.CONFIG_PATH)
            change(c)
            c.reindex()
            return writer.render(c)
        self.history = [carried(x) for x in self.history]
        self.future = [carried(x) for x in self.future]
        run_ctl("config", "reload")
        self._check_issues()
        self._git_sync(old, "save" if take_snapshot else "instant")
        return True

    def save(self):
        if self._blocked():
            return
        if not self._confirm_first_write():
            return
        old = self._disk_text()
        try:
            self._last_written = writer.render(self.config)
            previous = writer.save(self.config)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Actuator", f"Couldn't save. Your config wasn't changed.\n\n{e}")
            return
        self.dirty = False
        self._after_write(previous, old)  # reloads, which also starts a fresh undo history

    def open_versions(self):
        dialog = versions.VersionsDialog(self, None if self.broken else self.config)
        if dialog.exec() and dialog.chosen:
            listed = dialog.chosen.parent == writer.SNAPSHOT_DIR or \
                dialog.chosen == writer.original_path(cfg.CONFIG_PATH)
            self.restore(dialog.chosen, previewed=listed)  # files from elsewhere get a preview first

    def restore(self, source: Path, previewed: bool = False):
        if not self._ask_discard("restore"):
            return
        try:
            text = Path(source).read_text()
        except OSError as e:
            QMessageBox.critical(self, "Actuator", f"Couldn't open {source}\n\n{e}")
            return
        ok, message, _line = yaml_editor.check(text)
        if previewed:
            pass  # the Restore window already showed what this does
        elif not ok:
            # A broken version can still hold edits worth having; restoring it shows the red bar to fix it.
            answer = QMessageBox.warning(
                self, "This version has an error",
                f"This version isn't valid YAML:\n\n{message}\n\nRestore it anyway? Actuator will show "
                "the error so you can fix it.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        else:
            other = cfg.load(Path(source))
            groups = diffing.changes(self.config, other) if self.config and not self.broken else None
            if not RestorePreview(self, groups).exec():
                return
        old = self._disk_text()
        self._last_written = text
        previous = writer.restore(cfg.CONFIG_PATH, Path(source))
        self.dirty = False
        self._after_write(previous, old)

    def _disk_text(self) -> str | None:
        try:
            return cfg.CONFIG_PATH.read_text()
        except OSError:
            return None

    def _show_git(self, state: str, text: str, detail: str = "", link: str | None = None):
        """A git result, centered in the status bar for 10 seconds (or until the push finishes).
        state: ok, busy, error, or warn. A link makes the message open it when clicked."""
        color = {"ok": "#2e9e5b", "busy": self.palette().placeholderText().color().name(),
                 "error": "#d24545", "warn": "#c58a00"}[state]
        icon = {"ok": "✓", "busy": "⏳", "error": "✗", "warn": "⚠"}[state]
        body = f"{icon} {html.escape(text)}"
        if link:
            body = (f"<a href='{html.escape(link)}' style='color:{color}; text-decoration:none'>"
                    f"{body} ↗</a>")
        self.git_status.setText(f"<span style='color:{color}'>{body}</span>")
        self.git_status.setToolTip(f"Open {link}\n\n{detail}" if link and detail else (detail or text))
        self.git_status.setVisible(True)
        if state == "busy":
            self._git_hide.stop()
        else:
            self._git_hide.start()

    def _git_sync(self, old_text: str | None, kind: str):
        """Commit config.yaml (and push) if that developer option is on. kind is "save" for
        Save-like writes, "instant" for organizing changes such as folders."""
        if not self.feature("git_autocommit"):
            return
        if kind == "instant" and self.settings.value("git/when", "saves", type=str) != "all":
            return
        path = cfg.CONFIG_PATH
        root = gitsync.repo_root(path)
        if root is None:
            self._show_git("warn", "Not committed: the config folder isn't a git repository",
                           "See the README's Git auto-commit section for setup.")
            return
        new_text = self._disk_text() or ""
        ok, out = gitsync.commit(path, gitsync.message(old_text, new_text))
        if not ok:
            self._show_git("error", "Commit failed", out or "git gave no details")
            return
        if out == "nothing to commit":
            return
        if self.settings.value("git/push", False, type=bool):
            self._show_git("busy", "Committed. Pushing…", out)
            self._pusher.push(root)
        else:
            self._show_git("ok", "Committed", out)  # not on the remote, so nothing to link to

    def _pushed(self, ok: bool, out: str):
        if ok:
            root = gitsync.repo_root(cfg.CONFIG_PATH)
            self._show_git("ok", "Committed and pushed", out, gitsync.commit_url(root) if root else None)
        else:
            self._show_git("error", "Committed, but the push failed", out or "git gave no details")

    def _after_write(self, previous: Path | None, old_text: str | None = None):
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
                return  # back where it was: nothing to commit
        self._git_sync(old_text, "save")

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
        try:
            if cfg.ensure_exists():
                self.statusBar().showMessage(f"Started a new config at {cfg.CONFIG_PATH}.", 6000)
        except OSError as e:
            QMessageBox.critical(self, "Actuator", f"Couldn't create {cfg.CONFIG_PATH}\n\n{e}")
            return
        self._watch()
        try:
            self.config = cfg.load()
        except Exception as e:  # noqa: BLE001 - any parse error should be shown, not crash
            self._set_broken(e)
            return
        self._set_broken(None)
        self.dirty = False
        self._reset_history()
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

        def count(payload) -> str:
            if not (isinstance(payload, tuple) and payload[0] in ("global", "app")):
                return ""
            return str(len(self._bindings(*payload)))

        def entry(text, icon, payload, parent=None):
            item = QTreeWidgetItem([text, count(payload)])
            item.setForeground(1, self.palette().placeholderText())
            item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            item.setToolTip(1, "Bindings")
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
                items[("app", app)] = entry(apps_mod.display_name(app), apps_mod.icon_name(app), ("app", app), f)
            f.setExpanded(folder not in collapsed)
            f.setIcon(0, QIcon.fromTheme("folder-open" if f.isExpanded() else "folder"))
        for app in apps:
            if not self.config.folder_of(app):
                items[("app", app)] = entry(apps_mod.display_name(app), apps_mod.icon_name(app), ("app", app))
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
            menu.addAction(QIcon.fromTheme("list-remove"), f"Remove {apps_mod.display_name(app)}…",
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
        dialog = AddAppDialog(self, list(self.config.folders), self.config.apps)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        app, folder = self.config.same_app(cfg.app_key(dialog.value())), dialog.folder()

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
            self, "Actuator", f"Remove {apps_mod.display_name(app)}? {detail}",
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
        pause._release()  # a recording on the page being replaced must not leave shortcuts paused
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
                i = tabs.addTab(page, f"{title} ({len(list(page.rows()))})")
                tabs.tabBar().setTabData(i, title)
        # Stay on the same device tab when switching apps, and across restarts.
        last_tab = self.settings.value("last/tab", "Mouse", type=str)
        tabs.setCurrentIndex(next((i for i in range(tabs.count()) if tabs.tabBar().tabData(i) == last_tab), 0))
        tabs.currentChanged.connect(lambda i: self.settings.setValue("last/tab", tabs.tabBar().tabData(i)))
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
        page.editor.show_conditions = self.feature("only_if")
        page._selected(page.tree.currentItem(), None)  # the first row was shown before this was set
        page.editor.changed.connect(changed)
        page.editor.structure_changed.connect(lambda: self._refresh())
        page.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        page.tree.customContextMenuRequested.connect(lambda pos: self._binding_menu(page, pos))
        QShortcut(QKeySequence.StandardKey.Copy, page.tree, lambda: self._copy_binding(page.tree.currentItem()))
        QShortcut(QKeySequence.StandardKey.Paste, page.tree, self._paste_here)
        QShortcut(QKeySequence.StandardKey.Delete, page.tree,
                  lambda: page.editor.binding and page.editor._delete())

    # Copy and paste ---------------------------------------------------------

    def _targets(self, exclude) -> list[tuple[str, str | None, str]]:
        out = [("global", None, "Global")] + [("app", a, apps_mod.display_name(a)) for a in self.config.apps]
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
        if item and item.flags() & Qt.ItemFlag.ItemIsEditable:
            menu.addSeparator()
            menu.addAction(QIcon.fromTheme("edit-rename"), "Rename…", lambda: page.tree.editItem(item, 0))
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
        dialog = AddAppDialog(self, list(self.config.folders), self.config.apps)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        app, folder = self.config.same_app(cfg.app_key(dialog.value())), dialog.folder()

        def change(c):
            if app not in c.apps:
                c.add_app(app)
            if folder:
                c.move_to_folder(app, folder)
        if self.apply_now(change):
            self._copy_into(items, "app", app)

    def _copy_into(self, items, scope: str, app: str | None):
        where = apps_mod.display_name(app) if app else "Global"
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
            item.setToolTip(0, f"Sends {d.button_label(btn.src)}. Double-click or right-click to rename.")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        if gestures:
            if both:
                page.add_group("Gestures")
            for b in gestures:
                title = d.gesture_title(b.data)
                page.add_row([title, d.summary(b)], {"title": f"{title} in {where}", "binding": b})
        add = QPushButton(QIcon.fromTheme("media-record"), "Add button binding")
        add.setToolTip("Press the mouse button you want to bind")
        add.clicked.connect(lambda: self.add_button(scope, app))
        page.add_button(add)
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
        if trigger_type == "stroke":  # a stroke binding is useless without a recorded shape
            stroke = record_stroke(self)
            if not stroke:
                return
            data["strokes"] = [stroke]
        self.config.add_binding(section, data)
        self._refresh(lambda p: p.get("binding") is not None and p["binding"].data is data)

    def add_button(self, scope, app):
        """Learn a new button by pressing it, then bind it here."""
        bound_here = {x.button for x in self._bindings(scope, app)}
        dialog = CaptureDialog(self, [b for b in self.config.buttons if b.src not in bound_here])
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
