"""System tray icon and notifications."""
from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon


def make_icon():
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(255, 60, 60), 9))
    painter.drawEllipse(QRectF(8, 8, 48, 48))
    painter.end()
    return QIcon(pixmap)


class Tray(QSystemTrayIcon):
    fast_toggled = pyqtSignal(bool)
    quit_requested = pyqtSignal()

    def __init__(self, hotkey, fast_model):
        super().__init__(make_icon())
        self.setToolTip(f"ScreenLens ({hotkey})")
        self._menu = QMenu()  # keep a reference: the tray does not own it
        self._fast = self._menu.addAction(f"Use fast model ({fast_model})")
        self._fast.setCheckable(True)
        self._fast.toggled.connect(self.fast_toggled)
        self._menu.addAction("Settings", lambda: self.notify(
            "Settings", "Edit the .env file and restart ScreenLens."))
        self._menu.addSeparator()
        self._menu.addAction("Quit", self.quit_requested.emit)
        self.setContextMenu(self._menu)

    def notify(self, title, message, warning=False):
        icon = (QSystemTrayIcon.MessageIcon.Warning if warning
                else QSystemTrayIcon.MessageIcon.Information)
        self.showMessage(f"ScreenLens: {title}", message, icon, 8000)
