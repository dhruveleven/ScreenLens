"""Global hotkey. Callbacks run on the `keyboard` thread, so they only emit signals."""
import logging

import keyboard
from PyQt6.QtCore import QObject, pyqtSignal

log = logging.getLogger(__name__)


class Hotkeys(QObject):
    triggered = pyqtSignal()
    escape = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._escape_hook = None

    def register(self, combo: str) -> bool:
        try:
            keyboard.add_hotkey(combo, self.triggered.emit)
        except Exception:  # bad combo (ValueError) or the OS refused the hook
            log.exception("Could not register hotkey %r", combo)
            return False
        log.info("Hotkey registered: %s", combo)
        return True

    def watch_escape(self, on: bool):
        """Global Esc while the overlay is up: Windows sometimes refuses it focus.

        suppress=True (emit() returns None, which means "swallow") keeps that Esc
        from also reaching whichever app still has the focus.
        """
        if on and self._escape_hook is None:
            try:
                self._escape_hook = keyboard.on_press_key(
                    "esc", lambda _e: self.escape.emit(), suppress=True)
            except Exception:
                log.exception("Could not watch Esc")
        elif not on and self._escape_hook is not None:
            keyboard.unhook(self._escape_hook)
            self._escape_hook = None

    def stop(self):
        try:
            keyboard.unhook_all()
        except Exception:
            log.exception("Hotkey cleanup failed")
