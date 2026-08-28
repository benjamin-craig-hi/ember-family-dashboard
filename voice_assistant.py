#!/usr/bin/env python3
"""
Ember — local voice assistant for the family dashboard.

Pipeline: wake word (openWakeWord) -> record speech (Silero VAD) ->
STT (Moonshine ONNX) -> LLM (Ollama) -> TTS (Kokoro).

Runs standalone as a systemd user service. Audio in/out via PipeWire's
'default' device (auto-resamples the CX20755 codec to 16kHz).

LLM runs on a GPU workstation on the LAN — the kiosk is CPU-only and too slow
for a good model, so the LLM call is offloaded over the LAN. Set OLLAMA_HOST
to point at that box (defaults to localhost).

Wake-word hardening: a debounce (N consecutive frames above threshold),
a higher threshold, and a post-response cooldown so it only answers a
deliberate "Hey Jarvis" and doesn't fire on the word "Jarvis" in
conversation or on its own TTS echo.
"""

import json
import os
import sys
import time
import urllib.request
import urllib.parse
from datetime import datetime

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000
CHUNK = 1280  # 80ms @ 16kHz (openWakeWord default frame)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_dotenv(path):
    """Load KEY=VALUE pairs from a .env file into os.environ (never overrides)."""
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


_load_dotenv(os.path.join(BASE_DIR, ".env"))

MODEL = os.environ.get("OLLAMA_MODEL", "qwen3:8b-q8_0")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
ASSISTANT_NAME = "Ember"

CONFIG_PATH = os.path.join(BASE_DIR, "voice_config.json")
DATA_DIR = os.path.join(BASE_DIR, "data")
PHOTOS_DIR = os.path.join(BASE_DIR, "photos")
BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")


def _load_settings():
    """Read the shared settings.json (written by the dashboard UI)."""
    p = os.path.join(DATA_DIR, "settings.json")
    if os.path.exists(p):
        try:
            with open(p) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_settings(s):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(os.path.join(DATA_DIR, "settings.json"), "w") as f:
        json.dump(s, f, indent=2)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DEFAULTS = {
    "wake_word_model": "hey_jarvis_v0.1",  # base model; "Hey Jarvis"
    "wake_threshold": 0.6,                 # higher = fewer false triggers
    "wake_debounce_frames": 3,             # require N consecutive frames above threshold
    "cooldown_seconds": 5.0,               # ignore wake word for N sec after a response
    "tts_voice": "af_heart",
    "stt_model": "moonshine/tiny",
    "max_speech_seconds": 15.0,
    "silence_seconds": 1.5,
    "custom_verifier": None,  # path to trained .joblib verifier (unused for Jarvis)
    "custom_verifier_threshold": 0.5,
    "announce_ready": True,
}


def load_config():
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                cfg.update(json.load(f))
        except Exception as e:
            print(f"[config] error reading {CONFIG_PATH}: {e}", flush=True)
    return cfg


# ---------------------------------------------------------------------------
# Dashboard data (notes / chores / calendar) — shared with main.py
# ---------------------------------------------------------------------------
DEFAULT_FAMILY = [
    {"name": "Mom", "color": "#ff7a1a", "stars": 0},
    {"name": "Dad", "color": "#4a9eff", "stars": 0},
]

# Tool definitions for the LLM (qwen3:8b-q8_0 supports tool calling)
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "add_note",
            "description": "Add a note to the family dashboard",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The note text"}
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_chore",
            "description": "Add a chore to the family dashboard",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "The chore title"},
                    "day": {"type": "string", "description": "Optional day of the week"},
                    "stars": {"type": "integer", "description": "Optional star value for completing this chore"},
                    "assignee": {"type": "string", "description": "Optional family member the chore is assigned to"},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_chore",
            "description": "Mark a chore as done and award its stars",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "The chore title to complete"}
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_calendar_event",
            "description": "Add an event to the family calendar",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "The event title"},
                    "day": {"type": "string", "description": "The day or date, e.g. 'tomorrow' or 'Monday'"},
                    "time": {"type": "string", "description": "The time, e.g. '5pm'"},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_note",
            "description": "Delete a note from the family dashboard by matching its text",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The note text (or a distinctive part of it) to delete"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_chore",
            "description": "Delete a chore from the family dashboard by matching its title",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "The chore title to delete"},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_calendar_event",
            "description": "Delete an event from the family calendar by matching its title",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "The event title to delete"},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_calendar_events",
            "description": "List upcoming calendar events so you can find the exact title before editing or deleting",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_chores",
            "description": "List current chores so you can find the exact title before editing or deleting",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_notes",
            "description": "List current notes so you can find the exact text before deleting",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_settings",
            "description": "Read the current dashboard settings (assistant name, voice, location, formats, notification toggles)",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_settings",
            "description": "Change one or more dashboard settings. Valid keys: assistant_name, tts_voice, location, time_format (12h/24h), date_format (weekday/numeric/long), temp_unit (F/C), notify_calendar (true/false), notify_news (true/false), notify_interval (seconds), screensaver_enabled (true/false), screensaver_idle_minutes (number).",
            "parameters": {
                "type": "object",
                "properties": {
                    "changes": {"type": "object", "description": "Map of setting key to new value"},
                },
                "required": ["changes"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_photos",
            "description": "List the photos and videos currently in the screensaver",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_photo",
            "description": "Delete a photo or video from the screensaver by matching its filename",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "The photo/video filename (or a distinctive part of it) to delete"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "import_ical",
            "description": "Import calendar events from a public iCal feed URL (e.g. a Google Calendar public link or school calendar)",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The iCal (.ics) feed URL to import"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_grocery_item",
            "description": "Add an item to the shared grocery list",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The grocery item to add"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_grocery",
            "description": "List the current grocery list items",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_grocery_item",
            "description": "Remove an item from the grocery list by matching its text",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The grocery item text (or a distinctive part of it) to remove"},
                },
                "required": ["text"],
            },
        },
    },
]


def _load_json(name, default):
    p = os.path.join(DATA_DIR, name)
    if os.path.exists(p):
        try:
            with open(p) as f:
                return json.load(f)
        except Exception:
            return default
    return default


def _save_json(name, data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(os.path.join(DATA_DIR, name), "w") as f:
        json.dump(data, f, indent=2)


def add_note(text):
    notes = _load_json("notes.json", [])
    notes.append({"text": text})
    _save_json("notes.json", notes)


def add_chore(title, day="", stars=0, assignee=""):
    chores = _load_json("chores.json", [])
    chores.append({"title": title, "day": day, "done": False, "stars": int(stars or 0), "assignee": assignee})
    _save_json("chores.json", chores)


def complete_chore(title):
    chores = _load_json("chores.json", [])
    for c in chores:
        if c.get("title", "").strip().lower() == title.strip().lower() and not c.get("done"):
            c["done"] = True
            stars = int(c.get("stars", 0) or 0)
            assignee = c.get("assignee", "")
            if stars and assignee:
                family = _load_json("family.json", DEFAULT_FAMILY)
                for m in family:
                    if m["name"] == assignee:
                        m["stars"] = int(m.get("stars", 0)) + stars
                        break
                _save_json("family.json", family)
            break
    _save_json("chores.json", chores)


def add_calendar_event(title, day="", time=""):
    events = _load_json("calendar.json", [])
    events.append({"title": title, "day": day, "time": time})
    _save_json("calendar.json", events)


def delete_note(text):
    notes = _load_json("notes.json", [])
    t = text.strip().lower()
    kept = [n for n in notes if t not in n.get("text", "").strip().lower()]
    removed = len(notes) - len(kept)
    _save_json("notes.json", kept)
    return removed


def delete_chore(title):
    chores = _load_json("chores.json", [])
    t = title.strip().lower()
    kept = [c for c in chores if t not in c.get("title", "").strip().lower()]
    removed = len(chores) - len(kept)
    _save_json("chores.json", kept)
    return removed


def delete_calendar_event(title):
    events = _load_json("calendar.json", [])
    t = title.strip().lower()
    kept = [e for e in events if t not in e.get("title", "").strip().lower()]
    removed = len(events) - len(kept)
    _save_json("calendar.json", kept)
    return removed


def list_calendar_events():
    events = _load_json("calendar.json", [])
    return [{"title": e.get("title", ""), "day": e.get("day", ""), "time": e.get("time", "")} for e in events]


def list_chores():
    chores = _load_json("chores.json", [])
    return [{"title": c.get("title", ""), "day": c.get("day", ""), "done": c.get("done", False)} for c in chores]


def list_notes():
    notes = _load_json("notes.json", [])
    return [{"text": n.get("text", "")} for n in notes]


def list_photos():
    """List photos/videos in the screensaver directory."""
    if not os.path.isdir(PHOTOS_DIR):
        return []
    items = []
    for fn in sorted(os.listdir(PHOTOS_DIR)):
        if fn.startswith("."):
            continue
        p = os.path.join(PHOTOS_DIR, fn)
        if os.path.isfile(p):
            items.append({"name": fn, "size": os.path.getsize(p)})
    return items


def delete_photo(name):
    """Delete a photo/video by substring match on filename. Returns count removed."""
    if not os.path.isdir(PHOTOS_DIR):
        return 0
    t = name.strip().lower()
    removed = 0
    for fn in os.listdir(PHOTOS_DIR):
        if t and t in fn.lower():
            try:
                os.remove(os.path.join(PHOTOS_DIR, fn))
                removed += 1
            except OSError:
                pass
    return removed


def import_ical(url):
    """Import events from a public iCal feed via the dashboard backend."""
    url = (url or "").strip()
    if not url:
        return {"ok": False, "error": "no url"}
    try:
        data = json.dumps({"url": url}).encode("utf-8")
        req = urllib.request.Request(
            BACKEND_URL + "/api/calendar/import-ical",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=25) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _load_lists():
    return _load_json("lists.json", {"grocery": [], "custom": []})


def _save_lists(l):
    _save_json("lists.json", l)


def add_grocery_item(text):
    l = _load_lists()
    l.setdefault("grocery", []).append({"text": text, "done": False})
    _save_lists(l)


def list_grocery():
    l = _load_lists()
    return [{"text": it.get("text", ""), "done": it.get("done", False)} for it in l.get("grocery", [])]


def remove_grocery_item(text):
    l = _load_lists()
    t = text.strip().lower()
    items = l.get("grocery", [])
    kept = [it for it in items if t not in it.get("text", "").strip().lower()]
    removed = len(items) - len(kept)
    l["grocery"] = kept
    _save_lists(l)
    return removed


# Valid settings keys the voice assistant may change (mirrors main.py DEFAULT_SETTINGS)
_SETTING_KEYS = {
    "assistant_name", "tts_voice", "location", "time_format", "date_format",
    "temp_unit", "notify_calendar", "notify_news", "notify_email", "notify_interval",
    "screensaver_enabled", "screensaver_idle_minutes",
}


def get_settings():
    return _load_settings()


def update_settings(changes):
    s = _load_settings()
    applied = {}
    for k, v in (changes or {}).items():
        if k in _SETTING_KEYS:
            s[k] = v
            applied[k] = v
    _save_settings(s)
    return applied


def run_tool(name, args):
    if name == "add_note":
        add_note(args.get("text", ""))
        return "added"
    elif name == "add_chore":
        add_chore(args.get("title", ""), args.get("day", ""), args.get("stars", 0), args.get("assignee", ""))
        return "added"
    elif name == "complete_chore":
        complete_chore(args.get("title", ""))
        return "completed"
    elif name == "add_calendar_event":
        add_calendar_event(args.get("title", ""), args.get("day", ""), args.get("time", ""))
        return "added"
    elif name == "delete_note":
        return ("deleted" if delete_note(args.get("text", "")) else "not_found")
    elif name == "delete_chore":
        return ("deleted" if delete_chore(args.get("title", "")) else "not_found")
    elif name == "delete_calendar_event":
        return ("deleted" if delete_calendar_event(args.get("title", "")) else "not_found")
    elif name == "list_calendar_events":
        return json.dumps(list_calendar_events())
    elif name == "list_chores":
        return json.dumps(list_chores())
    elif name == "list_notes":
        return json.dumps(list_notes())
    elif name == "list_photos":
        return json.dumps(list_photos())
    elif name == "delete_photo":
        return ("deleted" if delete_photo(args.get("name", "")) else "not_found")
    elif name == "import_ical":
        return json.dumps(import_ical(args.get("url", "")))
    elif name == "add_grocery_item":
        add_grocery_item(args.get("text", ""))
        return "added"
    elif name == "list_grocery":
        return json.dumps(list_grocery())
    elif name == "remove_grocery_item":
        return ("deleted" if remove_grocery_item(args.get("text", "")) else "not_found")
    elif name == "get_settings":
        return json.dumps(get_settings())
    elif name == "update_settings":
        applied = update_settings(args.get("changes", {}))
        return json.dumps({"updated": applied})
    else:
        print(f"[tool] unknown tool: {name}", flush=True)
        return "unknown"


def confirmation_for(results):
    phrases = {
        "add_note": "I added that to your notes.",
        "add_chore": "I added that to your chores.",
        "complete_chore": "Nice, I marked that chore done.",
        "add_calendar_event": "I added that to your calendar.",
    }
    # results is a list of (name, status) tuples
    parts = []
    for name, status in results:
        if name in phrases and status == "added":
            parts.append(phrases[name])
        elif name == "complete_chore" and status == "completed":
            parts.append(phrases["complete_chore"])
        elif name in ("delete_note", "delete_chore", "delete_calendar_event", "delete_photo", "remove_grocery_item"):
            if status == "deleted":
                parts.append("I deleted that.")
            else:
                parts.append("I couldn't find anything matching that to delete.")
    return " ".join(parts) if parts else "Done."


def handle_tool_calls(msg):
    """Legacy single-round helper — no longer used (ask_llm now loops internally)."""
    tool_calls = getattr(msg, "tool_calls", None)
    if not tool_calls:
        return msg.content or ""
    results = []
    for tc in tool_calls:
        fn = tc.function
        name = fn.name
        try:
            args = fn.arguments
            if isinstance(args, str):
                args = json.loads(args)
        except Exception:
            args = {}
        print(f"[tool] {name}({args})", flush=True)
        status = run_tool(name, args)
        results.append((name, status))
    return confirmation_for(results)


# ---------------------------------------------------------------------------
# Audio device selection
# ---------------------------------------------------------------------------
def select_devices():
    """Pick the PipeWire 'default' device (auto-resamples to 16kHz).

    The raw ALSA hardware device (hw:1,0 CX20755) only supports 48kHz
    natively and rejects 16kHz capture. PipeWire's 'default' device
    resamples transparently, so we use that for both input and output.
    """
    devices = sd.query_devices()
    in_dev = sd.default.device[0]
    out_dev = sd.default.device[1]
    for i, d in enumerate(devices):
        name = d.get("name", "")
        if name.strip() == "default":
            if d.get("max_input_channels", 0) > 0:
                in_dev = i
            if d.get("max_output_channels", 0) > 0:
                out_dev = i
    return in_dev, out_dev


# ---------------------------------------------------------------------------
# TTS (Kokoro)
# ---------------------------------------------------------------------------
_pipeline = None


def _get_pipeline():
    global _pipeline
    if _pipeline is None:
        from kokoro import KPipeline
        _pipeline = KPipeline(lang_code="a")
    return _pipeline


def speak(text, voice="af_heart"):
    try:
        pipeline = _get_pipeline()
        for _, _, audio in pipeline(text, voice=voice):
            sd.play(audio, 24000)
            sd.wait()
            break
    except Exception as e:
        print(f"[tts] {e}", flush=True)


def chime():
    """Play a short two-tone acknowledgment so the user knows Jarvis heard
    the wake word and is listening for a command."""
    try:
        sr = 24000
        t1 = np.linspace(0, 0.12, int(sr * 0.12), False)
        t2 = np.linspace(0, 0.12, int(sr * 0.12), False)
        tone1 = 0.35 * np.sin(2 * np.pi * 880.0 * t1)      # A5
        tone2 = 0.35 * np.sin(2 * np.pi * 1174.66 * t2)    # D6
        audio = np.concatenate([tone1, tone2])
        fade = np.linspace(1.0, 0.0, len(audio))
        audio = audio * fade
        sd.play(audio, sr)
        sd.wait()
    except Exception as e:
        print(f"[chime] {e}", flush=True)


def chime_done():
    """Play a short descending two-tone so the user knows Jarvis finished
    listening and is now thinking. Distinct from the wake chime (which
    ascends)."""
    try:
        sr = 24000
        t1 = np.linspace(0, 0.12, int(sr * 0.12), False)
        t2 = np.linspace(0, 0.12, int(sr * 0.12), False)
        tone1 = 0.35 * np.sin(2 * np.pi * 1174.66 * t1)    # D6
        tone2 = 0.35 * np.sin(2 * np.pi * 880.0 * t2)      # A5
        audio = np.concatenate([tone1, tone2])
        fade = np.linspace(1.0, 0.0, len(audio))
        audio = audio * fade
        sd.play(audio, sr)
        sd.wait()
    except Exception as e:
        print(f"[chime_done] {e}", flush=True)


# ---------------------------------------------------------------------------
# STT (Moonshine ONNX)
# ---------------------------------------------------------------------------
_stt_model = None


def _get_stt_model(name):
    global _stt_model
    if _stt_model is None:
        import moonshine_onnx
        _stt_model = moonshine_onnx.MoonshineOnnxModel(model_name=name)
    return _stt_model


def transcribe(audio_np, model_name="moonshine/tiny"):
    try:
        import moonshine_onnx
        model = _get_stt_model(model_name)
        result = moonshine_onnx.transcribe(audio_np, model=model)
        if isinstance(result, (list, tuple)):
            return " ".join(str(r) for r in result).strip()
        return str(result).strip()
    except Exception as e:
        print(f"[stt] {e}", flush=True)
        return ""


# ---------------------------------------------------------------------------
# LLM (Ollama, optionally offloaded to a GPU box on the LAN)
# ---------------------------------------------------------------------------
_client = None


def _get_client():
    global _client
    if _client is None:
        from ollama import Client
        _client = Client(host=OLLAMA_HOST)
    return _client


def ask_llm(text):
    today = datetime.now().strftime("%A, %B %d, %Y")
    name = _load_settings().get("assistant_name") or ASSISTANT_NAME
    system = (
        f"Today is {today}. You are {name}, the warm, self-hosted family assistant — the light from within the home. "
        "You help the family stay organized and connected. "
        "Answer in one short sentence, in plain spoken language. No emoji, no lists. "
        "When the user asks you to add a note, chore, or calendar event, use the "
        "appropriate tool to actually save it. When the user asks you to delete, remove, or edit "
        "a note, chore, or calendar event, first use the matching list tool to find the exact "
        "title, then use the matching delete tool with that exact title. For calendar events, resolve relative "
        f"dates (like 'Friday' or 'tomorrow') against today's date ({today}); never default to a past year. "
        "When the user asks you to change a setting (like the assistant name, voice, location, time format, "
        "temperature unit, notification toggles, or screensaver), use the update_settings tool. When they ask what a setting "
        "currently is, use the get_settings tool. "
        "When the user asks about the screensaver photos or videos, use the list_photos tool to see what's there, "
        "and delete_photo to remove one (find the exact filename first). "
        "When the user asks to import a calendar from a link or feed, use the import_ical tool with that URL. "
        "When the user asks to add something to the grocery list, use the add_grocery_item tool. "
        "When they ask what's on the grocery list, use list_grocery. "
        "When they ask to remove something from the grocery list, first use list_grocery to find the exact item, "
        "then use remove_grocery_item with that text."
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": text},
    ]
    last_results = []
    for _ in range(4):  # allow up to 4 tool rounds (list -> delete -> confirm)
        resp = _get_client().chat(model=MODEL, messages=messages, tools=TOOLS, options={"num_ctx": 4096})
        msg = resp.message
        tool_calls = getattr(msg, "tool_calls", None)
        if not tool_calls:
            return msg.content or ""
        # Append the assistant's tool-call turn
        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {"function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in tool_calls
            ],
        })
        # Execute each tool and append its result
        last_results = []
        for tc in tool_calls:
            name = tc.function.name
            try:
                args = tc.function.arguments
                if isinstance(args, str):
                    args = json.loads(args)
            except Exception:
                args = {}
            print(f"[tool] {name}({args})", flush=True)
            status = run_tool(name, args)
            last_results.append((name, status))
            messages.append({"role": "tool", "content": str(status), "tool_name": name})
    # If we exhausted rounds, fall back to a confirmation phrase
    return confirmation_for(last_results)


# ---------------------------------------------------------------------------
# VAD (Silero)
# ---------------------------------------------------------------------------
_vad_model = None


def _get_vad():
    global _vad_model
    if _vad_model is None:
        from silero_vad import load_silero_vad
        _vad_model = load_silero_vad()
    return _vad_model


def _int16_to_float32(audio_int16):
    """Convert int16 PCM to float32 in [-1, 1]."""
    return audio_int16.astype(np.float32) / 32768.0


# ---------------------------------------------------------------------------
# Wake word (openWakeWord)
# ---------------------------------------------------------------------------
def build_wake_model(cfg):
    import openwakeword
    from openwakeword import get_pretrained_model_paths

    model_name = cfg["wake_word_model"]

    # Support a direct path to a custom-trained model (e.g. "models/hey_ember.onnx").
    if model_name.endswith(".onnx") and os.path.exists(model_name):
        model_path = model_name
    else:
        paths = get_pretrained_model_paths()
        model_path = None
        for p in paths:
            if model_name in p:
                model_path = p
                break
        if model_path is None:
            raise RuntimeError(f"Wake word model {model_name} not found in {paths}")

    kwargs = {}
    if cfg.get("custom_verifier"):
        base_key = os.path.basename(model_path)[:-5]  # strip .onnx
        kwargs["custom_verifier_models"] = {base_key: cfg["custom_verifier"]}
        kwargs["custom_verifier_threshold"] = cfg["custom_verifier_threshold"]

    oww = openwakeword.Model(wakeword_model_paths=[model_path], **kwargs)
    return oww, os.path.basename(model_path)[:-5]


def main():
    cfg = load_config()
    in_dev, out_dev = select_devices()
    sd.default.device = (in_dev, out_dev)
    sd.default.samplerate = SAMPLE_RATE
    sd.default.channels = 1

    print(f"[init] input device: {in_dev}, output device: {out_dev}", flush=True)
    print(f"[init] wake word model: {cfg['wake_word_model']}", flush=True)
    print(f"[init] wake threshold: {cfg['wake_threshold']}, "
          f"debounce: {cfg['wake_debounce_frames']} frames, "
          f"cooldown: {cfg['cooldown_seconds']}s", flush=True)

    oww, wake_key = build_wake_model(cfg)
    print(f"[init] wake word key: {wake_key}", flush=True)

    # Warm up models (first-use downloads happen here)
    print("[init] warming up STT + TTS + VAD...", flush=True)
    _get_stt_model(cfg["stt_model"])
    _get_pipeline()
    _get_vad()
    print("[init] ready. Listening for wake word...", flush=True)

    if cfg.get("announce_ready", True):
        name = _load_settings().get("assistant_name") or ASSISTANT_NAME
        speak(f"{name} is ready.")

    # One continuous input stream for the whole session (no per-chunk
    # stream open/close, which would create gaps that break wake-word
    # detection).
    stream = sd.InputStream(
        samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=CHUNK
    )
    stream.start()

    from silero_vad import get_speech_timestamps
    import torch

    vad = _get_vad()

    # Wake-word hardening state
    wake_frames = 0
    last_response = 0.0
    debounce = max(1, int(cfg.get("wake_debounce_frames", 3)))
    cooldown = float(cfg.get("cooldown_seconds", 5.0))

    while True:
        try:
            # 1. Listen for wake word (continuous stream)
            chunk, _ = stream.read(CHUNK)
            chunk = chunk.flatten()
            prediction = oww.predict(chunk)
            score = prediction.get(wake_key, 0.0)

            # Debounce: require N consecutive frames above threshold so a
            # single transient spike (or the word "Jarvis" in conversation)
            # doesn't trigger a response.
            if score >= cfg["wake_threshold"]:
                wake_frames += 1
            else:
                wake_frames = 0

            in_cooldown = (time.time() - last_response) < cooldown

            if wake_frames >= debounce and not in_cooldown:
                wake_frames = 0
                print(f"[wake] detected ({score:.2f})", flush=True)

                # Acknowledge the wake word with a short chime so the user
                # knows Jarvis is listening, then record the command.
                chime()
                time.sleep(0.25)  # let the chime tail clear before recording

                # 2. Record speech until silence (VAD)
                speech_chunks = []
                heard_speech = False
                silence_frames = 0
                max_frames = int(cfg["max_speech_seconds"] * SAMPLE_RATE / CHUNK)
                silence_frames_needed = int(cfg["silence_seconds"] * SAMPLE_RATE / CHUNK)

                for _ in range(max_frames):
                    c, _ = stream.read(CHUNK)
                    c = c.flatten()
                    speech_chunks.append(c)

                    # VAD on accumulated audio
                    audio_f32 = _int16_to_float32(np.concatenate(speech_chunks))
                    tensor = torch.from_numpy(audio_f32)
                    ts = get_speech_timestamps(
                        tensor, vad, sampling_rate=SAMPLE_RATE, threshold=0.5
                    )
                    if ts:
                        heard_speech = True
                        last_end = ts[-1]["end"] / SAMPLE_RATE
                        elapsed = len(audio_f32) / SAMPLE_RATE
                        if elapsed - last_end >= cfg["silence_seconds"]:
                            break
                    else:
                        if heard_speech:
                            silence_frames += 1
                            if silence_frames >= silence_frames_needed:
                                break

                if not heard_speech:
                    print("[wake] no speech captured", flush=True)
                    last_response = time.time()
                    continue

                speech = np.concatenate(speech_chunks)
                speech_f32 = _int16_to_float32(speech)

                # Trim to speech region
                tensor = torch.from_numpy(speech_f32)
                ts = get_speech_timestamps(
                    tensor, vad, sampling_rate=SAMPLE_RATE, threshold=0.5
                )
                if ts:
                    start = ts[0]["start"]
                    end = ts[-1]["end"]
                    speech_f32 = speech_f32[start:end]

                if len(speech_f32) < SAMPLE_RATE * 0.3:  # < 0.3s, too short
                    print("[wake] speech too short", flush=True)
                    last_response = time.time()
                    continue

                # Done listening — acknowledge with a descending chime so the
                # user knows Jarvis captured the command and is now thinking.
                chime_done()

                # 3. STT
                text = transcribe(speech_f32, cfg["stt_model"])
                print(f"[stt] {text!r}", flush=True)
                if not text:
                    # False trigger or no intelligible speech — stay silent.
                    print("[wake] no speech, ignoring", flush=True)
                    last_response = time.time()
                    continue

                # 4. LLM (with tool calling)
                print("[llm] thinking...", flush=True)
                reply = ask_llm(text)
                print(f"[llm] {reply!r}", flush=True)

                # 5. TTS
                voice = _load_settings().get("tts_voice") or cfg["tts_voice"]
                speak(reply, voice)
                last_response = time.time()

        except KeyboardInterrupt:
            print("[exit] bye", flush=True)
            break
        except Exception as e:
            print(f"[loop] {e}", flush=True)
            time.sleep(1)

    stream.stop()
    stream.close()


if __name__ == "__main__":
    main()
