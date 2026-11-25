"""Gemini via the google-genai SDK (signatures checked against 3.6.1)."""
import logging
import re
from typing import Iterator

import httpx2  # the HTTP client google-genai 3.x uses; needed for its error types
from google import genai
from google.genai import errors, types

from screenlens.llm.providers.base import LLMError, TruncatedError

log = logging.getLogger(__name__)

TIMEOUT_MS = 60_000

# Gemini 3+ models documented to accept thinking_level="minimal". Others (3.8 Flash,
# Gemini 4, ...) answer 400, so anything not listed here gets "low" instead.
MINIMAL_THINKING_MODELS = (
    "gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.1-flash-lite", "gemini-3-flash",
)
THINKING_BUDGETS_25 = {"minimal": 0, "low": 1024, "medium": 8192, "high": 24576}

BLOCK_REASONS = {
    "SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "RECITATION", "IMAGE_SAFETY",
    "IMAGE_PROHIBITED_CONTENT", "MODEL_ARMOR", "JAILBREAK",
}


def thinking_config(model: str, setting: str) -> types.ThinkingConfig | None:
    """Translate the THINKING setting into what this model accepts."""
    if setting == "default":
        return None
    level = "minimal" if setting == "off" else setting
    if model.startswith("gemini-2.5"):
        budget = THINKING_BUDGETS_25[level]
        if "pro" in model:
            budget = max(128, budget)  # 2.5 Pro cannot turn thinking off
        return types.ThinkingConfig(thinking_budget=budget)
    if level == "minimal" and not model.startswith(MINIMAL_THINKING_MODELS):
        level = "low"
    return types.ThinkingConfig(thinking_level=level.upper())


def to_llm_error(exc: Exception, model: str, fast_model: str) -> LLMError:
    if isinstance(exc, LLMError):
        return exc
    if isinstance(exc, errors.APIError):
        detail = str(exc.message or "")
        tip = "" if model == fast_model else (
            f", or switch to the fast model ({fast_model}) from the tray menu")
        if exc.code == 429:
            return LLMError(f"Rate limit reached for {model}. Wait a minute and retry{tip}.")
        if exc.code in (401, 403) or "api key" in detail.lower():
            return LLMError("Gemini rejected the API key. Check GEMINI_API_KEY in .env, then restart.")
        if exc.code == 404:
            return LLMError(f"Model {model} was not found. Set MODEL in .env (see logs/app.log).")
        if exc.code and exc.code >= 500:
            problem = "is overloaded" if exc.code == 503 else "is unavailable"
            return LLMError(f"{model} {problem} right now (error {exc.code}). "
                            f"Wait a little and retry{tip}.")
        return LLMError(f"Gemini error {exc.code}: {detail[:200]}")
    if isinstance(exc, httpx2.TimeoutException):
        return LLMError("Gemini took too long to respond. Check your connection and retry.")
    if isinstance(exc, httpx2.HTTPError):
        return LLMError("Could not reach Gemini. Check your internet connection and retry.")
    return LLMError(f"Unexpected error ({type(exc).__name__}). See logs/app.log.")


class GeminiProvider:
    def __init__(self, api_key, model, fast_model, system_prompt, max_tokens=1000,
                 temperature=0.3, thinking="minimal", client=None):
        self.model = model
        self._fast_model = fast_model
        self._system_prompt = system_prompt
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._thinking = thinking
        self._client = client or genai.Client(
            api_key=api_key, http_options=types.HttpOptions(timeout=TIMEOUT_MS))

    def _config(self, model, bare=False):
        config = types.GenerateContentConfig(
            system_instruction=self._system_prompt,
            max_output_tokens=self._max_tokens,
        )
        if not bare:
            config.temperature = self._temperature
            config.thinking_config = thinking_config(model, self._thinking)
        return config

    def _chunks(self, model, history):
        """Raw chunks. Retries once without tuning if the model rejects a parameter."""
        try:
            yield from self._client.models.generate_content_stream(
                model=model, contents=history, config=self._config(model))
        except errors.APIError as exc:
            # A 400 is raised before any chunk, so restarting cannot duplicate text.
            if exc.code != 400 or not re.search(
                    r"thinking|temperature|sampling", str(exc.message), re.I):
                raise
            log.warning("%s rejected thinking/temperature, retrying without them", model)
            yield from self._client.models.generate_content_stream(
                model=model, contents=history, config=self._config(model, bare=True))

    def stream(self, history: list[types.Content]) -> Iterator[str]:
        model = self.model  # fixed for this request even if the tray toggles it
        got_text = False
        finish = blocked = usage = None
        try:
            for chunk in self._chunks(model, history):
                if chunk.prompt_feedback and chunk.prompt_feedback.block_reason:
                    blocked = chunk.prompt_feedback.block_reason.name
                usage = chunk.usage_metadata or usage
                candidate = chunk.candidates[0] if chunk.candidates else None
                if candidate is None:
                    continue
                finish = candidate.finish_reason.name if candidate.finish_reason else finish
                for part in (candidate.content and candidate.content.parts) or []:
                    if part.text and not part.thought:
                        got_text = True
                        yield part.text
        except Exception as exc:
            error = to_llm_error(exc, model, self._fast_model)
            # Type and API message only: a traceback could carry request contents.
            log.error("Gemini request failed: %s %s",
                      type(exc).__name__, str(getattr(exc, "message", exc))[:300])
            raise error from None

        thoughts = (usage and usage.thoughts_token_count) or 0
        log.info("model=%s finish=%s input_tokens=%s thought_tokens=%s output_tokens=%s", model,
                 finish, usage and usage.prompt_token_count, thoughts,
                 usage and usage.candidates_token_count)
        self._check_outcome(got_text, finish, blocked, thoughts)

    def _check_outcome(self, got_text, finish, blocked, thoughts):
        reason = blocked or finish
        if reason in BLOCK_REASONS:
            raise LLMError(f"Gemini blocked this response ({reason.lower()}). "
                           "Try circling a different area.")
        if finish == "MAX_TOKENS":
            if got_text:
                raise TruncatedError(f"Answer cut off at MAX_TOKENS={self._max_tokens}.")
            raise LLMError(
                f"The model used the whole MAX_TOKENS={self._max_tokens} budget before answering "
                f"({thoughts} thinking tokens). Raise MAX_TOKENS in .env, then restart.")
        if not got_text:
            raise LLMError(f"Gemini returned an empty response ({reason or 'no reason given'}).")

    def missing_models(self, wanted) -> list[str]:
        """Which of `wanted` this key cannot use. Logs the available Flash models if any."""
        available = {
            m.name.removeprefix("models/") for m in self._client.models.list()
            if m.name and "generateContent" in (m.supported_actions or ["generateContent"])
        }
        missing = [m for m in wanted if m not in available]
        if missing:
            log.warning("Not available: %s. Available Flash models: %s", ", ".join(missing),
                        ", ".join(sorted(m for m in available if "flash" in m)) or "none")
        return missing
