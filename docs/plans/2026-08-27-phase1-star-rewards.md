# Ember — Phase 1: Star-Powered Rewards — Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Gamify chores with star values, per-member star balances, milestone celebrations, and a reward store kids can "spend" stars in.

**Architecture:** Extend the existing FastAPI + JSON-file + static HTML stack. Add a `family.json` (members + star balances), `rewards.json` (store), and `milestones.json` (thresholds). Chores gain `stars` and `assignee` fields. Completing a chore awards stars to the assignee; claiming a reward deducts them.

**Tech Stack:** FastAPI (Python), vanilla JS, JSON files. No new dependencies.

**Repo:** `~/career/family-dashboard/` (local) → `github.com/benjamin-craig-hi/family-dashboard` → kiosk clone `~/dashboard-app/`.

---

## Data model (target shapes)

`data/family.json`:
```json
[
  {"name": "Mom", "color": "#ff7a1a", "stars": 0},
  {"name": "Dad", "color": "#4a9eff", "stars": 0},
  {"name": "Kid1", "color": "#7ac74f", "stars": 0}
]
```

`data/rewards.json`:
```json
[
  {"title": "Extra screen time", "cost": 10, "claimed": false},
  {"title": "Pick the movie", "cost": 5, "claimed": false}
]
```

`data/milestones.json`:
```json
[
  {"threshold": 10, "label": "10 stars!", "emoji": "⭐"},
  {"threshold": 25, "label": "25 stars!", "emoji": "🌟"},
  {"threshold": 50, "label": "50 stars!", "emoji": "🏆"}
]
```

`data/chores.json` (extended — existing entries get defaults on load):
```json
[
  {"title": "Take out the trash", "day": "Monday", "done": false, "stars": 2, "assignee": "Kid1"}
]
```

---

## Task 1: Backend — data helpers for family/rewards/milestones

**Objective:** Add load/save helpers and default-seeding for the three new JSON files.

**Files:**
- Modify: `main.py` (after the existing `_load`/`_save` helpers, ~line 49)

**Step 1: Add helpers + seed defaults**

Insert after `_save` (line 49):

```python
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
```

**Step 2: Verify**

Run: `python3 -m py_compile main.py`
Expected: no output (compiles clean).

**Step 3: Commit**

```bash
git add main.py
git commit -m "feat(rewards): add family/rewards/milestones data helpers"
```

---

## Task 2: Backend — award stars on chore completion

**Objective:** When a chore is marked done, award its `stars` to its `assignee` (if set).

**Files:**
- Modify: `main.py` — the `set_chore_done` endpoint (~line 163)

**Step 1: Rewrite `set_chore_done` to award stars**

Replace the existing `set_chore_done` function with:

```python
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
```

**Step 2: Verify**

Run: `python3 -m py_compile main.py`
Expected: no output.

**Step 3: Commit**

```bash
git add main.py
git commit -m "feat(rewards): award stars to assignee on chore completion"
```

---

## Task 3: Backend — rewards + family + milestones endpoints

**Objective:** Expose read endpoints for family/rewards/milestones and a claim-reward endpoint.

**Files:**
- Modify: `main.py` (append before the `app.mount(...)` line, ~line 202)

**Step 1: Add endpoints**

Insert before `app.mount(...)`:

```python
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
```

**Step 2: Verify**

Run: `python3 -m py_compile main.py`
Expected: no output.

**Step 3: Commit**

```bash
git add main.py
git commit -m "feat(rewards): family/rewards/milestones endpoints + claim"
```

---

## Task 4: Backend — extend chore tool + add "complete chore" tool

**Objective:** Let the LLM (chat + voice) set `stars`/`assignee` on new chores and mark a chore complete by title.

**Files:**
- Modify: `main.py` — `TOOLS` list (~line 56) and `_run_tool` (~line 105)

**Step 1: Extend `add_chore` tool schema**

In `TOOLS`, replace the `add_chore` entry's `properties` with:

```python
"properties": {
    "title": {"type": "string", "description": "The chore title"},
    "day": {"type": "string", "description": "Optional day of the week"},
    "stars": {"type": "integer", "description": "Optional star value for completing this chore"},
    "assignee": {"type": "string", "description": "Optional family member the chore is assigned to"},
},
```

**Step 2: Add a `complete_chore` tool**

Add a new tool entry after `add_chore`:

```python
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
```

**Step 3: Update `_run_tool`**

Replace the `add_chore` branch and add a `complete_chore` branch:

```python
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
```

**Step 4: Verify**

Run: `python3 -m py_compile main.py`
Expected: no output.

**Step 5: Commit**

```bash
git add main.py
git commit -m "feat(rewards): chore stars/assignee + complete_chore tool"
```

---

## Task 5: Frontend — render star values + assignee on chores

**Objective:** Show each chore's star count and assignee color chip; keep the checkbox.

**Files:**
- Modify: `static/index.html` — `loadChores()` (~line 338)

**Step 1: Add a `family` cache + color lookup**

Near the top of the dashboard logic (after `let _events = []`), add:

```js
let _family = [];
```

**Step 2: Add `loadFamily()`**

Add a loader (mirrors the others):

```js
async function loadFamily() {
  const r = await fetch('/api/family');
  const d = await r.json();
  _family = d;
}
```

**Step 3: Update `loadChores()` to render stars + assignee**

Replace the `loadChores` body's item template with:

```js
const html = d.map((c, i) => {
  const done = !!c.done;
  const member = _family.find(m => m.name === c.assignee);
  const chip = member ? '<span class="chip" style="background:' + member.color + '">' + esc(member.name) + '</span>' : '';
  const stars = c.stars ? '<span class="stars">' + '⭐'.repeat(Math.min(c.stars, 5)) + ' ' + c.stars + '</span>' : '';
  return '<div class="item chore-item' + (done ? ' done' : '') + '">' +
    '<input type="checkbox" ' + (done ? 'checked' : '') + ' onchange="toggleChore(' + i + ', this.checked)">' +
    '<div class="t">' + esc(c.title || '') + '</div>' +
    chip + stars +
    (c.day ? '<div class="d">' + esc(c.day) + '</div>' : '') +
    '</div>';
}).join('') || '<div class="item"><div class="t">No chores</div></div>';
```

**Step 4: Add CSS for `.chip` and `.stars`**

In the `<style>` block, after the `.chore-item.done .t` rule, add:

```css
.chip { font-size:.7rem; padding:2px 8px; border-radius:10px; color:#111; font-weight:700; flex:none; }
.stars { font-size:.8rem; color:var(--accent); flex:none; }
```

**Step 5: Call `loadFamily()` in `refreshAll()`**

Update `refreshAll` to:

```js
function refreshAll() { loadCalendar(); loadChores(); loadNotes(); loadFamily(); }
```

**Step 6: Verify**

Run: `node --check` on the extracted script (see Task 8 for the extraction command).
Expected: no syntax errors.

**Step 7: Commit**

```bash
git add static/index.html
git commit -m "feat(rewards): render chore stars + assignee chips"
```

---

## Task 6: Frontend — rewards strip (balance, milestones, store)

**Objective:** Add a "Rewards" panel showing each member's star balance, milestone progress, and a claimable reward store.

**Files:**
- Modify: `static/index.html` — add a panel in the right column + JS

**Step 1: Add the rewards panel markup**

In the right column (`.right-col`), add a third pane after Notes:

```html
<div class="col">
  <h2>Rewards</h2>
  <div class="list" id="rewards"></div>
</div>
```

**Step 2: Add `loadRewards()`**

```js
async function loadRewards() {
  const [fam, rew, mil] = await Promise.all([
    fetch('/api/family').then(r => r.json()),
    fetch('/api/rewards').then(r => r.json()),
    fetch('/api/milestones').then(r => r.json()),
  ]);
  const balances = fam.map(m =>
    '<div class="item"><div class="t">' + esc(m.name) + ' — ' + m.stars + ' ⭐</div></div>'
  ).join('');
  const store = rew.map((r, i) =>
    '<div class="item"><div class="t">' + esc(r.title) + ' (' + r.cost + ' ⭐)</div>' +
    (r.claimed ? '<div class="d">claimed</div>' : '<button onclick="claimReward(' + i + ')">Claim</button>') +
    '</div>'
  ).join('');
  document.getElementById('rewards').innerHTML =
    '<div class="d">Balances</div>' + balances +
    '<div class="d" style="margin-top:8px">Reward store</div>' + store;
}
```

**Step 3: Add `claimReward()`**

```js
async function claimReward(i) {
  const assignee = prompt('Who is claiming this reward?');
  if (!assignee) return;
  const r = await fetch('/api/rewards/' + i + '/claim', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ assignee: assignee }),
  });
  const d = await r.json();
  if (!d.ok) alert(d.error || 'Could not claim');
  loadRewards();
}
```

**Step 4: Call `loadRewards()` in `refreshAll()`**

```js
function refreshAll() { loadCalendar(); loadChores(); loadNotes(); loadFamily(); loadRewards(); }
```

**Step 5: Verify**

Run: `node --check` on the extracted script.
Expected: no syntax errors.

**Step 6: Commit**

```bash
git add static/index.html
git commit -m "feat(rewards): rewards panel with balances + store"
```

---

## Task 7: Voice — extend add_chore + complete_chore tools

**Objective:** Mirror the backend tool changes in `voice_assistant.py` so voice commands can set stars/assignee and complete chores.

**Files:**
- Modify: `voice_assistant.py` — `TOOLS` (~line 73), `add_chore` (~line 145), `run_tool` (~line 157), `confirmation_for` (~line 168)

**Step 1: Extend `add_chore` tool schema** (same as Task 4 Step 1)

**Step 2: Add `complete_chore` tool** (same as Task 4 Step 2)

**Step 3: Update `add_chore` function**

```python
def add_chore(title, day="", stars=0, assignee=""):
    chores = _load_json("chores.json", [])
    chores.append({"title": title, "day": day, "done": False, "stars": int(stars or 0), "assignee": assignee})
    _save_json("chores.json", chores)
```

**Step 4: Add `complete_chore` function + wire into `run_tool`**

```python
def complete_chore(title):
    chores = _load_json("chores.json", [])
    for c in chores:
        if c.get("title", "").strip().lower() == title.strip().lower() and not c.get("done"):
            c["done"] = True
            stars = int(c.get("stars", 0) or 0)
            assignee = c.get("assignee", "")
            if stars and assignee:
                family = _load_json("family.json", [])
                for m in family:
                    if m["name"] == assignee:
                        m["stars"] = int(m.get("stars", 0)) + stars
                        break
                _save_json("family.json", family)
            break
    _save_json("chores.json", chores)
```

In `run_tool`, add:

```python
    elif name == "complete_chore":
        complete_chore(args.get("title", ""))
```

**Step 5: Add confirmation phrase**

In `confirmation_for`, add:

```python
        "complete_chore": "Nice, I marked that chore done.",
```

**Step 6: Verify**

Run: `python3 -m py_compile voice_assistant.py`
Expected: no output.

**Step 7: Commit**

```bash
git add voice_assistant.py
git commit -m "feat(rewards): voice complete_chore + chore stars/assignee"
```

---

## Task 8: Verify end-to-end locally (mock server)

**Objective:** Prove the full loop works before deploying.

**Step 1: Extract + syntax-check the JS**

```bash
cd ~/career/family-dashboard
node -e "const fs=require('fs');const h=fs.readFileSync('static/index.html','utf8');const m=h.match(/<script>([\s\S]*?)<\/script>/);fs.writeFileSync('/tmp/ember_check.js',m[1]);" && node --check /tmp/ember_check.js && echo "JS OK"
```

**Step 2: Compile both Python files**

```bash
python3 -m py_compile main.py voice_assistant.py && echo "PY OK"
```

**Step 3: Render a screenshot with sample data**

Reuse the mock-server approach from the layout work (serve `static/` + sample `family.json`/`rewards.json`/`chores.json`), then:

```bash
google-chrome --headless=new --disable-gpu --no-sandbox --hide-scrollbars \
  --window-size=1920,1080 --virtual-time-budget=5000 \
  --screenshot=/tmp/ember_phase1.png "http://127.0.0.1:8099/"
```

Then `vision_analyze` the PNG to confirm the rewards panel renders.

**Step 4: Commit any fixes**

---

## Task 9: Deploy to kiosk

**Objective:** Ship Phase 1 to the kiosk via the git workflow.

**Step 1: Push**

```bash
cd ~/career/family-dashboard
git push https://<token>@github.com/benjamin-craig-hi/family-dashboard.git main
```

**Step 2: Pull + restart on kiosk**

```bash
ssh dashboard@10.0.0.5 'cd ~/dashboard-app && git pull && \
  sudo systemctl restart dashboard-backend.service && \
  systemctl --user restart ember-voice.service'
```

**Step 3: Verify**

```bash
ssh dashboard@10.0.0.5 'systemctl is-active dashboard-backend.service; \
  systemctl --user is-active ember-voice.service; \
  curl -s http://localhost:8000/api/family; \
  curl -s http://localhost:8000/api/rewards'
```

Expected: both `active`; family/rewards return JSON.

---

## Risks / Notes

- **Existing chores lack `stars`/`assignee`** — the frontend and backend both
  treat missing fields as 0/"" (safe). No migration needed.
- **`prompt()` for claim assignee** is a stopgap; a proper member picker is a
  later polish task (Phase 1 keeps it simple).
- **Milestones are stored but not yet surfaced** as celebrations — that's
  Phase 2 (Motivation Mode). Phase 1 just exposes the data.
- **Star award is idempotent** — only fires on the not-done→done transition,
  so toggling a chore off and on doesn't double-award.
