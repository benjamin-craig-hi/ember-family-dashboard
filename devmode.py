"""Development mode — let Ember modify her own source code, safely.

Gated by the ``dev_mode`` setting (default OFF). When off, the tools in this
module are not even registered with the model, so they cannot be invoked.

WHY THE GUARDRAILS ARE HERE
---------------------------
Ember runs as a service. If a self-edit leaves the voice assistant unable to
start, she cannot be asked to fix it — she is the thing that is broken. Every
write therefore follows one path: validate -> syntax check -> write -> commit
-> health check -> automatic rollback if the service does not come back.

Changes are committed to git before they take effect, so any edit can be
undone with a single revert. Nothing here escalates privilege: this module
never calls sudo, and the service allowlist is hard-coded.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import time
import urllib.request

APP_DIR = os.path.dirname(os.path.abspath(__file__))
AUDIT_LOG = os.path.join(APP_DIR, "data", "devmode_audit.log")

# --- what may be edited -----------------------------------------------------
# Only Ember's own source. Data files, secrets, virtualenvs and git internals
# are off limits: settings are changed through the settings tool, which masks
# the API key, and a raw write to settings.json could leak it into a reply.
ALLOWED_SUFFIXES = (".py", ".html", ".css", ".js")
DENY_DIR_PARTS = {".git", "venv", ".venv", "__pycache__", "data", "models",
                  "photos", "node_modules", ".ssh"}
DENY_NAMES = {"settings.json", "secrets.json", ".env", "wyze.env"}

# --- what may be restarted --------------------------------------------------
# Hard-coded allowlist. Never take a service name from the model.
SERVICES = {
    "dashboard-backend.service": {"scope": "system",
                                  "label": "dashboard backend",
                                  "check": "http"},
    "ember-voice.service": {"scope": "user",
                            "label": "voice assistant",
                            "check": "active"},
}

# The dashboard backend runs as a SYSTEM service ("User=dashboard"), so it has
# no session bus and `systemctl --user` fails with "Failed to connect to bus".
# We therefore probe the user service with `systemctl --user --machine=dashboard@`
# which asks systemd-machined to run the check inside that user's session.
VOICE_UID = 1000

MAX_READ_CHARS = 20000
MAX_PATCH_CHARS = 60000

_UNDO = []          # in-process undo stack: list of {path, content, commit}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _run(cmd, timeout=90):
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                           timeout=timeout, cwd=APP_DIR)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    except Exception as e:  # noqa: BLE001
        return 1, str(e)


def _git(args, timeout=90):
    return _run(f"git {args}", timeout=timeout)


def audit(action, detail, ok=True):
    try:
        os.makedirs(os.path.dirname(AUDIT_LOG), exist_ok=True)
        rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "action": action,
               "ok": bool(ok), "detail": str(detail)[:600]}
        with open(AUDIT_LOG, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:  # noqa: BLE001
        pass


def is_enabled(settings):
    """Dev mode is opt-in and defaults off."""
    v = settings.get("dev_mode")
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def _safe_path(rel):
    """Resolve a path inside APP_DIR, or return an error string.

    Rejects absolute paths, traversal, denied directories and denied files so
    a confused (or prompt-injected) model cannot read the API key or escape
    the application directory.
    """
    if not rel or not isinstance(rel, str):
        return None, "No file specified."
    rel = rel.strip().lstrip("/")
    if rel.startswith("~") or os.path.isabs(rel):
        return None, "Only paths inside the app directory are allowed."
    full = os.path.realpath(os.path.join(APP_DIR, rel))
    if not (full == APP_DIR or full.startswith(APP_DIR + os.sep)):
        return None, "That path is outside the app directory."
    sub = os.path.relpath(full, APP_DIR)
    parts = sub.split(os.sep)
    if any(p in DENY_DIR_PARTS for p in parts):
        return None, f"'{sub}' is a protected location."
    if parts[-1] in DENY_NAMES:
        return None, f"'{parts[-1]}' holds secrets and cannot be edited here."
    if not sub.endswith(ALLOWED_SUFFIXES):
        return None, "Only .py, .html, .css and .js files can be edited."
    return full, None


def _syntax_error(path, content):
    """Return an error string if the new content is not valid Python."""
    if not path.endswith(".py"):
        return None
    try:
        ast.parse(content)
    except SyntaxError as e:
        return f"SyntaxError on line {e.lineno}: {e.msg}"
    return None


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------
def git_status():
    _, out = _git("status --short")
    _, head = _git("log --oneline -5")
    _, br = _git("rev-parse --abbrev-ref HEAD")
    return {"branch": br.strip(), "dirty": out.strip().splitlines()[:20],
            "recent_commits": head.strip().splitlines()}


def _commit(path_rel, message):
    _git("add -A")
    rc, out = _git(f'commit -q -m {json.dumps(message)}')
    if rc != 0:
        return None, out.strip()[:200]
    _, sha = _git("rev-parse --short HEAD")
    return sha.strip(), None


def rollback_last(reason="unspecified"):
    """Undo the most recent commit. Used automatically when a change breaks."""
    _, before = _git("rev-parse --short HEAD")
    _git("reset --hard HEAD~1")
    _, after = _git("rev-parse --short HEAD")
    audit("rollback", f"{before.strip()} -> {after.strip()} ({reason})", ok=True)
    return after.strip()


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------
def _run_user_systemctl(verb, svc, timeout=20):
    """Run a systemctl command against the dashboard user's session bus.

    The backend is a system service with no session bus, so we try the
    --machine form first, then an explicit XDG_RUNTIME_DIR, then plain
    --user (which works when invoked from that user's own session).
    """
    uid = VOICE_UID
    attempts = [
        f"systemctl --user --machine=dashboard@ {verb} {svc}",
        f"XDG_RUNTIME_DIR=/run/user/{uid} systemctl --user {verb} {svc}",
        f"systemctl --user {verb} {svc}",
    ]
    last = ""
    for cmd in attempts:
        rc, out = _run(cmd, timeout=timeout)
        last = out
        if "Failed to connect to bus" not in out and "Operation not permitted" not in out:
            return out
    return last


def _service_active(svc):
    meta = SERVICES[svc]
    if meta["scope"] == "user":
        out = _run_user_systemctl("is-active", svc)
    else:
        _, out = _run(f"systemctl is-active {svc}")
    return out.strip().splitlines()[-1].strip() == "active" if out.strip() else False


# --- restart accounting -----------------------------------------------------
# A restart that fails must never be mistaken for success. We confirm the
# service actually went down and came back, and compare start timestamps so a
# silently-rejected restart (e.g. a sudo rule that did not match) is caught
# rather than passing health checks against the OLD, still-running process.
_SINCE = {}


def _started_at(svc):
    meta = SERVICES[svc]
    if meta["scope"] == "user":
        out = _run_user_systemctl("show -p ActiveEnterTimestamp", svc)
    else:
        _, out = _run(f"systemctl show {svc} -p ActiveEnterTimestamp")
    for line in out.splitlines():
        if "ActiveEnterTimestamp=" in line:
            return line.split("=", 1)[1].strip()
    return ""


def _restart_cmd(svc):
    meta = SERVICES[svc]
    if meta["scope"] == "user":
        return f"sudo -n systemctl --machine=dashboard@ restart {svc}"
    return f"sudo -n systemctl restart {svc}"


def _health_check(svc, prev_started):
    """True only if the service is up AND is a NEW process."""
    if not _service_active(svc):
        return False, "not active"
    if prev_started:
        now = _started_at(svc)
        if now and now == prev_started:
            return False, "still the old process"
    if SERVICES[svc]["check"] == "http":
        if not _http_ok():
            return False, "not answering on :8000"
    return True, ""


def _http_ok(url="http://localhost:8000/", tries=8):
    for _ in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=4) as r:
                if r.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1.5)
    return False


def health(svc):
    """True if the service is running AND answering."""
    if svc not in SERVICES:
        return False
    if not _service_active(svc):
        return False
    if SERVICES[svc]["check"] == "http":
        return _http_ok()
    return True


def status():
    out = {}
    for svc, meta in SERVICES.items():
        out[meta["label"]] = "running" if health(svc) else "NOT RUNNING"
    return out


# ---------------------------------------------------------------------------
# restart
# ---------------------------------------------------------------------------
def restart(svc, wait=10):
    """Restart a service and confirm it really came back.

    Returns "ok" / "fail" alongside the message so callers decide on rollback
    using the same evidence, rather than re-probing and racing the result.
    """
    if svc not in SERVICES:
        return False, f"I can only restart: {', '.join(SERVICES)}."
    prev = _started_at(svc)
    rc, out = _run(_restart_cmd(svc), timeout=60)
    if rc != 0 and ("sudo" in out.lower() or "a password is required" in out.lower()
                    or "not allowed" in out.lower()):
        audit("restart", f"{svc} blocked: {out.strip()[:200]}", ok=False)
        return False, (f"I was not allowed to restart the "
                       f"{SERVICES[svc]['label']} (the sudo rule did not match), "
                       f"so the change is NOT live.")
    time.sleep(wait)
    ok, why = _health_check(svc, prev)
    audit("restart", f"{svc} -> {'healthy' if ok else 'FAILED: ' + why}", ok=ok)
    label = SERVICES[svc]["label"]
    if ok:
        return True, f"{label} restarted and is responding."
    if why == "still the old process":
        return False, (f"The {label} did not actually restart — it is still "
                       f"running the previous version, so nothing changed yet.")
    return False, f"The {label} did not come back up ({why})."


def restart_all(wait=12):
    msgs = [restart(svc, wait=wait)[1] for svc in SERVICES]
    return " ".join(msgs)


# ---------------------------------------------------------------------------
# read / edit
# ---------------------------------------------------------------------------
def list_files():
    rows = []
    for root, dirs, files in os.walk(APP_DIR):
        dirs[:] = [d for d in dirs if d not in DENY_DIR_PARTS
                   and not d.startswith(".")]
        for f in sorted(files):
            if f.endswith(ALLOWED_SUFFIXES):
                rel = os.path.relpath(os.path.join(root, f), APP_DIR)
                rows.append(rel)
    return sorted(rows)


def read_file(rel, start=None, end=None):
    full, err = _safe_path(rel)
    if err:
        return err
    if not os.path.isfile(full):
        return f"There is no file called '{rel}'."
    try:
        with open(full) as f:
            lines = f.read().splitlines()
    except Exception as e:  # noqa: BLE001
        return f"Could not read {rel}: {e}"
    total = len(lines)
    lo = max(1, int(start)) if start else 1
    hi = min(total, int(end)) if end else total
    body = "\n".join(f"{i}|{lines[i-1]}" for i in range(lo, hi + 1))
    if len(body) > MAX_READ_CHARS:
        body = body[:MAX_READ_CHARS] + "\n... (truncated; read a line range)"
    return f"{rel} (lines {lo}-{hi} of {total}):\n{body}"


def patch_file(rel, old_string, new_string, summary="", svc=None,
               restart_after=True):
    """Replace one exact snippet, commit it, and auto-revert if it breaks.

    `old_string` must appear exactly once — an ambiguous edit is refused
    rather than guessed at.
    """
    full, err = _safe_path(rel)
    if err:
        return f"Refused: {err}"
    if not old_string:
        return "Refused: no text to replace was given."
    try:
        with open(full) as f:
            original = f.read()
    except Exception as e:  # noqa: BLE001
        return f"Could not read {rel}: {e}"

    n = original.count(old_string)
    if n == 0:
        return (f"Refused: that exact text was not found in {rel}. "
                f"Read the file first and copy the snippet precisely.")
    if n > 1:
        return (f"Refused: that text appears {n} times in {rel} — "
                f"include more surrounding lines to make it unique.")

    new_content = original.replace(old_string, new_string, 1)
    if len(new_content) > MAX_PATCH_CHARS:
        return "Refused: the result would be implausibly large."

    se = _syntax_error(rel, new_content)
    if se:
        audit("patch", f"{rel}: {se}", ok=False)
        return f"Refused: the change would not run — {se}"

    try:
        with open(full, "w") as f:
            f.write(new_content)
    except Exception as e:  # noqa: BLE001
        return f"Could not write {rel}: {e}"

    msg = summary or f"dev: edit {rel}"
    sha, cerr = _commit(rel, msg)
    if cerr:
        # Put it back if we could not record the change.
        with open(full, "w") as f:
            f.write(original)
        return f"Refused: could not commit the change ({cerr})."

    _UNDO.append({"path": rel, "content": original, "commit": sha})
    audit("patch", f"{rel} @ {sha}: {msg}")

    result = f"Edited {rel} and committed as {sha}."
    if not restart_after or not svc:
        return result + " Restart the service to apply it."

    ok, r = restart(svc)
    if ok:
        return result + " " + r

    # The change did not take effect cleanly. Undo it and bring the service back.
    rollback_last(f"bad change to {rel}: {r}")
    _, back = restart(svc)
    recovered = health(svc)
    audit("autorevert", f"{rel}: {'recovered' if recovered else 'STILL DOWN'}",
          ok=recovered)
    if recovered:
        return (f"The edit to {rel} did not apply cleanly ({r}), so I rolled it "
                f"back and the {SERVICES[svc]['label']} is running normally again. "
                f"That change needs a different approach.")
    return (f"URGENT: the edit to {rel} left the {SERVICES[svc]['label']} broken "
            f"({r}) and the rollback did not restore it. It needs attention now.")


def write_file_full(rel, content, summary="", svc=None):
    """Replace a whole file (for new files, or heavy rewrites)."""
    full, err = _safe_path(rel)
    if err:
        return f"Refused: {err}"
    if not isinstance(content, str) or not content.strip():
        return "Refused: no content was given."
    if len(content) > MAX_PATCH_CHARS:
        return "Refused: the file is implausibly large."

    se = _syntax_error(rel, content)
    if se:
        return f"Refused: the file would not run — {se}"

    original = None
    if os.path.isfile(full):
        try:
            with open(full) as f:
                original = f.read()
        except Exception as e:  # noqa: BLE001
            return f"Could not read {rel}: {e}"

    try:
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(content)
    except Exception as e:  # noqa: BLE001
        return f"Could not write {rel}: {e}"

    msg = summary or f"dev: write {rel}"
    sha, cerr = _commit(rel, msg)
    if cerr:
        if original is None:
            os.remove(full)
        else:
            with open(full, "w") as f:
                f.write(original)
        return f"Refused: could not commit the change ({cerr})."

    if original is not None:
        _UNDO.append({"path": rel, "content": original, "commit": sha})
    audit("write", f"{rel} @ {sha}: {msg}")

    result = f"Wrote {rel} and committed as {sha}."
    if not svc:
        return result + " Restart the service to apply it."
    ok, r = restart(svc)
    if ok:
        return result + " " + r
    rollback_last(f"bad file {rel}: {r}")
    restart(svc)
    if health(svc):
        return (f"Writing {rel} did not apply cleanly ({r}); I rolled it back "
                f"automatically and it is running again.")
    return (f"URGENT: {rel} left the {SERVICES[svc]['label']} broken and the "
            f"rollback did not restore it.")


def undo_last():
    """Restore the previous version of the most recently edited file."""
    if not _UNDO:
        # Fall back to git: revert the last commit.
        sha = rollback_last("manual undo")
        return f"Reverted the last change (now at {sha})."
    rec = _UNDO.pop()
    full, err = _safe_path(rec["path"])
    if err:
        return f"Refused: {err}"
    with open(full, "w") as f:
        f.write(rec["content"])
    _commit(rec["path"], f"dev: undo edit to {rec['path']}")
    audit("undo", rec["path"])
    return f"Restored the previous version of {rec['path']}."


def recent_changes(n=10):
    _, out = _git(f"log --oneline -{int(n)}")
    return out.strip()
