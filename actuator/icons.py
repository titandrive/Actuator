"""Lucide icons (bundled in icons/, ISC license), tinted to the theme's text color or a given color.

Actuator asks for icons by their freedesktop names ("document-save"); those map to Lucide.
Names that aren't mapped (an app's own icon, like "firefox") still come from the system theme.
"""

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, QStandardPaths, Qt
from PySide6.QtGui import QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QMessageBox

ICON_DIR = Path(__file__).parent / "icons"

THEME_TO_LUCIDE = {
    "list-add": "plus", "edit-delete": "trash-2", "list-remove": "trash-2",
    "text-x-script": "file-code", "folder-open": "folder-open", "folder-new": "folder-plus",
    "folder": "folder", "media-record": "circle-dot", "document-edit": "square-pen",
    "edit-undo": "undo-2", "edit-redo": "redo-2", "edit-rename": "pencil", "edit-copy": "copy",
    "edit-paste": "clipboard-paste", "document-revert": "history", "document-open-recent": "history",
    "view-refresh": "refresh-cw", "application-menu": "menu", "open-menu-symbolic": "menu",
    "object-locked": "lock", "object-unlocked": "lock-open", "input-mouse": "mouse",
    "input-keyboard": "keyboard", "input-touchpad": "touchpad", "input-tablet": "tablet",
    "help-about-symbolic": "info", "dialog-information": "info", "go-next": "arrow-right",
    "edit-delete-remove": "x", "edit-clear-all": "rotate-ccw", "draw-freehand": "pencil",
    "document-save": "save", "document-open": "eye", "document-import": "file-input",
    "document-export": "download", "dialog-error": "circle-x", "crosshairs": "crosshair",
    "configure": "settings", "globe": "globe", "media-playback-start": "play",
    "media-playback-pause": "pause", "view-filter": "search", "folder-backup": "archive-restore",
}

SIZES = (16, 22, 24, 32, 48)


def _text_color() -> str:
    app = QApplication.instance()
    return app.palette().color(QPalette.ColorRole.Text).name() if app else "#888888"


@lru_cache(maxsize=None)
def _svg(name: str, color: str, filled: bool) -> bytes:
    text = (ICON_DIR / f"{name}.svg").read_text().replace("currentColor", color)
    if filled:
        text = text.replace('fill="none"', f'fill="{color}"')
    return text.encode()


def _pixmap(name: str, color: str, size: int, filled: bool = False, opacity: float = 1.0) -> QPixmap:
    ratio = 2.0  # crisp on HiDPI screens
    pixmap = QPixmap(int(size * ratio), int(size * ratio))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setOpacity(opacity)
    QSvgRenderer(QByteArray(_svg(name, color, filled))).render(painter, QRectF(pixmap.rect()))
    painter.end()
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


@lru_cache(maxsize=None)
def lucide(name: str, color: str | None = None, filled: bool = False) -> QIcon:
    """A Lucide icon in the given color (the theme's text color by default), with a faded
    version for its disabled state."""
    color = color or _text_color()
    out = QIcon()
    for size in SIZES:
        out.addPixmap(_pixmap(name, color, size, filled), QIcon.Mode.Normal)
        out.addPixmap(_pixmap(name, color, size, filled, opacity=0.35), QIcon.Mode.Disabled)
    return out


def icon(name: str, fallback: QIcon | None = None) -> QIcon:
    """Drop-in for QIcon.fromTheme: Lucide for Actuator's own icons, the theme for everything else."""
    lucide_name = THEME_TO_LUCIDE.get(name, name)
    if (ICON_DIR / f"{lucide_name}.svg").exists():
        return lucide(lucide_name)
    return QIcon.fromTheme(name, fallback) if fallback is not None else QIcon.fromTheme(name)


@lru_cache(maxsize=None)
def img(name: str, color: str | None = None, size: int = 14, filled: bool = False) -> str:
    """An <img> tag for rich text labels (status lines, checklists)."""
    color = color or _text_color()
    cache = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation) or "/tmp")
    cache = cache / "actuator-icons"
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f"{name}-{color.lstrip('#')}-{size}{'-f' if filled else ''}.png"
    if not path.exists():
        _pixmap(name, color, size, filled).save(str(path))
    return f"<img src='{path}' width='{size}' height='{size}' style='vertical-align: middle'>"


# Message boxes: Lucide icons in place of the theme's, which clash with everything else.
MESSAGE_ICONS = {
    QMessageBox.Icon.Question: ("circle-help", None),     # None: the theme's accent color
    QMessageBox.Icon.Information: ("info", None),
    QMessageBox.Icon.Warning: ("triangle-alert", "#e5a50a"),
    QMessageBox.Icon.Critical: ("circle-x", "#d24545"),
}


def _prepare(box: QMessageBox):
    # Plasma otherwise shows its own "native" box, theme icon and all, in place of this one.
    box.setOption(QMessageBox.Option.DontUseNativeDialog, True)
    if box.icon() in MESSAGE_ICONS:
        name, color = MESSAGE_ICONS[box.icon()]
        color = color or box.palette().color(QPalette.ColorRole.Highlight).name()
        box.setIconPixmap(_pixmap(name, color, 40))


def use_lucide_message_boxes(app: QApplication):
    """Every QMessageBox, including the static question()/warning() ones, gets a Lucide icon.
    Done by wrapping QMessageBox's entry points rather than an app-wide event filter, which
    would run Python on every event in the app and make everything lag."""
    original_exec = QMessageBox.exec

    def exec_(box):
        _prepare(box)
        return original_exec(box)
    QMessageBox.exec = exec_

    def static(kind):
        def show(parent, title, text, buttons=QMessageBox.StandardButton.Ok,
                 default=QMessageBox.StandardButton.NoButton):
            box = QMessageBox(kind, title, text, buttons, parent)
            box.setDefaultButton(default)
            return QMessageBox.StandardButton(box.exec())
        return staticmethod(show)
    QMessageBox.question = static(QMessageBox.Icon.Question)
    QMessageBox.information = static(QMessageBox.Icon.Information)
    QMessageBox.warning = static(QMessageBox.Icon.Warning)
    QMessageBox.critical = static(QMessageBox.Icon.Critical)
