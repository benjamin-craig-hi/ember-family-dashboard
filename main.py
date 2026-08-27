import json
import os
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from ollama import Client

MODEL = os.environ.get("OLLAMA_MODEL", "qwen3:8b-q8_0")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
_client = Client(host=OLLAMA_HOST)
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
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
        chores.append({"title": args.get("title", ""), "day": args.get("day", "")})
        _save("chores.json", chores)
    elif name == "add_calendar_event":
        events = _load("calendar.json", [])
        events.append({"title": args.get("title", ""), "day": args.get("day", ""), "time": args.get("time", "")})
        _save("calendar.json", events)


@app.post("/api/chat")
def api_chat(req: ChatRequest):
    resp = _client.chat(
        model=MODEL,
        messages=[
            {"role": "system", "content": "You are a helpful family dashboard assistant. Answer in one short sentence. No emoji. When the user asks you to add a note, chore, or calendar event, use the appropriate tool to actually save it."},
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
    chores.append(body)
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


app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(os.path.abspath(__file__)), "static"), html=True), name="static")
