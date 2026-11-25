# ScreenLens

Press a hotkey, circle anything on your screen, and Gemini explains what it is and
what it means in context. Follow-up questions work in the same panel. Windows only.

## Setup

Needs Python 3.11 or 3.12.

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env
```

Get a free key: open <https://aistudio.google.com/api-keys>, sign in, click
**Create API key**, and paste it into `.env` as `GEMINI_API_KEY=...`.

```powershell
.venv\Scripts\python -m screenlens            # real
.venv\Scripts\python -m screenlens --dry-run  # canned answers, no API calls
```

A red ring appears in the system tray (maybe behind the `^` arrow).

> **Privacy.** Every capture uploads your *whole* primary screen, not only the circled
> part. Google states that free-tier content is used to improve its products; paid-tier
> content is not. Do not capture anything sensitive on a free key.

## Use

1. Press `ctrl+shift+space`. The screen freezes.
2. Draw around something with the left mouse button.
3. Read the answer; type follow-ups and press Enter.

Esc or right-click cancels the overlay. Esc or the close button closes the panel. Drag the
panel by its header. Pressing the hotkey again starts over. The tray menu has
**Use fast model**, Settings (stub) and Quit.

## Configuration (`.env`, restart to apply)

| Key | Default | Notes |
|---|---|---|
| `GEMINI_API_KEY` | | required unless `--dry-run` |
| `MODEL` | `gemini-3.8-flash` | checked against your key at startup |
| `FAST_MODEL` | `gemini-3.5-flash-lite` | switch from the tray |
| `HOTKEY` | `ctrl+shift+space` | `keyboard` library syntax |
| `MAX_TOKENS` | `1000` | includes thinking tokens; raise to ~2000 if answers get cut off |
| `THINKING` | `minimal` | `off`/`minimal` = lowest the model supports; `low`, `medium`, `high`, `default` |
| `TEMPERATURE` | `0.3` | ignored by Gemini 3.6+ and 3.5 Flash-Lite |
| `SEND_PNG` | `false` | PNG instead of JPEG q85 |
| `SAVE_CAPTURES` | `false` | writes images, prompt and response to `captures/<timestamp>/` |

The system prompt is `prompts/system.md`. Logs are in `logs/app.log` and include per-request
latency (`ttft` = time to first token):

```powershell
Select-String ttft logs\app.log
```

## Verifying each part by hand

1. **Hotkey, overlay, lasso:** run with `--dry-run`, press the hotkey, draw. Try Esc before
   and during a drag, a single click, and mashing the hotkey.
2. **Capture:** set `SAVE_CAPTURES=true` and check `crop.jpg` and `context.jpg` in the newest
   `captures/` folder. Repeat at 100%, 125% and 150% (Settings > Display > Scale).
3. **Streaming:** add a key, run without `--dry-run`, circle something.
4. **Follow-ups:** ask a question that only makes sense given the image.
5. **Errors:** turn Wi-Fi off and capture (message plus Retry); use a wrong key; set
   `MODEL=nope` (tray notification, available Flash models in the log).

Tests: `.venv\Scripts\python -m pip install -r requirements-dev.txt`, then
`.venv\Scripts\python -m pytest`.

## Layout

```
screenlens/
  main.py       entry point, wiring and flow (Controller)
  config.py     .env, logging, key redaction
  hotkey.py     global hotkey; emits Qt signals only
  geometry.py   pure math: DPI mapping, boxes, panel placement
  capture.py    screenshot, crop, annotate, encode
  ui/           overlay, panel, tray
  llm/session.py    conversation for one capture
  llm/worker.py     runs a turn on a background thread
  llm/providers/    base interface, Gemini, dry run
```

## Known limitations

- Primary monitor only.
- The hotkey does not fire while an administrator window has focus, unless ScreenLens also
  runs as administrator.
- The hotkey is not swallowed, so the focused app sees it too. `ctrl+shift+space` is also a
  shortcut in VS Code (parameter hints) and Windows Terminal (new tab menu); change `HOTKEY`
  if that is annoying.
- Taking the screenshot takes about 0.25 s on integrated graphics.
- DRM-protected video is captured as black.
- If **Smart App Control** blocks a PyQt6 file ("An Application Control policy has blocked
  this file"), use the exact versions in `requirements.txt`. Newer PyQt6 builds may not have
  earned its trust yet.

## v2 roadmap

- Multi-monitor (`capture.grab` already takes a monitor; `Overlay` takes a screen)
- OCR / UI Automation grounding (more parts in `Session.prepare`)
- Guided steps with on-screen highlights
- More providers (implement `stream(history)` in `llm/providers/`)
- Settings window, packaging / installer
