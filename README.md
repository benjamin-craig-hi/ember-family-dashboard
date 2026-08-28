# Ember Family Dashboard

> *Light from within the home.*

A self-hosted, wall-mounted family dashboard with a voice assistant. Take an old
unused touchscreen laptop or tablet, point it at the wall, and turn it into the
heart of the home — no cloud, no subscription, no lock-in.

Runs on a CPU-only Linux kiosk (no GPU, no telephony, no cloud). Wake word → mic
→ STT → LLM → TTS, plus a touch-friendly on-screen keyboard and a full family
board: calendar, chores, notes, star-powered rewards, live weather, a rotating
notification feed, a photo screensaver, shared lists, and meal planning.

Everything runs on-device. The only optional network hops are the LLM, the
weather (Open-Meteo, no API key), and an optional RSS news feed. The LLM can run
three ways — offloaded to a GPU box on the LAN (the default), fully local on the
kiosk, or (soon) a bring-your-own-key cloud API. See
[LLM deployment](#llm-deployment).

## Screenshots

![Ember Family Dashboard](docs/screenshots/dashboard.png)

![Settings](docs/screenshots/settings.png)

## Features

### Family board
- **Month/week/day calendar** as the main pane (takes ~3/4 of the screen), with
  day/week/month navigation, a "today" highlight, and color-coded events.
- **Chores** — checkable tasks with star values and a colored assignee chip per
  family member. Click any chore or event to edit it.
- **Notes** — a shared scratchpad.
- **Chat box** — type a request ("Ask Ember…") and the assistant can add, list,
  and delete notes, chores, and calendar events via tool calling.

### Star-Powered Rewards (Phase 1)
- Chores carry **star values** and an **assignee**.
- Checking off a chore **awards stars** to that member (idempotent — re-checking
  doesn't double-award).
- A **reward store** with per-member star balances and a tap-to-select claim
  picker.
- **Milestone celebrations** — a big emoji pop when a member crosses a star
  threshold (10 / 25 / 50, configurable).

### Top bar
- **Live weather** — current temp, condition icon, and today's high/low, centered
  in the header. Sourced from Open-Meteo (no API key). Day/night-aware icons
  (moon phases after sunset). Location is configured in settings.
- **Live clock** — 12h or 24h, with three date formats.
- **Notification feed** — a rotating pill that cycles through upcoming calendar
  events (next 7 days), RSS news (multi-feed, 35+ presets), and (soon) email.
  Rotation speed is configurable.

### Photo & video screensaver (Phase 3)
- After a configurable idle period (default 5 min), the board fades into a
  full-screen carousel of your photos and videos (images + autoplay-muted
  looping video).
- Upload and delete media from Settings. Media lives in `photos/` (git-ignored).

### Calendar sync & views (Phase 4)
- **iCal feed import** — paste a public calendar feed URL (Google Calendar
  public link, iCloud, Outlook, CalDAV, etc.) and Ember imports the events,
  deduplicating by UID.
- **Month / week / day** views with a one-tap toggle.

### Home management (Phase 5)
- **Shared grocery list** — a dedicated mobile page (`/grocery.html`) any phone
  on the LAN can open, with tap-to-check, add, and delete.
- **Custom lists** — create any named list (packing, wishlist, etc.).
- **Parental PIN lock** — set a 4-digit PIN; opening Settings then requires it.
- **Event countdowns** — named events with a date, showing "X days" / "today".
- **Sleep mode** — set a sleep window; the screen dims during those hours
  (including overnight windows that cross midnight).

### "Calendar Plus" AI (Phase 6)
- **Meal planning** — a dedicated `/meals.html` page with a 7-day editable plan
  and an **✨ Suggest** button that asks the LLM to propose a week of dinners
  (honoring preferences like "no beef, quick meals").
- **Grocery list export** — one tap to share the list via the phone's native
  share sheet, clipboard, or a plain-text download (replaces Instacart).

### Settings (⚙️)
- **Family members** — add, rename, recolor (color picker), and delete. Colors
  flow through to chore chips and the reward picker.
- **Location** — auto-geocoded for weather.
- **Date & time** — 12h/24h, three date formats, °F/°C.
- **Assistant** — name, wake word, and TTS voice (20 Kokoro voices).
- **Notification feed** — toggle calendar/news/email, multi-feed selection,
  rotation speed.
- **Calendar connections** — add/remove connections (Google Calendar, iCloud,
  Outlook, CalDAV, iCal feed) with URLs. *(Config layer + iCal import — full
  two-way sync is a later phase.)*
- **Screensaver** — enable/disable, idle timer, photo manager.
- **Home management** — PIN, sleep window, countdowns, lists.

### Voice assistant
- **"Hey Jarvis"** wake word, then speak a command. It can add, list, and delete
  notes, chores, calendar events, grocery items, and photos; read and set the
  meal plan; import iCal feeds; and adjust settings — all by voice. It speaks
  back as **Ember**.
- The assistant name and TTS voice are read from the shared settings, so
  renaming it in the UI flows through to the voice.
- A **"Hey Ember"** wake word is in training (see [Roadmap](#roadmap)).

### On-screen keyboard
- A built-in touch keyboard, because GNOME's OSK is unreliable with snap
  Chromium in kiosk mode.

### Fully local
- Wake word, speech-to-text, and text-to-speech all run on the kiosk's CPU.

## Architecture

| Component | Choice |
|-----------|--------|
| Dashboard | FastAPI + static HTML/JS |
| LLM | Ollama (`qwen3:8b-q8_0` by default) |
| Wake word | openWakeWord (`hey_jarvis_v0.1`) |
| STT | Moonshine ONNX (`moonshine/tiny`) |
| TTS | Kokoro-82M |
| VAD | Silero VAD |
| Mic capture | `sounddevice` via PipeWire "default" device |
| Weather | Open-Meteo (no API key) |
| News feed | RSS/Atom (stdlib parser) |

## LLM deployment

Ember's chat and voice features need an LLM. There are three ways to run it,
depending on your hardware.

### A. LAN offload (the default setup)

The kiosk is a low-power, CPU-only machine, so the model runs on a **second
computer on your network** — a GPU workstation — and the kiosk talks to it over
the LAN. This is the setup the project was built around: the kiosk stays cheap
and quiet, and the heavy lifting happens on a machine that already has a GPU.

```bash
# on the GPU box: bind Ollama to the network
OLLAMA_HOST=0.0.0.0:11434 ollama serve

# on the kiosk: point at the GPU box
OLLAMA_HOST=http://<gpu-box-ip>:11434
```

> **Dependency note:** in this mode the dashboard and voice assistant depend on
> that second machine being on and reachable. If it's off, chat and voice
> commands won't get a response — but the rest of the board (calendar, chores,
> notes, weather) keeps working.

### B. Fully local (single machine)

If your kiosk hardware can handle it, you can run Ollama directly on the same
machine and skip the second computer entirely. Install Ollama on the kiosk and
leave `OLLAMA_HOST` at the default `http://localhost:11434`. A smaller model
(e.g. `qwen3:4b` or `llama3.2:3b`) keeps it responsive on modest hardware.

### C. Bring-your-own-key (BYOK) cloud — *in the works*

For anyone who'd rather not run a model locally at all, a BYOK option is
planned: plug in an API key for whatever provider you like (OpenAI, Anthropic,
Google, Mistral, OpenRouter, etc.) and Ember will route chat and voice to that
cloud API instead of a local model. This is not implemented yet — it's on the
roadmap.

## Roadmap

Shipped (Phases 1–6, self-contained parts):

- ✅ **Phase 1 — Star-Powered Rewards**
- ✅ **Phase 2 — Motivation Mode** (milestone celebrations, settings UI, top
  bar, notification feed, voice/wake-name selector)
- ✅ **Phase 3 — Photo & Video Screensaver**
- ✅ **Phase 4 — Calendar Sync & Views** (iCal import + month/week/day)
- ✅ **Phase 5 — Home Management Extras** (grocery + custom lists, PIN lock,
  countdowns, sleep mode)
- ✅ **Phase 6 — "Calendar Plus" AI** (meal planning + grocery export)

In progress / TODO:

- 🚧 **"Hey Ember" wake word** — custom wake-word model in training; will replace
  "Hey Jarvis" once deployed.
- 🚧 **BYOK cloud API** — bring-your-own-key routing for chat/voice.
- ⬜ **Two-way calendar sync** — Google OAuth, iCloud/CalDAV, and Outlook/Graph
  push/pull (needs per-provider credentials).
- ⬜ **Email notification feed** — surface inbox items in the rotating feed
  (needs email account credentials).
- ⬜ **Magic Import** — forward a flyer/PDF/email and auto-populate events
  (needs email ingestion + a vision/PDF model).
- ⬜ **AI recipe bank** — snap a photo of a recipe and auto-categorize it (needs
  a vision model).
- ⬜ **Syncthing photo sync** — watch a Syncthing folder so photos appear in the
  screensaver automatically (the upload endpoint is the current fallback).

## Files

- `main.py` — FastAPI dashboard (board + chat + tool calling + weather +
  notifications + settings + rewards + photos + lists + meals + iCal import)
- `voice_assistant.py` — the standalone voice loop (wake → VAD → STT → LLM → TTS)
- `static/index.html` — the dashboard UI (calendar, chores, notes, rewards,
  weather, clock, notification feed, settings, on-screen keyboard)
- `static/grocery.html` — mobile grocery list page
- `static/meals.html` — meal planning page
- `jarvis-voice.service` — systemd **user** service for the voice loop
- `record_wakeword.py` — records clips to train a custom wake word (optional)
- `docs/plans/` — roadmap and phase plans

## Setup

### 1. System packages

```bash
sudo apt-get install -y libportaudio2 alsa-utils pulseaudio-utils
```

### 2. Python dependencies

```bash
python3 -m venv venv && source venv/bin/activate
pip install fastapi uvicorn ollama
pip install onnxruntime numpy soundfile scipy sounddevice
pip install kokoro openwakeword silero-vad
pip install useful-moonshine-onnx   # NOT "moonshine" and NOT "useful-moonshine"

# CPU-only torch stack (the naive install pulls broken CUDA builds)
pip install --force-reinstall \
    torch==2.4.1 torchvision==0.19.1 torchaudio==2.4.1 \
    --index-url https://download.pytorch.org/whl/cpu

pip install "transformers==4.46.3" "huggingface-hub>=1.5.0,<2.0" "tokenizers>=0.22.0,<=0.23.0"
```

> The weather, notification feed, photo upload, and iCal parser use only the
> Python standard library (`urllib`, `re`, `json`) — no extra dependencies.

### 3. Configure the LLM

```bash
cp .env.example .env
# edit .env to point OLLAMA_HOST at your Ollama instance
```

Both `main.py` and `voice_assistant.py` auto-load `.env` from their own
directory (a tiny stdlib loader — no extra dependency). Values in `.env` are
read into the environment but **never override** a variable that's already set,
so a systemd `Environment=` line or a real shell export still wins.

`.env` is git-ignored, so each machine keeps its own copy and repo updates
never clobber it.

See [LLM deployment](#llm-deployment) for the three ways to run the model
(LAN offload, fully local, or BYOK cloud).

### 4. Run

```bash
# dashboard (port 8000)
uvicorn main:app --host 0.0.0.0 --port 8000

# voice assistant (separate process)
python voice_assistant.py
```

### 5. Run the voice loop as a service (optional)

```bash
mkdir -p ~/.config/systemd/user
cp jarvis-voice.service ~/.config/systemd/user/
# edit the paths in the service file to match your install
loginctl enable-linger "$USER"
systemctl --user daemon-reload
systemctl --user enable --now jarvis-voice.service
```

## Notes

- The wake word is "Hey Jarvis" (not bare "Jarvis"). A "Hey Ember" wake word is
  in training (see [Roadmap](#roadmap)).
- First wake-word trigger after boot is slow (model warm-up); later ones are
  snappy.
- Kokoro downloads `en-core-web-sm` (spaCy) and the 82M model on first use.
- The `af_heart` TTS voice is female/warm; `am_michael`/`am_adam` are male.
- Runtime data (calendar, chores, notes, family, rewards, milestones, settings,
  lists, meals) lives in `data/` and is git-ignored — family data is never
  committed. Photos/videos live in `photos/` and are also git-ignored.

## License

MIT — see [LICENSE](LICENSE).
