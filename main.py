import json
import os
from datetime import datetime
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


def _load_family():
    return _load("family.json", DEFAULT_FAMILY)


def _load_rewards():
    return _load("rewards.json", DEFAULT_REWARDS)


def _load_milestones():
    return _load("milestones.json", DEFAULT_MILESTONES)


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
            {"role": "system", "content": f"Today is {today}. You are a helpful family dashboard assistant. Answer in one short sentence. No emoji. When the user asks you to add a note, chore, or calendar event, use the appropriate tool to actually save it. For calendar events, resolve relative dates (like 'Friday' or 'tomorrow') against today's date ({today}); never default to a past year."},
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


app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(os.path.abspath(__file__)), "static"), html=True), name="static")
