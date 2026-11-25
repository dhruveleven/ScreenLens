"""Canned responses for --dry-run. Makes no network calls."""
import time

FIRST = """**This is a dry run.** No API call was made; this text only exercises the panel.

What a real answer looks like:

- **What it is:** the thing you circled
- **What it means here:** based on the app around it

```python
print("code blocks render too")
```
"""
FOLLOW_UP = "Canned follow-up. The conversation now has **{turns}** turns."


class DryRunProvider:
    model = "dry-run"

    def stream(self, history):
        text = FIRST if len(history) == 1 else FOLLOW_UP.format(turns=len(history))
        time.sleep(0.6)  # visible "Thinking..." state
        for piece in text.split(" "):
            time.sleep(0.03)
            yield piece + " "
