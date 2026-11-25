"""Fullscreen frozen screenshot on which the user draws a lasso."""
from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QWidget

from screenlens.geometry import is_tiny_stroke

TINT = QColor(0, 0, 0, 70)
STROKE = QColor(255, 40, 40)
STROKE_WIDTH = 4


class Overlay(QWidget):
    finished = pyqtSignal(list, QPoint)  # logical points, global release position
    cancelled = pyqtSignal()

    def __init__(self, screenshot, screen):
        """screenshot: PIL RGB image of `screen` in physical pixels."""
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setGeometry(screen.geometry())
        self._points: list[QPointF] = []
        self._drawing = False
        self._over = False

        width, height = screenshot.size
        raw = screenshot.tobytes()
        image = QImage(raw, width, height, width * 3, QImage.Format.Format_RGB888)
        self._background = QPixmap.fromImage(image)  # copies, so `raw` may be freed
        # Bake the tint in once: cheaper repaints on integrated graphics.
        painter = QPainter(self._background)
        painter.fillRect(self._background.rect(), TINT)
        painter.end()

    def open(self):
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()

    def cancel(self):
        if not self._over:
            self._over = True
            self.close()
            self.cancelled.emit()

    # --- events ---
    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.cancel()

    def closeEvent(self, event):
        # Closed some other way (e.g. Alt+F4): still tell the controller.
        if not self._over:
            self._over = True
            self.cancelled.emit()
        super().closeEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drawing = True
            self._points = [event.position()]
        elif event.button() == Qt.MouseButton.RightButton:
            self.cancel()

    def mouseMoveEvent(self, event):
        if not self._drawing:
            return
        previous, current = self._points[-1], event.position()
        self._points.append(current)
        margin = STROKE_WIDTH + 2
        dirty = QRectF(previous, current).normalized()
        self.update(dirty.adjusted(-margin, -margin, margin, margin).toAlignedRect())

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or not self._drawing or self._over:
            return
        self._drawing = False
        points = [(p.x(), p.y()) for p in self._points]
        if is_tiny_stroke(points):
            self.cancel()
            return
        self._over = True
        release = event.globalPosition().toPoint()
        self.close()
        self.finished.emit(points, release)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setClipRect(event.rect())
        painter.drawPixmap(self.rect(), self._background)
        if len(self._points) > 1:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(
                QPen(STROKE, STROKE_WIDTH, Qt.PenStyle.SolidLine,
                     Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            )
            painter.drawPolyline(self._points)
