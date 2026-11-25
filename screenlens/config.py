"""Settings from .env / environment, plus logging setup."""
import logging
import os
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CAPTURES_DIR = ROOT / "captures"
LOG_FILE = ROOT / "logs" / "app.log"
SYSTEM_PROMPT_FILE = ROOT / "prompts" / "system.md"

THINKING_VALUES = ("off", "minimal", "low", "medium", "high", "default")

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Config:
    api_key: str = field(default="", repr=False)  # repr=False: never print the key
    model: str = "gemini-3.8-flash"
    fast_model: str = "gemini-3.5-flash-lite"
    hotkey: str = "ctrl+shift+space"
    max_tokens: int = 1000
    thinking: str = "minimal"
    temperature: float = 0.3
    send_png: bool = False
    save_captures: bool = False


def _flag(raw):
    return raw.lower() in ("1", "true", "yes", "on")


def _thinking(raw):
    if raw.lower() not in THINKING_VALUES:
        raise ValueError(raw)
    return raw.lower()


def _positive_int(raw):
    value = int(raw)
    if value <= 0:
        raise ValueError(raw)
    return value


def parse_config(env: Mapping[str, str]) -> Config:
    """Build a Config. Bad values fall back to the default with a warning."""

    def get(name, default, cast=str):
        raw = env.get(name, "").strip()
        if not raw:
            return default
        try:
            return cast(raw)
        except ValueError:
            log.warning("Invalid %s=%r, using %r", name, raw, default)
            return default

    return Config(
        api_key=env.get("GEMINI_API_KEY", "").strip(),
        model=get("MODEL", Config.model),
        fast_model=get("FAST_MODEL", Config.fast_model),
        hotkey=get("HOTKEY", Config.hotkey).lower(),
        max_tokens=get("MAX_TOKENS", Config.max_tokens, _positive_int),
        thinking=get("THINKING", Config.thinking, _thinking),
        temperature=get("TEMPERATURE", Config.temperature, float),
        send_png=get("SEND_PNG", False, _flag),
        save_captures=get("SAVE_CAPTURES", False, _flag),
    )


def load_config() -> Config:
    # utf-8-sig: Notepad may save .env with a BOM, which would corrupt the first key.
    load_dotenv(ROOT / ".env", encoding="utf-8-sig")
    return parse_config(os.environ)


class RedactingFormatter(logging.Formatter):
    """Removes the API key from anything logged, tracebacks included."""

    secret = ""

    def format(self, record):
        text = super().format(record)
        return text.replace(self.secret, "[redacted]") if self.secret else text


def setup_logging() -> RedactingFormatter:
    LOG_FILE.parent.mkdir(exist_ok=True)
    formatter = RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handlers = [
        RotatingFileHandler(LOG_FILE, maxBytes=500_000, backupCount=3, encoding="utf-8"),
        logging.StreamHandler(),
    ]
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in handlers:
        handler.setFormatter(formatter)
        root.addHandler(handler)
    # Keep SDK/HTTP internals quiet: they could log request contents.
    for noisy in ("google_genai", "httpx", "httpx2", "httpcore", "httpcore2", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return formatter
