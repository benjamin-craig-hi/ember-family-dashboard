"""Frigate camera client for Ember.

Talks to the Frigate NVR REST API to answer questions like:
  - "Has anyone come to the door?"
  - "What happened on the porch camera at 3pm?"
  - "Did anything happen yesterday?"

Frigate runs on hotrod at FRIGATE_HOST (default 127.0.0.1:5000, the internal
unauthenticated API -- it is reachable only from the LAN/tailnet, so do NOT
expose that port publicly; use the authenticated port 8971 for remote access).

Design notes:
  * Every function returns plain strings ready to hand to the LLM as a tool
    result. Never raise: the voice loop must not die because Frigate is down.
  * Snapshot bytes are returned base64 so they can be attached to a vision
    model's `images` field.
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

FRIGATE_HOST = os.environ.get("FRIGATE_HOST", "127.0.0.1:5000")
FRIGATE_BASE = os.environ.get("FRIGATE_BASE", f"http://{FRIGATE_HOST}")
HTTP_TIMEOUT = float(os.environ.get("FRIGATE_TIMEOUT", "12"))

# HST is UTC-10 with no DST.
HST = timezone(timedelta(hours=-10))


# --------------------------------------------------------------------------
# low-level helpers
# --------------------------------------------------------------------------
def _get(path: str, timeout: float | None = None):
    """GET a Frigate API path. Returns parsed JSON, bytes, or None on failure."""
    url = FRIGATE_BASE.rstrip("/") + path
    try:
        with urllib.request.urlopen(url, timeout=timeout or HTTP_TIMEOUT) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    except Exception:
        return None
    ctype = ""
    try:
        ctype = r.headers.get("Content-Type", "")
    except Exception:
        pass
    if "json" in ctype or (raw[:1] in (b"{", b"[")):
        try:
            return json.loads(raw.decode())
        except Exception:
            return raw
    return raw


def is_available() -> bool:
    """True if Frigate answers the stats endpoint."""
    return isinstance(_get("/api/stats", timeout=5), dict)


def list_cameras() -> list[str]:
    """Names of cameras configured in Frigate."""
    stats = _get("/api/stats")
    if isinstance(stats, dict):
        return sorted((stats.get("cameras") or {}).keys())
    cfg = _get("/api/config")
    if isinstance(cfg, dict):
        return sorted((cfg.get("cameras") or {}).keys())
    return []


def camera_online(name: str) -> bool | None:
    stats = _get("/api/stats")
    if not isinstance(stats, dict):
        return None
    cam = (stats.get("cameras") or {}).get(name)
    if not cam:
        return None
    return bool(cam.get("camera_fps", 0))


# --------------------------------------------------------------------------
# snapshots (for vision)
# --------------------------------------------------------------------------
def snapshot_b64(camera: str, *, height: int | None = None) -> str | None:
    """Latest frame from a camera as base64 JPEG, for a vision model."""
    q = f"?height={int(height)}" if height else ""
    data = _get(f"/api/{urllib.parse.quote(camera)}/latest.jpg{q}")
    if isinstance(data, (bytes, bytearray)) and len(data) > 500:
        return base64.b64encode(data).decode()
    return None


def snapshot_bytes(camera: str, *, height: int | None = None,
                   attempts: int = 3) -> bytes | None:
    """Latest frame as raw JPEG bytes (for the dashboard panel).

    Retries: Frigate serves latest.jpg from its decoded-frame cache, which can
    momentarily miss on a low-traffic stream (the kiosk webcam, notably) when
    several cameras are grabbed in the same instant. A short retry clears it;
    without this a valid camera reports as unresponsive.
    """
    q = f"?height={int(height)}" if height else ""
    path = f"/api/{urllib.parse.quote(camera)}/latest.jpg{q}"
    for i in range(max(1, attempts)):
        data = _get(path)
        if isinstance(data, (bytes, bytearray)) and len(data) > 500:
            return bytes(data)
        if i < attempts - 1:
            time.sleep(0.6)
    return None


# --------------------------------------------------------------------------
# events
# --------------------------------------------------------------------------
def _fmt_time(ts: float) -> str:
    return datetime.fromtimestamp(ts, HST).strftime("%a %b %-d, %-I:%M %p")


def _ago(ts: float) -> str:
    delta = datetime.now(HST) - datetime.fromtimestamp(ts, HST)
    s = int(delta.total_seconds())
    if s < 60:
        return "just now"
    if s < 3600:
        return f"{s // 60} min ago"
    if s < 86400:
        return f"{s // 3600} hr ago"
    return f"{s // 86400} day(s) ago"


def get_events(camera: str | None = None, *, hours: float = 24,
               label: str | None = None, limit: int = 24) -> list[dict]:
    """Object events from the last `hours`, newest first."""
    after = datetime.now(timezone.utc) - timedelta(hours=hours)
    params = {
        "after": after.timestamp(),
        "limit": str(max(1, min(int(limit), 100))),
        "include_thumbnails": "0",
    }
    if camera:
        params["cameras"] = camera
    if label:
        params["labels"] = label
    events = _get("/api/events?" + urllib.parse.urlencode(params))
    if not isinstance(events, list):
        return []
    out = []
    for e in events:
        if not isinstance(e, dict):
            continue
        out.append({
            "camera": e.get("camera"),
            "label": e.get("label"),
            "start": e.get("start_time"),
            "end": e.get("end_time"),
            "score": round(float(e.get("top_score") or 0), 2),
            "zones": e.get("zones") or [],
            "id": e.get("id"),
            "has_clip": bool(e.get("has_clip")),
        })
    return out


def _describe_events(events: list[dict], window: str) -> str:
    if not events:
        return f"No activity detected {window}."
    by_label: dict[str, int] = {}
    for e in events:
        key = e.get("label") or "object"
        by_label[key] = by_label.get(key, 0) + 1
    summary = ", ".join(f"{n} {lbl}" for lbl, n in sorted(by_label.items(),
                                                         key=lambda x: -x[1]))
    lines = [f"{len(events)} event(s) {window}: {summary}."]
    for e in events[:8]:
        when = _fmt_time(e["start"]) if e.get("start") else "?"
        zone = f" in {', '.join(e['zones'])}" if e.get("zones") else ""
        lines.append(f"- {when} ({_ago(e['start']) if e.get('start') else '?'}): "
                     f"{e.get('label')} on {e.get('camera')}{zone}")
    if len(events) > 8:
        lines.append(f"...and {len(events) - 8} more.")
    return "\n".join(lines)


def activity_summary(camera: str | None = None, *, hours: float = 24,
                     person_only: bool = False) -> str:
    """Human-readable summary of recent activity (tool-result text)."""
    evs = get_events(camera, hours=hours,
                     label="person" if person_only else None)
    span = f"in the last {int(hours)} hour(s)"
    if camera:
        span += f" on {camera}"
    if not is_available():
        return ("The camera system is not responding right now, so I can't "
                "check activity.")
    return _describe_events(evs, span)


def door_check(hours: float = 12) -> str:
    """Was there a person at any door/entry camera?"""
    evs = get_events(None, hours=hours, label="person")
    if not is_available():
        return "The camera system is not responding right now."
    if not evs:
        return f"No person detected at any camera in the last {int(hours)} hour(s)."
    doorish = [e for e in evs
               if any(k in (e.get("camera") or "").lower()
                      for k in ("door", "porch", "front", "entry", "entryway"))]
    target = doorish or evs
    header = (f"Yes - {len(doorish)} person event(s) at an entry camera"
              if doorish else
              f"No entry camera events, but {len(evs)} person event(s) elsewhere")
    return header + f" in the last {int(hours)} hour(s).\n" + _describe_events(target, "")


# --------------------------------------------------------------------------
# vision
# --------------------------------------------------------------------------
def look(camera: str) -> tuple[str | None, str]:
    """Return (base64 jpeg, description-of-status) for a vision call."""
    if not is_available():
        return None, "The camera system is not responding right now."
    cams = list_cameras()
    if camera and camera not in cams:
        close = [c for c in cams if camera.lower() in c.lower()]
        if close:
            camera = close[0]
        elif cams:
            return None, (f"I don't have a camera called '{camera}'. "
                          f"Available: {', '.join(cams)}.")
    img = snapshot_b64(camera, height=720)
    if not img:
        return None, f"I couldn't get a picture from {camera} right now."
    return img, ""


def resolve_camera(spoken: str) -> str | None:
    """Map a spoken camera name onto a real one (tolerant of ASR errors)."""
    cams = list_cameras()
    if not cams:
        return None
    want = (spoken or "").lower().strip()
    if not want:
        return cams[0] if len(cams) == 1 else None
    for c in cams:
        if c.lower() == want:
            return c
    for c in cams:                      # substring either direction
        if want in c.lower() or c.lower() in want:
            return c
    tokens = set(want.replace("_", " ").split())
    for c in cams:
        if tokens & set(c.lower().replace("_", " ").split()):
            return c
    return None


def resolve_camera_list(spoken: str | None = None) -> list[str]:
    cams = list_cameras()
    if not spoken:
        return cams
    one = resolve_camera(spoken)
    return [one] if one else cams
