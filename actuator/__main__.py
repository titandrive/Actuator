import sys

from PySide6.QtWidgets import QApplication

from . import icons
from .ui import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Actuator")
    app.setDesktopFileName("actuator")
    icons.use_lucide_message_boxes(app)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
