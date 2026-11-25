import logging
import re
from types import SimpleNamespace

import httpx2
import pytest
from google.genai import errors, types
from PIL import Image

from screenlens.config import RedactingFormatter, parse_config
from screenlens.llm import worker
from screenlens.llm.providers.base import LLMError, TruncatedError
from screenlens.llm.providers.gemini_provider import GeminiProvider, thinking_config, to_llm_error
from screenlens.llm.session import CONTEXT_LABEL, CROP_LABEL, QUESTION, Session

POINTS = [(100, 100), (300, 100), (300, 300), (100, 300), (100, 110)]


def make_session(**kwargs):
    return Session(Image.new("RGB", (1920, 1080), "white"), POINTS, **kwargs)


# --- history -----------------------------------------------------------------

def test_first_turn_order():
    session = make_session()
    session.prepare()
    (turn,) = session.history
    assert turn.role == "user"
    assert [p.text or p.inline_data.mime_type for p in turn.parts] == [
        CONTEXT_LABEL, "image/jpeg", CROP_LABEL, "image/jpeg", QUESTION]


def test_prepare_is_idempotent_for_retries():
    session = make_session()
    session.prepare()
    session.prepare()
    assert len(session.history) == 1


def test_follow_up_keeps_images_and_alternates_roles():
    session = make_session()
    session.prepare()
    session.add_answer("a1")
    session.add_question("q2")
    session.add_answer("a2")
    session.add_question("q3")
    assert [c.role for c in session.history] == ["user", "model", "user", "model", "user"]
    assert sum(1 for p in session.history[0].parts if p.inline_data) == 2
    assert session.history[-1].parts[0].text == "q3"


def test_question_after_failed_first_turn_joins_it():
    session = make_session()
    session.prepare()
    session.add_question("what about this?")
    assert len(session.history) == 1
    assert session.history[0].parts[-1].text == "what about this?"


def test_question_after_failed_follow_up_replaces_it():
    session = make_session()
    session.prepare()
    session.add_answer("a1")
    session.add_question("failed")
    session.add_question("new")
    assert [c.role for c in session.history] == ["user", "model", "user"]
    assert session.history[-1].parts[0].text == "new"


def test_save_captures(tmp_path):
    session = make_session(captures_dir=tmp_path)
    session.prepare()
    session.save_turn("SYS", "answer")
    (folder,) = tmp_path.iterdir()
    assert {f.name for f in folder.iterdir()} == {
        "context.jpg", "crop.jpg", "turn1_prompt.txt", "turn1_response.md"}
    prompt = (folder / "turn1_prompt.txt").read_text(encoding="utf-8")
    assert "SYS" in prompt and QUESTION in prompt


def test_nothing_saved_by_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session = make_session()
    session.prepare()
    session.save_turn("SYS", "answer")
    assert list(tmp_path.iterdir()) == []


# --- worker ------------------------------------------------------------------

class FakeSignals:
    def __init__(self):
        self.events = []
        for name in ("chunk", "done", "failed"):
            emit = lambda sid, text, name=name: self.events.append((name, text))
            setattr(self, name, SimpleNamespace(emit=emit))


class FakeProvider:
    model = "fake"

    def __init__(self, *items):
        self.items = items

    def stream(self, history):
        for item in self.items:
            if isinstance(item, Exception):
                raise item
            if callable(item):
                item()
            else:
                yield item


def run(session, *items):
    signals = FakeSignals()
    worker.run_turn(session, FakeProvider(*items), signals)
    return signals.events


def test_worker_streams_then_records_the_answer():
    session = make_session()
    assert run(session, "Hel", "lo") == [("chunk", "Hel"), ("chunk", "lo"), ("done", "")]
    assert session.history[-1].role == "model"
    assert session.history[-1].parts[0].text == "Hello"


def test_worker_failure_leaves_history_ready_for_retry():
    session = make_session()
    assert run(session, "part", LLMError("boom"))[-1] == ("failed", "boom")
    assert [c.role for c in session.history] == ["user"]
    assert run(session, "ok")[-1] == ("done", "")


def test_worker_truncated_answer_is_kept_with_a_notice():
    session = make_session()
    assert run(session, "part", TruncatedError("cut"))[-1] == ("done", "cut")
    assert session.history[-1].parts[0].text == "part"


def test_worker_unexpected_exception_is_reported():
    assert run(make_session(), ValueError("x"))[-1][0] == "failed"


def test_cancelled_session_goes_silent():
    session = make_session()
    events = run(session, "a", session.cancelled.set, "b")
    assert events == [("chunk", "a")]
    assert len(session.history) == 1


# --- Gemini provider ---------------------------------------------------------

@pytest.mark.parametrize("model, setting, expected", [
    ("gemini-3.8-flash", "minimal", "LOW"),       # documented: minimal is an error
    ("gemini-3.8-flash", "off", "LOW"),
    ("gemini-4-hydrogen", "minimal", "LOW"),
    ("gemini-9-future", "off", "LOW"),
    ("gemini-3.5-flash-lite", "minimal", "MINIMAL"),
    ("gemini-3.6-flash", "off", "MINIMAL"),
    ("gemini-3.8-flash", "high", "HIGH"),
])
def test_thinking_level(model, setting, expected):
    assert thinking_config(model, setting).thinking_level.value == expected


def test_thinking_default_and_25_budgets():
    assert thinking_config("gemini-3.8-flash", "default") is None
    assert thinking_config("gemini-2.5-flash", "off").thinking_budget == 0
    assert thinking_config("gemini-2.5-pro", "off").thinking_budget == 128


def response(text=None, finish=None, thought=False, block=None):
    data = {}
    if text is not None or finish:
        candidate = {}
        if text is not None:
            candidate["content"] = {"role": "model", "parts": [{"text": text, "thought": thought}]}
        if finish:
            candidate["finish_reason"] = finish
        data["candidates"] = [candidate]
    if block:
        data["prompt_feedback"] = {"block_reason": block}
    return types.GenerateContentResponse.model_validate(data)


class FakeModels:
    """Each entry of `scripts` is one call: a list of chunks and/or an exception."""

    def __init__(self, *scripts, available=()):
        self.scripts = list(scripts)
        self.configs = []
        self.available = available

    def generate_content_stream(self, *, model, contents, config):
        self.configs.append(config)
        for item in self.scripts.pop(0):
            if isinstance(item, Exception):
                raise item
            yield item

    def list(self):
        return [types.Model(name=f"models/{n}", supported_actions=["generateContent"])
                for n in self.available]


def provider(*scripts, **kwargs):
    models = FakeModels(*scripts, **kwargs)
    return GeminiProvider("key", "gemini-3.8-flash", "gemini-3.5-flash-lite", "SYS",
                          client=SimpleNamespace(models=models)), models


def api_error(code, message="", status=""):
    return errors.APIError(code, {"error": {"code": code, "message": message, "status": status}})


def test_stream_yields_text_and_skips_thoughts():
    p, models = provider([response("hmm", thought=True), response("A"), response("B", "STOP")])
    assert list(p.stream([])) == ["A", "B"]
    assert models.configs[0].system_instruction == "SYS"
    assert models.configs[0].max_output_tokens == 1000


def test_rejected_parameter_is_retried_without_tuning():
    p, models = provider([api_error(400, "Thinking level MINIMAL is not supported")],
                         [response("ok", "STOP")])
    assert list(p.stream([])) == ["ok"]
    assert models.configs[1].thinking_config is None and models.configs[1].temperature is None


def test_other_400_is_not_retried():
    p, models = provider([api_error(400, "bad image")])
    with pytest.raises(LLMError, match="bad image"):
        list(p.stream([]))
    assert len(models.configs) == 1


@pytest.mark.parametrize("chunks, match", [
    ([response(block="SAFETY")], "blocked"),
    ([response("partial", "SAFETY")], "blocked"),
    ([response(finish="STOP")], "empty response"),
    ([], "empty response"),
    ([response(finish="MAX_TOKENS")], "Raise MAX_TOKENS"),
])
def test_bad_outcomes(chunks, match):
    p, _ = provider(chunks)
    with pytest.raises(LLMError, match=match):
        list(p.stream([]))


def test_truncated_after_text():
    p, _ = provider([response("abc", "MAX_TOKENS")])
    with pytest.raises(TruncatedError):
        list(p.stream([]))


@pytest.mark.parametrize("exc, match", [
    (api_error(429), "Rate limit.*fast model"),
    (api_error(400, "API key not valid"), "rejected the API key"),
    (api_error(403), "rejected the API key"),
    (api_error(404), "not found"),
    (api_error(503), "overloaded.*fast model"),
    (api_error(500), "unavailable"),
    (httpx2.ConnectError("x"), "internet connection"),
    (httpx2.ReadTimeout("x"), "too long"),
    (RuntimeError("x"), "RuntimeError"),
])
def test_error_messages(exc, match):
    assert re.search(match, str(to_llm_error(exc, "m", "fast")))


def test_rate_limit_on_fast_model_does_not_suggest_itself():
    assert "switch" not in str(to_llm_error(api_error(429), "fast", "fast"))


def test_missing_models_logs_available_flash(caplog):
    p, _ = provider(available=["gemini-3.8-flash", "gemini-9-pro"])
    with caplog.at_level(logging.WARNING):
        assert p.missing_models(["gemini-3.8-flash", "gone"]) == ["gone"]
    assert "gemini-3.8-flash" in caplog.text and "gemini-9-pro" not in caplog.text


# --- config ------------------------------------------------------------------

def test_config_defaults_and_bad_values():
    config = parse_config({"MAX_TOKENS": "lots", "THINKING": "??", "SAVE_CAPTURES": "True"})
    assert (config.max_tokens, config.thinking, config.save_captures) == (1000, "minimal", True)
    assert config.model == "gemini-3.8-flash" and config.api_key == ""
    assert parse_config({"MAX_TOKENS": "-5"}).max_tokens == 1000


def test_api_key_is_never_shown():
    assert "s3cret" not in repr(parse_config({"GEMINI_API_KEY": "s3cret"}))
    formatter = RedactingFormatter("%(message)s")
    formatter.secret = "s3cret"
    record = logging.LogRecord("n", logging.INFO, "f", 1, "key=%s", ("s3cret",), None)
    assert formatter.format(record) == "key=[redacted]"
