"""Conversation state for one capture."""
import itertools
import logging
import threading
from datetime import datetime

from google.genai import types

from screenlens.capture import EncodedImage, build_images

log = logging.getLogger(__name__)

CONTEXT_LABEL = "Full screen for context, the region the user circled is outlined in red:"
CROP_LABEL = "Zoomed crop of the circled region:"
QUESTION = "What is this and what does it mean here?"

_ids = itertools.count(1)


def _text(text):
    return types.Part.from_text(text=text)


def _image(image: EncodedImage):
    return types.Part.from_bytes(data=image.data, mime_type=image.mime_type)


class Session:
    """Used by one worker at a time; `cancelled` is the only cross-thread state."""

    def __init__(self, screenshot, points, png=False, captures_dir=None):
        """points: lasso in physical pixels. captures_dir: set to save everything."""
        self.id = next(_ids)
        self.cancelled = threading.Event()
        self.history: list[types.Content] = []
        self._screenshot = screenshot
        self._points = points
        self._png = png
        self._save_dir = None
        if captures_dir:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
            self._save_dir = captures_dir / stamp

    def prepare(self):
        """Build the first user turn (runs on the worker: image work takes a moment)."""
        if self.history:
            return
        context, crop = build_images(self._screenshot, self._points, self._png)
        self._screenshot = None  # ~6 MB we no longer need
        self.history.append(types.Content(role="user", parts=[
            _text(CONTEXT_LABEL), _image(context),
            _text(CROP_LABEL), _image(crop),
            _text(QUESTION),
        ]))
        self._save(f"context.{context.extension}", context.data)
        self._save(f"crop.{crop.extension}", crop.data)

    def add_question(self, question: str):
        """Append a follow-up, keeping user/model turns alternating after a failure."""
        if self.history and self.history[-1].role == "user":
            if len(self.history) == 1:  # images never answered: ask alongside them
                self.history[0].parts.append(_text(question))
                return
            self.history.pop()  # replace the follow-up that failed
        self.history.append(types.Content(role="user", parts=[_text(question)]))

    def add_answer(self, text: str):
        self.history.append(types.Content(role="model", parts=[_text(text)]))

    def save_turn(self, system_prompt: str, response: str):
        turn = sum(1 for c in self.history if c.role == "user")
        prompt = "\n".join(p.text or "[image]" for p in self.history[-1].parts)
        if turn == 1:
            prompt = f"[system]\n{system_prompt}\n\n[user]\n{prompt}"
        self._save(f"turn{turn}_prompt.txt", prompt.encode("utf-8"))
        self._save(f"turn{turn}_response.md", response.encode("utf-8"))

    def _save(self, name, data: bytes):
        if self._save_dir is None:
            return
        try:
            self._save_dir.mkdir(parents=True, exist_ok=True)
            (self._save_dir / name).write_bytes(data)
        except OSError:
            log.exception("Could not save capture file %s", name)
