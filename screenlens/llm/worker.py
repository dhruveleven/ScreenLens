"""Runs one model turn off the UI thread and reports back through signals."""
import logging
import threading
import time

from PyQt6.QtCore import QObject, pyqtSignal

from screenlens.llm.providers.base import LLMError, TruncatedError

log = logging.getLogger(__name__)


class StreamSignals(QObject):
    """Every signal carries the session id so the UI can drop stale events."""

    chunk = pyqtSignal(int, str)
    done = pyqtSignal(int, str)    # notice ("" if none)
    failed = pyqtSignal(int, str)  # user-facing message


def start_turn(session, provider, signals, system_prompt=""):
    # Daemon: a request blocked on the network must not keep the app alive on quit.
    threading.Thread(
        target=run_turn, args=(session, provider, signals, system_prompt), daemon=True
    ).start()


def run_turn(session, provider, signals, system_prompt=""):
    pieces = []
    notice = ""
    model = getattr(provider, "model", "?")
    try:
        session.prepare()
        started = time.perf_counter()
        first = None
        stream = provider.stream(session.history)
        try:
            for piece in stream:
                if session.cancelled.is_set():
                    log.info("session=%d cancelled", session.id)
                    return
                if first is None:
                    first = time.perf_counter() - started
                pieces.append(piece)
                signals.chunk.emit(session.id, piece)
        except TruncatedError as exc:
            notice = str(exc)
        finally:
            if hasattr(stream, "close"):
                stream.close()  # drops the HTTP connection if we stopped early
        if session.cancelled.is_set():
            return
        log.info("session=%d model=%s ttft=%.2fs total=%.2fs chars=%d", session.id, model,
                 first or 0, time.perf_counter() - started, sum(map(len, pieces)))
        answer = "".join(pieces)
        session.save_turn(system_prompt, answer)
        session.add_answer(answer)
        signals.done.emit(session.id, notice)
    except LLMError as exc:
        log.warning("session=%d model=%s failed: %s", session.id, model, exc)
        signals.failed.emit(session.id, str(exc))
    except Exception as exc:
        log.exception("session=%d worker crashed", session.id)
        signals.failed.emit(session.id, f"Unexpected error ({type(exc).__name__}). See logs/app.log.")
