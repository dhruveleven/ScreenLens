"""Floating answer panel: streamed markdown, follow-up box, errors with Retry."""
import re

from PyQt6.QtCore import QPoint, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import (
    QDesktopServices, QGuiApplication, QKeySequence, QPixmap, QShortcut, QTextDocument,
)
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizeGrip, QTextBrowser, QVBoxLayout,
)

from screenlens.geometry import place_panel

WIDTH, HEIGHT = 440, 400
RENDER_INTERVAL_MS = 60  # re-render at most ~16x/s while streaming

STYLE = """
#panel { background: #1f2126; border: 1px solid #454a54; }
QLabel { color: #aab0bb; font-size: 12px; }
#error { color: #ff9a8f; }
QTextBrowser { background: transparent; color: #e8eaee; border: none; font-size: 14px; }
QLineEdit { background: #2b2e35; color: #e8eaee; border: 1px solid #454a54;
            border-radius: 4px; padding: 6px 8px; font-size: 13px; }
QLineEdit:focus { border-color: #7aa2f7; }
QLineEdit:disabled { color: #6b7079; }
QPushButton { background: #363a43; color: #e8eaee; border: none;
              border-radius: 4px; padding: 5px 12px; font-size: 12px; }
QPushButton:hover { background: #454a54; }
#close { background: transparent; color: #aab0bb; padding: 2px 8px; font-size: 14px; }
#close:hover { color: white; background: #454a54; }
"""


def escape_markdown(text):
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|<>~&])", r"\\\1", text)


class AnswerView(QTextBrowser):
    """Markdown view that never loads resources.

    Model output is influenced by whatever is on screen, so an image path in it
    (file:// or a UNC share) must not make the app touch the disk or network.
    """

    def __init__(self):
        super().__init__()
        self._blank = QPixmap(1, 1)
        self._blank.fill(Qt.GlobalColor.transparent)
        self.setOpenLinks(False)
        self.anchorClicked.connect(self._open_link)
        self.highlighted.connect(lambda url: self.setToolTip(url.toString()))

    def loadResource(self, _type, _url):
        # Must be a valid image: given nothing, Qt opens the path directly instead.
        return self._blank

    @staticmethod
    def _open_link(url: QUrl):
        if url.scheme() in ("http", "https"):
            QDesktopServices.openUrl(url)

    def show_markdown(self, text):
        bar = self.verticalScrollBar()
        old = bar.value()
        follow = old >= bar.maximum() - 20  # keep position if the user scrolled up
        self.document().setMarkdown(
            text,
            QTextDocument.MarkdownFeature.MarkdownDialectGitHub
            | QTextDocument.MarkdownFeature.MarkdownNoHTML,
        )
        bar.setValue(bar.maximum() if follow else old)


class Panel(QFrame):
    asked = pyqtSignal(str)
    retry = pyqtSignal()
    closed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setObjectName("panel")
        self.setWindowTitle("ScreenLens")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setStyleSheet(STYLE)
        self.setMinimumSize(320, 220)
        self._finished = ""  # markdown of completed turns
        self._live = ""      # answer being streamed
        self._drag_from = None

        self.status = QLabel()
        close = QPushButton("✕", objectName="close")
        close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close.clicked.connect(self.close)
        header = QHBoxLayout()
        header.addWidget(self.status, 1)
        header.addWidget(close)

        self.view = AnswerView()
        self.error = QLabel(objectName="error")
        self.error.setWordWrap(True)
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.retry_button = QPushButton("Retry")
        self.retry_button.clicked.connect(self.retry)
        error_row = QHBoxLayout()
        error_row.addWidget(self.error, 1)
        error_row.addWidget(self.retry_button, 0, Qt.AlignmentFlag.AlignTop)

        self.input = QLineEdit(placeholderText="Ask a follow-up and press Enter")
        self.input.returnPressed.connect(self._submit)
        footer = QHBoxLayout()
        footer.addWidget(self.input, 1)
        footer.addWidget(QSizeGrip(self), 0, Qt.AlignmentFlag.AlignBottom)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 8, 8)
        layout.addLayout(header)
        layout.addWidget(self.view, 1)
        layout.addLayout(error_row)
        layout.addLayout(footer)

        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self.close)
        self._render_timer = QTimer(self, singleShot=True, interval=RENDER_INTERVAL_MS)
        self._render_timer.timeout.connect(self._render)

    # --- driven by the controller ---
    def warm_up(self):
        """Load fonts, styles and the native window now; the first show took ~0.5 s."""
        self.view.show_markdown("**a** `b` *c*")
        self._set_state("", error="e", can_retry=True)
        self.winId()
        self.grab()  # renders off-screen
        self.view.clear()

    def open_at(self, anchor: QPoint):
        """Show empty, near anchor (global logical position), fully on-screen."""
        self._finished = ""
        self._live = ""
        self._render()
        self.input.clear()
        screen = QGuiApplication.screenAt(anchor) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        self.resize(min(WIDTH, area.width()), min(HEIGHT, area.height()))
        self.move(*place_panel((anchor.x(), anchor.y()), (self.width(), self.height()),
                               (area.x(), area.y(), area.width(), area.height())))
        self.show()
        self.raise_()
        self.activateWindow()

    def begin(self, question=None):
        """Enter the Thinking state; question is None for the first turn and retries."""
        if question is not None:
            heading = f"---\n\n**You:** {escape_markdown(question)}\n\n"
            self._finished = self._join(self._finished, heading)
        self._live = ""
        self._render()
        self._set_state("Thinking...", busy=True)

    def add_chunk(self, text):
        if not self._live:
            self.status.setText("Answering...")
        self._live += text
        if not self._render_timer.isActive():
            self._render_timer.start()

    def finish(self, notice=""):
        self._finished = self._join(self._finished, self._live)
        self._live = ""
        self._render()
        self._set_state("ScreenLens", error=notice)
        self.input.setFocus()

    def fail(self, message):
        self._render()
        self._set_state("Something went wrong", error=message, can_retry=True)

    # --- internals ---
    @staticmethod
    def _join(a, b):
        return f"{a.rstrip()}\n\n{b}" if a.strip() else b

    def _set_state(self, status, busy=False, error="", can_retry=False):
        self.status.setText(status)
        self.input.setEnabled(not busy)
        self.error.setText(error)
        self.error.setVisible(bool(error))
        self.retry_button.setVisible(can_retry)

    def _render(self):
        self._render_timer.stop()
        self.view.show_markdown(self._join(self._finished, self._live))

    def _submit(self):
        question = self.input.text().strip()
        if question:
            self.input.clear()
            self.asked.emit(question)

    def closeEvent(self, event):
        self._render_timer.stop()
        self.closed.emit()
        super().closeEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_from = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event):
        if self._drag_from is not None:
            self.move(event.globalPosition().toPoint() - self._drag_from)

    def mouseReleaseEvent(self, _event):
        self._drag_from = None
