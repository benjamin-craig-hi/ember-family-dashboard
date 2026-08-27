# Family Dashboard

A fully-local, wall-mounted family dashboard with a voice assistant. Runs on a
CPU-only Linux kiosk (no GPU, no telephony, no cloud). Wake word → mic → STT →
LLM → TTS, plus a touch-friendly on-screen keyboard and a notes/chores/calendar
board.

Everything runs on-device. The only optional network hop is the LLM, which can
be offloaded to a GPU workstation on the LAN via `OLLAMA_HOST`.

## Features

- **Voice assistant** — "Hey Jarvis" wake word, then speak a command. It can
  add notes, chores, and calendar events by voice.
- **Family board** — a month-view calendar as the main pane, with chores and
  notes stacked in a narrower right-hand column, plus a chat box.
- **On-screen keyboard** — a built-in touch keyboard, because GNOME's OSK is
  unreliable with snap Chromium in kiosk mode.
- **Fully local** — wake word, speech-to-text, and text-to-speech all run on
  the kiosk's CPU.

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

## Files

- `main.py` — FastAPI dashboard (board + chat + tool calling)
- `voice_assistant.py` — the standalone voice loop (wake → VAD → STT → LLM → TTS)
- `static/index.html` — the dashboard UI with the on-screen keyboard
- `jarvis-voice.service` — systemd **user** service for the voice loop
- `record_wakeword.py` — records clips to train a custom wake word (optional)

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

### 3. Configure the LLM

```bash
cp .env.example .env
# edit .env to point OLLAMA_HOST at your Ollama instance
```

The dashboard defaults to `http://localhost:11434`. To offload the model to a
GPU box on the LAN, set `OLLAMA_HOST` to that machine's address and make sure
its Ollama binds to the network (`OLLAMA_HOST=0.0.0.0:11434`).

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

- The wake word is "Hey Jarvis" (not bare "Jarvis").
- First wake-word trigger after boot is slow (model warm-up); later ones are
  snappy.
- Kokoro downloads `en-core-web-sm` (spaCy) and the 82M model on first use.
- The `af_heart` TTS voice is female/warm; `am_michael`/`am_adam` are male.

## License

MIT — see [LICENSE](LICENSE).
