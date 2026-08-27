# Skylight-Parity Roadmap — Family Dashboard "Super Charge"

> **Status:** Feasibility check + phased plan. NOT yet approved for execution.
> **Date:** 2026-08-27
> **Inspiration:** Skylight Calendar Max (myskylight.com)

---

## 1. Feasibility Verdict (honest, per feature)

Legend: ✅ feasible now · 🟡 feasible with real work · 🔴 not feasible / skip

### Hardware & display (Skylight's physical specs)

| Skylight spec | Our reality | Verdict |
|---|---|---|
| 27" 2560×1440 touchscreen | 15.6" 1920×1080 2-in-1 (Toshiba) | 🔴 hardware — not a software task |
| Auto-rotate portrait/landscape | 2-in-1 already rotates; CSS can adapt | 🟡 low value, defer |
| Anti-glare / frames / wall mount / 12V DC / dual speakers | fixed hardware | 🔴 N/A to software |

**Key point:** every *software* feature below is achievable on the current
hardware. The only thing we can't match is physical size/resolution. If a
27" wall display is the end goal, that's a monitor + a small always-on PC
(or hotrod driving a display) — a hardware decision, not code.

### Core calendar (free tier)

| Feature | Verdict | Notes |
|---|---|---|
| iCal public feed (school/sports) | ✅ | fetch + parse `.ics`; trivial |
| Day / Week / Month views | 🟡 | have Month; add Day + Week |
| Color-coding per family member | 🟡 | needs a "family members" data model |
| Add / edit / delete on touchscreen | 🟡 | have add; need edit + delete |
| Google Calendar two-way sync | 🟡 | OAuth + Calendar API |
| iCloud / Yahoo sync | 🟡 | CalDAV |
| Outlook sync | 🟡 | Microsoft Graph |
| Cozi / Readdle sync | 🔴 | no public API |
| Multi-device linking | 🟡 | needs shared backend; YAGNI unless 2nd screen |
| No web browsing (kid-safe) | ✅ | already kiosk-mode locked |

### Home management (free tier)

| Feature | Verdict | Notes |
|---|---|---|
| Task manager / chore charts (assigned to members) | 🟡 | extends current chores |
| Custom lists | 🟡 | data model + UI |
| Shared grocery list (add from phone) | 🟡 | needs phone reachability (LAN web UI) |
| Parental lock (PIN) | ✅ | simple gate on settings |
| Event countdown | ✅ | trivial |
| Live weather | ✅ | Open-Meteo, no API key |
| Sleep mode (schedule dim/blank) | ✅ | JS + systemd timer |

### "Calendar Plus" premium tier

| Feature | Verdict | Notes |
|---|---|---|
| Sidekick AI (text/audio → structured) | ✅ | already have (voice + LLM tool-calling) |
| Sidekick AI (printed material → structured) | 🟡 | needs OCR/vision model |
| Magic Import (forward flyer/PDF/email → events) | 🟡 | email ingestion + vision/PDF parse |
| Meal planning | 🟡 | data model + UI |
| AI recipe bank (photo → categorized) | 🟡 | vision model |
| Grocery delivery sync (Instacart) | 🔴 | no public consumer API — replace with "export list" |

### The three headline features Ben listed

| Feature | Verdict | Notes |
|---|---|---|
| **Star-Powered Rewards** (stars on chores, milestones, reward store) | ✅ | fully self-contained; highest value; Phase 1 |
| **Disney Motivation Mode** (animated graphics, emoji celebrations, voice alerts) | 🟡 | graphics/emoji = ✅; "Elsa" voice = 🔴 (see caveat) |
| **Photo & Video Screensaver** (idle → photo frame, cloud-synced) | 🟡 | photos ✅; video needs codec care; "cloud" = self-hosted |

---

## 2. Hard Constraints & Caveats (read before building)

1. **"Elsa" voice is off the table** (dropped by Ben). Disney character
   voices are copyrighted and we have no voice-cloning pipeline. Instead,
   add an on-screen **voice + wake-name selector** (Kokoro voices, e.g.
   `af_heart` / `am_michael` / `am_adam`) so a family can pick its own
   assistant name and voice. Emoji celebrations + animated graphics are fine.

2. **"Cloud-synced" must be self-hosted** (Ben's standing preference:
   open-source/self-hosted over paid APIs). Recommend **Syncthing** for
   photo/video sync from phones (peer-to-peer, no cloud, LAN-friendly).
   No Google Photos / iCloud dependency.

3. **Instacart has no consumer API.** Grocery delivery sync is not
   feasible. Replace with "export grocery list to text/email" (or a
   shareable link).

4. **CPU-only kiosk** (i7-4510U, no GPU). Video *playback* is fine (Intel
   HD 4400 hardware decode), but *transcoding* is CPU-bound. Photos are
   trivial. Videos must be pre-transcoded to a compatible codec (H.264
   MP4) or played as-is if already compatible. Heavy lifting (vision,
   transcoding) offloads to hotrod.

5. **Storage is not a constraint.** Kiosk has 821 GB free; hotrod 366 GB.
   A family photo/video library fits easily on the kiosk.

6. **Cozi / Readdle have no public API** — drop from the sync list. The
   realistic sync set is: Google, iCloud, Outlook, generic CalDAV, and
   iCal public feeds.

---

## 3. Architecture Decision (keep it simple)

**Stay single-node.** The kiosk (always-on, 821 GB free) remains the source
of truth. hotrod stays the LLM/vision/transcode offload. Add features
incrementally to the existing FastAPI + JSON + static HTML stack.

- **Data:** keep JSON files for now (a family's data is tiny). Introduce
  SQLite only when calendar sync lands (external IDs, sync tokens, hundreds
  of events) — not before. YAGNI.
- **Shared backend (hotrod):** only if multi-device linking becomes real.
  Don't build it speculatively.

This honors the "cut layers, keep core scaling" preference.

---

## 4. Phased Roadmap

Each phase is independently shippable and deployable to the kiosk.

### Phase 1 — Star-Powered Rewards (highest value, fully self-contained)

The gamification loop: chores carry star values → completing a chore earns
stars → stars accumulate toward milestones → a reward store lets kids
"spend" stars.

**Data model** (extend `chores.json` + new files):
- `chores.json`: add `stars` (int) and `assignee` (string, family member).
- `rewards.json`: `[{ "title", "cost", "claimed": false }]`.
- `milestones.json`: `[{ "threshold", "label", "emoji" }]`.
- `family.json`: `[{ "name", "color", "stars" }]` (also feeds color-coding).

**Backend** (`main.py`): endpoints for
- `POST /api/chores/{i}/complete` → mark done + award stars to assignee.
- `GET/POST /api/rewards`, `POST /api/rewards/{i}/claim` → deduct stars.
- `GET /api/family` → star totals + milestone progress.

**Frontend** (`static/index.html`):
- Chore rows show star count + assignee color chip.
- A "Rewards" strip: star balance, milestone progress bar, reward store
  with claim buttons.
- Emoji celebration on milestone hit (ties into Phase 2).

**Voice** (`voice_assistant.py`): extend `add_chore` to accept `stars` and
`assignee`; add a "complete chore" tool.

### Phase 2 — Motivation Mode (graphics + emoji + voice/wake-name selector)

- CSS/emoji milestone celebrations (confetti, star burst) — GPU-accelerated
  in Chromium, no perf concern.
- Animated motivational graphics on chore completion.
- On-screen **voice + wake-name selector** (Kokoro voices; pick your own
  assistant name instead of being locked to "Jarvis").
- Reuse the existing chime/TTS pipeline; add a "celebration" voice line.

### Phase 3 — Photo & Video Screensaver

- Idle detection (no touch/input for N minutes) → full-screen photo/video
  carousel; any touch returns to the dashboard.
- Photo library: a watched folder + Syncthing for phone sync.
- Video: H.264 MP4 playback; pre-transcode on hotrod if needed.
- A simple "add photos" upload endpoint as a fallback to Syncthing.

### Phase 4 — Calendar Sync & Views

- iCal public feeds (trivial, do early if wanted).
- Day/Week views + edit/delete on touchscreen.
- Google (OAuth) + iCloud/CalDAV + Outlook (Graph) two-way sync.
- Introduce SQLite here (external IDs, sync tokens).
- Color-coding per family member (uses `family.json` from Phase 1).

### Phase 5 — Home Management Extras

- Custom lists, shared grocery list (LAN web UI for phones), parental PIN
  lock, event countdown, live weather (Open-Meteo), sleep mode.

### Phase 6 — "Calendar Plus" AI (last, biggest lift)

- Magic Import: email ingestion + vision/PDF parsing → auto-populate events.
- AI recipe bank (photo → categorized).
- Meal planning.
- Grocery list export (replaces Instacart).

---

## 5. Decisions (locked in 2026-08-27)

1. **Form factor:** the 15.6" 2-in-1 is the final device. The ethos is
   *anyone can take an old unused touchscreen laptop/tablet and do the
   same thing* — repurpose old hardware, not buy a 27" panel. So: no
   auto-rotate investment, no resolution scaling beyond responsive CSS.
2. **Voice:** drop the Disney/Elsa idea entirely. Instead, add an on-screen
   option to **select different TTS voices and wake names** (so a family
   isn't locked to "Jarvis").
3. **Photo sync:** self-hosted (Syncthing) — confirmed.
4. **Calendar providers:** support all of them — Google, iCloud, Outlook,
   CalDAV, and iCal public feeds.
5. **Instacart:** drop it. Replace with **"export grocery list"** (text /
   email / shareable link).
6. **Multi-device:** no second screen. Single kiosk. The goal is to take
   what's good about Skylight and integrate it into open source.

**Naming:** **Ember** (locked 2026-08-27). Wake word will be "Hey Ember"
(deferred). Spirit: self-hosted, warm, home-grown — light from *within* the
home, the opposite of "light from outside / cloud / subscription."

---

## 6. Suggested First Step

Phase 1 (Star-Powered Rewards) is the clear starting point: fully
self-contained, no external services, high family value, and it lays the
`family.json` foundation that color-coding (Phase 4) reuses. Recommend
approving Phase 1 and deferring the open questions that only affect later
phases.
