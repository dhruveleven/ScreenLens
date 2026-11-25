"""Entry point: wires tray, hotkey, overlay, panel and provider together."""
import argparse
import logging
import signal
import sys
import tempfile
import threading
from pathlib import Path

from PyQt6.QtCore import QLockFile, QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QApplication

from screenlens.capture import grab
from screenlens.config import CAPTURES_DIR, SYSTEM_PROMPT_FILE, load_config, setup_logging
from screenlens.geometry import to_physical
from screenlens.hotkey import Hotkeys
from screenlens.llm.providers.dry_run import DryRunProvider
from screenlens.llm.providers.gemini_provider import GeminiProvider, to_llm_error
from screenlens.llm.session import Session
from screenlens.llm.worker import StreamSignals, start_turn
from screenlens.ui.overlay import Overlay
from screenlens.ui.panel import Panel
from screenlens.ui.tray import Tray

log = logging.getLogger(__name__)

# Time for the compositor to remove the old panel before we take the screenshot.
PANEL_HIDE_MS = 120
NO_KEY = "GEMINI_API_KEY is missing. Add it to .env (see .env.example), then restart."


class Controller(QObject):
    """Owns the flow: hotkey -> screenshot -> overlay -> session -> panel."""

    warn = pyqtSignal(str, str)  # title, message; emitted from the startup check thread

    def __init__(self, config, provider, system_prompt):
        super().__init__()
        self.config = config
        self.provider = provider  # None when there is no API key
        self.system_prompt = system_prompt
        self.session = None
        self.overlay = None
        self._capture_pending = False

        self.hotkeys = Hotkeys()
        self.hotkeys.triggered.connect(self.begin_capture)
        self.hotkeys.escape.connect(self.cancel_overlay)

        self.panel = Panel()
        self.panel.asked.connect(self.ask)
        self.panel.retry.connect(self.retry)
        self.panel.closed.connect(self.end_session)

        self.signals = StreamSignals()
        self.signals.chunk.connect(self.on_chunk)
        self.signals.done.connect(self.on_done)
        self.signals.failed.connect(self.on_failed)

        self.tray = Tray(config.hotkey, config.fast_model)
        self.tray.fast_toggled.connect(self.use_fast_model)
        self.tray.quit_requested.connect(QApplication.quit)
        self.warn.connect(lambda title, message: self.tray.notify(title, message, warning=True))

    def start(self):
        self.panel.warm_up()
        self.tray.show()
        if not self.hotkeys.register(self.config.hotkey):
            self.warn.emit("Hotkey failed", f"Could not register '{self.config.hotkey}'. "
                                            "Change HOTKEY in .env, then restart.")
        if self.provider is None:
            self.warn.emit("API key missing", NO_KEY)
        elif isinstance(self.provider, GeminiProvider):
            threading.Thread(target=self._check_models, daemon=True).start()

    def _check_models(self):
        """Runs on its own thread: only logs and emits signals."""
        try:
            missing = self.provider.missing_models([self.config.model, self.config.fast_model])
        except Exception as exc:
            error = to_llm_error(exc, self.config.model, self.config.fast_model)
            log.warning("Model check failed: %s", error)
            self.warn.emit("Could not check models", str(error))
            return
        if missing:
            self.warn.emit("Model not available", f"{', '.join(missing)}. Available Flash models "
                                                  "are listed in logs/app.log.")

    # --- capture ---
    def begin_capture(self):
        if self.overlay is not None or self._capture_pending:
            return  # one overlay at a time
        self._capture_pending = True
        was_visible = self.panel.isVisible()
        self.end_session()
        self.panel.hide()
        QTimer.singleShot(PANEL_HIDE_MS if was_visible else 0, self._open_overlay)

    def _open_overlay(self):
        self._capture_pending = False
        try:
            screenshot = grab()
        except Exception:
            log.exception("Screenshot failed")
            self.warn.emit("Capture failed", "Could not take a screenshot. See logs/app.log.")
            return
        screen = QGuiApplication.primaryScreen()
        logical = (screen.geometry().width(), screen.geometry().height())
        self.overlay = Overlay(screenshot, screen)
        self.overlay.finished.connect(
            lambda points, release: self._start_session(screenshot, points, logical, release))
        self.overlay.cancelled.connect(self._overlay_gone)
        self.hotkeys.watch_escape(True)
        self.overlay.open()

    def _overlay_gone(self):
        self.overlay = None
        self.hotkeys.watch_escape(False)

    def cancel_overlay(self):
        if self.overlay is not None:
            self.overlay.cancel()

    def _start_session(self, screenshot, points, logical, release):
        self._overlay_gone()
        physical = to_physical(points, logical, screenshot.size)
        self.session = Session(screenshot, physical, self.config.send_png,
                               CAPTURES_DIR if self.config.save_captures else None)
        self.panel.open_at(release)
        self._run()

    # --- conversation ---
    def ask(self, question):
        if self.session is not None:
            self.session.add_question(question)
            self._run(question)

    def retry(self):
        if self.session is not None:
            self._run()

    def _run(self, question=None):
        self.panel.begin(question)
        if self.provider is None:
            self.panel.fail(NO_KEY)
        else:
            start_turn(self.session, self.provider, self.signals, self.system_prompt)

    def end_session(self):
        if self.session is not None:
            self.session.cancelled.set()
            self.session = None

    def use_fast_model(self, on):
        if isinstance(self.provider, GeminiProvider):
            self.provider.model = self.config.fast_model if on else self.config.model
            log.info("Model switched to %s", self.provider.model)

    # --- worker signals; stale sessions are ignored ---
    def _is_current(self, session_id):
        return self.session is not None and self.session.id == session_id

    def on_chunk(self, session_id, text):
        if self._is_current(session_id):
            self.panel.add_chunk(text)

    def on_done(self, session_id, notice):
        if self._is_current(session_id):
            self.panel.finish(notice)

    def on_failed(self, session_id, message):
        if self._is_current(session_id):
            self.panel.fail(message)


def make_provider(config, system_prompt, dry_run):
    if dry_run:
        return DryRunProvider()
    if not config.api_key:
        return None
    return GeminiProvider(config.api_key, config.model, config.fast_model, system_prompt,
                          config.max_tokens, config.temperature, config.thinking)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="screenlens", description="Circle to explain.")
    parser.add_argument("--dry-run", action="store_true",
                        help="stream a canned answer instead of calling the API")
    args = parser.parse_args(argv)

    formatter = setup_logging()
    config = load_config()
    formatter.secret = config.api_key

    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    signal.signal(signal.SIGINT, signal.SIG_DFL)  # Ctrl+C in the terminal quits

    lock = QLockFile(str(Path(tempfile.gettempdir()) / "screenlens.lock"))
    lock.setStaleLockTime(0)  # a lock held by a dead process is still detected as stale
    if not lock.tryLock(100):
        log.error("ScreenLens is already running")
        return 1

    system_prompt = SYSTEM_PROMPT_FILE.read_text(encoding="utf-8").strip()
    controller = Controller(config, make_provider(config, system_prompt, args.dry_run),
                            system_prompt)
    controller.start()
    log.info("Started: %s", "dry run" if args.dry_run else config)
    try:
        return app.exec()
    finally:
        controller.hotkeys.stop()
        controller.tray.hide()
