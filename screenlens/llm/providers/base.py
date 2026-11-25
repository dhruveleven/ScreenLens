"""Provider interface. A provider turns a conversation into a stream of text."""
from typing import Iterator, Protocol

from google.genai import types


class LLMError(Exception):
    """A failure with a message that is safe and useful to show to the user."""


class TruncatedError(LLMError):
    """The answer hit the token limit; the text streamed so far is still usable."""


class Provider(Protocol):
    model: str

    def stream(self, history: list[types.Content]) -> Iterator[str]:
        """Yield text chunks. Raises LLMError on any failure."""
        ...
