# Ember Family Dashboard

> *Light from within the home.*

A self-hosted, wall-mounted family dashboard with a voice assistant. Take an old
unused touchscreen laptop or tablet, point it at the wall, and turn it into the
heart of the home — no cloud, no subscription, no lock-in.

Runs on a CPU-only Linux kiosk (no GPU, no telephony, no cloud). Wake word → mic
→ STT → LLM → TTS, plus a touch-friendly on-screen keyboard and a full family
board: calendar, chores, notes, star-powered rewards, live weather, and a
rotating notification feed.

Everything runs on-device. The only optional network hops are the LLM (which can
be offloaded to a GPU workstation on the LAN via `OLLAMA_HOST`), the weather
(Open-Meteo, no API key), and an optional RSS news feed.

## Features

### Family board
- **Month-view calendar** as the main pane (takes ~3/4 of the screen), with
  day/week/month navigation and a "today" highlight.
- **Chores** — checkable tasks with star values and a colored assignee chip per
  family member.
- **Notes** — a shared scratchpad.
- **Chat box** — type a request ("Ask Ember…") and the assistant can add notes,
  chores, and calendar events via tool calling.

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
  in the header. Sourced from Open-Meteo (no API key). Location is configured in
  settings.
- **Live clock** — 12h or 24h, with three date formats.
- **Notification feed** — a rotating one-line pill that cycles through upcoming
  calendar events (next 7 days), an optional RSS news feed, and (soon) email.
  Rotation speed is configurable.

### Settings (⚙️)
- **Family members** — add, rename, recolor (color picker), and delete. Colors
  flow through to chore chips and the reward picker.
- **Location** — auto-geocoded for weather.
- **Date & time** — 12h/24h, three date formats, °F/°C.
- **Assistant** — name and wake word.
- **Notification feed** — toggle calendar/news/email, set the news URL, set
  rotation speed.
- **Calendar connections** — add/remove connections (Google Calendar, iCloud,
  Outlook, CalDAV, iCal feed) with URLs. *(Config layer only — actual sync is a
  later phase.)*

### Voice assistant
- **"Hey Jarvis"** wake word, then speak a command. It can add notes, chores,
  and calendar events by voice, and speaks back as **Ember**.
- The assistant name and TTS voice are read from the shared settings, so
  renaming it in the UI flows through to the voice.

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

## Files

- `main.py` — FastAPI dashboard (board + chat + tool calling + weather +
  notifications + settings + rewards)
- `voice_assistant.py` — the standalone voice loop (wake → VAD → STT → LLM → TTS)
- `static/index.html` — the dashboard UI (calendar, chores, notes, rewards,
  weather, clock, notification feed, settings, on-screen keyboard)
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

> The weather and notification feed use only the Python standard library
> (`urllib`, `re`, `json`) — no extra dependencies.

### 3. Configure the LLM

```bash
cp .env.example .env
# edit .env to point OLLAMA_HOST at your Ollama instance
```

Both `main.py` and `voice_assistant.py` auto-load `.env` from their own
directory (a tiny stdlib loader — no extra dependency). Values in `.env` are
read into the environment but **never override** a variable that's already set,
so a systemd `Environment=` line or a real shell export still wins.

The dashboard defaults to `http://localhost:11434`. To offload the model to a
GPU box on the LAN, set `OLLAMA_HOST` to that machine's address and make sure
its Ollama binds to the network (`OLLAMA_HOST=0.0.0.0:11434`).

`.env` is git-ignored, so each machine keeps its own copy and repo updates
never clobber it.

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
  planned for a later phase (it needs custom wake-word training).
- First wake-word trigger after boot is slow (model warm-up); later ones are
  snappy.
- Kokoro downloads `en-core-web-sm` (spaCy) and the 82M model on first use.
- The `af_heart` TTS voice is female/warm; `am_michael`/`am_adam` are male.
- Runtime data (calendar, chores, notes, family, rewards, milestones, settings)
  lives in `data/` and is git-ignored — family data is never committed.

## License

MIT — see [LICENSE](LICENSE).
