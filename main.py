import json
import os
import re
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from ollama import Client

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
_client = Client(host=OLLAMA_HOST)
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

app = FastAPI(title="Family Dashboard")


def _load(name, default):
    p = os.path.join(DATA_DIR, name)
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return default


def _save(name, data):
    with open(os.path.join(DATA_DIR, name), "w") as f:
        json.dump(data, f, indent=2)


DEFAULT_FAMILY = [
    {"name": "Mom", "color": "#ff7a1a", "stars": 0},
    {"name": "Dad", "color": "#4a9eff", "stars": 0},
]
DEFAULT_REWARDS = [
    {"title": "Extra screen time", "cost": 10, "claimed": False},
    {"title": "Pick the movie", "cost": 5, "claimed": False},
]
DEFAULT_MILESTONES = [
    {"threshold": 10, "label": "10 stars!", "emoji": "⭐"},
    {"threshold": 25, "label": "25 stars!", "emoji": "🌟"},
    {"threshold": 50, "label": "50 stars!", "emoji": "🏆"},
]

DEFAULT_SETTINGS = {
    "location": "Honolulu, HI",
    "latitude": 21.3069,
    "longitude": -157.8583,
    "time_format": "12h",          # "12h" or "24h"
    "date_format": "weekday",      # "weekday" | "numeric" | "long"
    "temp_unit": "F",              # "F" or "C"
    "assistant_name": "Ember",
    "wake_word": "Hey Jarvis",     # display only; wake-word model change is Phase 2
    "tts_voice": "af_heart",
    # Notification feed
    "notify_calendar": True,
    "notify_news": False,
    "notify_email": False,
    "news_feed_url": "",
    "notify_interval": 10,         # seconds between rotating feed items
    # Calendar connections (configured now; sync is a later phase)
    "calendar_connections": [],    # [{provider, url}]
}


def _load_family():
    return _load("family.json", DEFAULT_FAMILY)


def _load_rewards():
    return _load("rewards.json", DEFAULT_REWARDS)


def _load_milestones():
    return _load("milestones.json", DEFAULT_MILESTONES)


def _load_settings():
    s = dict(DEFAULT_SETTINGS)
    s.update(_load("settings.json", {}))
    return s


def _save_settings(s):
    _save("settings.json", s)


class ChatRequest(BaseModel):
    content: str


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
]


def _run_tool(name, args):
    if name == "add_note":
        notes = _load("notes.json", [])
        notes.append({"text": args.get("text", "")})
        _save("notes.json", notes)
    elif name == "add_chore":
        chores = _load("chores.json", [])
        chores.append({
            "title": args.get("title", ""),
            "day": args.get("day", ""),
            "done": False,
            "stars": int(args.get("stars", 0) or 0),
            "assignee": args.get("assignee", ""),
        })
        _save("chores.json", chores)
    elif name == "complete_chore":
        chores = _load("chores.json", [])
        title = args.get("title", "")
        for c in chores:
            if c.get("title", "").strip().lower() == title.strip().lower() and not c.get("done"):
                c["done"] = True
                stars = int(c.get("stars", 0) or 0)
                assignee = c.get("assignee", "")
                if stars and assignee:
                    family = _load_family()
                    for m in family:
                        if m["name"] == assignee:
                            m["stars"] = int(m.get("stars", 0)) + stars
                            break
                    _save("family.json", family)
                break
        _save("chores.json", chores)
    elif name == "add_calendar_event":
        events = _load("calendar.json", [])
        events.append({"title": args.get("title", ""), "day": args.get("day", ""), "time": args.get("time", "")})
        _save("calendar.json", events)


@app.post("/api/chat")
def api_chat(req: ChatRequest):
    today = datetime.now().strftime("%A, %B %d, %Y")
    resp = _client.chat(
        model=MODEL,
        messages=[
            {"role": "system", "content": f"Today is {today}. You are Ember, the warm, self-hosted family assistant — the light from within the home. You help the family stay organized and connected. Answer in one short sentence, warm and plain-spoken. No emoji. When the user asks you to add a note, chore, or calendar event, use the appropriate tool to actually save it. For calendar events, resolve relative dates (like 'Friday' or 'tomorrow') against today's date ({today}); never default to a past year."},
            {"role": "user", "content": req.content},
        ],
        tools=TOOLS,
        options={"num_ctx": 4096},
    )
    msg = resp.message
    tool_calls = getattr(msg, "tool_calls", None)
    if tool_calls:
        for tc in tool_calls:
            fn = tc.function
            try:
                args = fn.arguments
                if isinstance(args, str):
                    args = json.loads(args)
            except Exception:
                args = {}
            _run_tool(fn.name, args)
        return {"reply": "Done."}
    return {"reply": msg.content}


@app.get("/api/chores")
def get_chores():
    return _load("chores.json", [])


@app.post("/api/chores")
async def add_chore(request: Request):
    body = await request.json()
    chores = _load("chores.json", [])
    body.setdefault("done", False)
    chores.append(body)
    _save("chores.json", chores)
    return {"ok": True}


@app.post("/api/chores/{index}/done")
async def set_chore_done(index: int, request: Request):
    body = await request.json()
    chores = _load("chores.json", [])
    if not (0 <= index < len(chores)):
        return {"ok": False, "error": "index out of range"}
    chore = chores[index]
    new_done = bool(body.get("done", False))
    # Award stars only on the transition from not-done -> done
    if new_done and not chore.get("done"):
        stars = int(chore.get("stars", 0) or 0)
        assignee = chore.get("assignee", "")
        if stars and assignee:
            family = _load_family()
            for m in family:
                if m["name"] == assignee:
                    m["stars"] = int(m.get("stars", 0)) + stars
                    break
            _save("family.json", family)
    chore["done"] = new_done
    _save("chores.json", chores)
    return {"ok": True}


@app.get("/api/notes")
def get_notes():
    return _load("notes.json", [])


@app.post("/api/notes")
async def add_note(request: Request):
    body = await request.json()
    notes = _load("notes.json", [])
    notes.append(body)
    _save("notes.json", notes)
    return {"ok": True}


@app.get("/api/calendar")
def get_calendar():
    return _load("calendar.json", [])


@app.post("/api/calendar")
async def add_calendar_event(request: Request):
    body = await request.json()
    events = _load("calendar.json", [])
    events.append(body)
    _save("calendar.json", events)
    return {"ok": True}


@app.get("/api/family")
def get_family():
    return _load_family()


@app.get("/api/rewards")
def get_rewards():
    return _load_rewards()


@app.post("/api/rewards")
async def add_reward(request: Request):
    body = await request.json()
    rewards = _load_rewards()
    rewards.append({"title": body.get("title", ""), "cost": int(body.get("cost", 0)), "claimed": False})
    _save("rewards.json", rewards)
    return {"ok": True}


@app.post("/api/rewards/{index}/claim")
async def claim_reward(index: int, request: Request):
    body = await request.json()
    assignee = body.get("assignee", "")
    rewards = _load_rewards()
    family = _load_family()
    if not (0 <= index < len(rewards)):
        return {"ok": False, "error": "index out of range"}
    reward = rewards[index]
    cost = int(reward.get("cost", 0))
    member = next((m for m in family if m["name"] == assignee), None)
    if member is None:
        return {"ok": False, "error": "unknown member"}
    if int(member.get("stars", 0)) < cost:
        return {"ok": False, "error": "not enough stars"}
    member["stars"] = int(member.get("stars", 0)) - cost
    reward["claimed"] = True
    _save("family.json", family)
    _save("rewards.json", rewards)
    return {"ok": True}


@app.get("/api/milestones")
def get_milestones():
    return _load_milestones()


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
@app.get("/api/settings")
def get_settings():
    return _load_settings()


@app.put("/api/settings")
async def put_settings(request: Request):
    body = await request.json()
    s = _load_settings()
    for k in DEFAULT_SETTINGS:
        if k in body:
            s[k] = body[k]
    _save_settings(s)
    return s


# ---------------------------------------------------------------------------
# Weather (Open-Meteo, no API key required)
# ---------------------------------------------------------------------------
def _geocode(query):
    url = "https://geocoding-api.open-meteo.com/v1/search?count=1&format=json&name=" + urllib.parse.quote(query)
    with urllib.request.urlopen(url, timeout=8) as r:
        data = json.loads(r.read().decode())
    res = (data.get("results") or [None])[0]
    if not res:
        return None
    return {
        "name": res.get("name"),
        "admin1": res.get("admin1"),
        "country": res.get("country"),
        "latitude": res.get("latitude"),
        "longitude": res.get("longitude"),
    }


@app.get("/api/weather")
def get_weather():
    s = _load_settings()
    lat = s.get("latitude")
    lon = s.get("longitude")
    unit = s.get("temp_unit", "F")
    temp_unit = "fahrenheit" if unit == "F" else "celsius"
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,weather_code,relative_humidity_2m,apparent_temperature"
        f"&daily=temperature_2m_max,temperature_2m_min,weather_code&timezone=auto&temperature_unit={temp_unit}"
    )
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            data = json.loads(r.read().decode())
    except Exception as e:
        return {"ok": False, "error": str(e)}
    cur = data.get("current", {})
    daily = data.get("daily", {})
    return {
        "ok": True,
        "location": s.get("location", ""),
        "temp": cur.get("temperature_2m"),
        "apparent": cur.get("apparent_temperature"),
        "humidity": cur.get("relative_humidity_2m"),
        "code": cur.get("weather_code"),
        "unit": unit,
        "today_high": (daily.get("temperature_2m_max") or [None])[0],
        "today_low": (daily.get("temperature_2m_min") or [None])[0],
    }


@app.post("/api/geocode")
async def geocode(request: Request):
    body = await request.json()
    q = body.get("query", "")
    if not q:
        return {"ok": False, "error": "empty query"}
    try:
        res = _geocode(q)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    if not res:
        return {"ok": False, "error": "location not found"}
    return {"ok": True, "result": res}


# ---------------------------------------------------------------------------
# Family member management
# ---------------------------------------------------------------------------
@app.post("/api/family")
async def add_member(request: Request):
    body = await request.json()
    family = _load_family()
    family.append({
        "name": body.get("name", "").strip(),
        "color": body.get("color", "#ff7a1a"),
        "stars": int(body.get("stars", 0) or 0),
    })
    _save("family.json", family)
    return {"ok": True}


@app.put("/api/family/{index}")
async def update_member(index: int, request: Request):
    body = await request.json()
    family = _load_family()
    if not (0 <= index < len(family)):
        return {"ok": False, "error": "index out of range"}
    m = family[index]
    if "name" in body:
        m["name"] = body["name"].strip()
    if "color" in body:
        m["color"] = body["color"]
    if "stars" in body:
        m["stars"] = int(body["stars"] or 0)
    _save("family.json", family)
    return {"ok": True}


@app.delete("/api/family/{index}")
async def delete_member(index: int):
    family = _load_family()
    if not (0 <= index < len(family)):
        return {"ok": False, "error": "index out of range"}
    family.pop(index)
    _save("family.json", family)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Notification feed (calendar events + optional RSS news)
# ---------------------------------------------------------------------------
def _resolve_day(day_str):
    """Mirror the frontend resolveDay(): free-text day -> date (or None)."""
    s = str(day_str or "").strip().lower()
    if not s:
        return None
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", s)
    if m:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
    today = datetime.now().date()
    if s == "today":
        return today
    if s == "tomorrow":
        return today + timedelta(days=1)
    if s == "yesterday":
        return today - timedelta(days=1)
    days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    if s in days:
        idx = days.index(s)
        diff = idx - today.weekday()
        if diff < 0:
            diff += 7
        return today + timedelta(days=diff)
    return None


def _clean_xml_text(text):
    """Strip CDATA wrappers and any remaining tags from an XML text node."""
    text = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", text, flags=re.S)
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip()


def _fetch_rss(url, limit=5):
    """Fetch an RSS/Atom feed and return a list of {title, link}."""
    req = urllib.request.Request(url, headers={"User-Agent": "Ember/1.0"})
    with urllib.request.urlopen(req, timeout=8) as r:
        xml = r.read().decode("utf-8", "ignore")
    items = re.findall(r"<item>(.*?)</item>", xml, re.S)
    if not items:
        items = re.findall(r"<entry>(.*?)</entry>", xml, re.S)
    out = []
    for it in items:
        t = re.search(r"<title[^>]*>(.*?)</title>", it, re.S)
        l = re.search(r"<link[^>]*href=[\"'](.*?)[\"']", it, re.S) or re.search(r"<link[^>]*>(.*?)</link>", it, re.S)
        if t:
            title = _clean_xml_text(t.group(1))
            if title:
                out.append({
                    "title": title,
                    "link": (l.group(1).strip() if l else ""),
                })
        if len(out) >= limit:
            break
    return out


@app.get("/api/notifications")
def get_notifications():
    s = _load_settings()
    feed = []

    # 1. Upcoming calendar events (next 7 days)
    if s.get("notify_calendar", True):
        events = _load("calendar.json", [])
        today = datetime.now().date()
        for e in events:
            d = _resolve_day(e.get("day", ""))
            if d and today <= d <= today + timedelta(days=7):
                label = "Today" if d == today else ("Tomorrow" if d == today + timedelta(days=1) else d.strftime("%A"))
                feed.append({
                    "type": "calendar",
                    "icon": "📅",
                    "text": f"{label}: {e.get('title', '')}" + (f" at {e['time']}" if e.get("time") else ""),
                })

    # 2. News feed (optional RSS)
    if s.get("notify_news") and s.get("news_feed_url"):
        try:
            for item in _fetch_rss(s["news_feed_url"], 5):
                feed.append({"type": "news", "icon": "📰", "text": item["title"], "link": item["link"]})
        except Exception:
            feed.append({"type": "news", "icon": "📰", "text": "News feed unavailable"})

    # 3. Email notifications (placeholder — no email integration yet)
    if s.get("notify_email"):
        feed.append({"type": "email", "icon": "✉️", "text": "Email notifications coming soon"})

    return {"ok": True, "items": feed, "interval": s.get("notify_interval", 10)}


app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(os.path.abspath(__file__)), "static"), html=True), name="static")
