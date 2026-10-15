import os
import sys

from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from . import icons
from .ui import MainWindow

# One Actuator at a time: several windows editing the same config would undo each other's changes.
SERVER_NAME = f"actuator-{os.getuid()}"


def already_running() -> bool:
    """Ask a running Actuator to come to the front. True if there was one."""
    socket = QLocalSocket()
    socket.connectToServer(SERVER_NAME)
    if not socket.waitForConnected(300):
        return False
    socket.write(b"show")
    socket.waitForBytesWritten(300)
    socket.disconnectFromServer()
    return True


def listen(window: MainWindow) -> QLocalServer:
    server = QLocalServer(window)
    QLocalServer.removeServer(SERVER_NAME)  # left behind if a previous run crashed
    server.listen(SERVER_NAME)

    def bring_to_front():
        server.nextPendingConnection()  # its message only ever means "show yourself"
        window.showNormal() if window.isMinimized() else window.show()
        window.raise_()
        window.activateWindow()
    server.newConnection.connect(bring_to_front)
    return server


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Actuator")
    app.setDesktopFileName("actuator")
    if already_running():
        return 0
    icons.use_lucide_message_boxes(app)
    window = MainWindow()
    listen(window)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
