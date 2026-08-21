# Session state — resume here

Working notes for picking this project back up. Not user documentation
(that's `README.md` and `docs/`) — this is the operational context a fresh
session needs, including the things that cost time to discover.

**Last updated:** 2026-08-04 · main @ `5530f16` · 98 tests green · CI green

---

## 1. What this project is

Autonomous Android automation agent. An LLM looks at a phone screen, decides
what to tap/type, and drives the device. Three surfaces:

| Surface | Path | Role |
|---|---|---|
| Backend | `backend/` | FastAPI. Agent loop, 6 LLM providers, KB, nav graph, persistence |
| Web UI | `frontend/` | React dashboard — Setup/Chat/Explore/Deploy/Demos/History/KB |
| Android app | `android/` | "Hey Agent" ambient voice assistant |

### Two execution paths — this is the key thing to understand

There are **two different ways a task gets executed**, and they exist for a
reason:

**A. ADB path (laptop).** `run_explore` / `run_deploy` in
`backend/agent/loop.py`. The backend drives the phone over ADB (USB or
wireless). Full-featured: vision, KB, nav graph, screenshots, demonstrations.
This is the original design and still works.

**B. On-device path (mobile + cloud).** The phone runs the loop itself
(`android/.../OnDeviceAgentLoop.kt`) and the backend only reasons, via two
stateless endpoints:

- `POST /agent/interpret` — speech text → `{app_name, task}`
- `POST /agent/decide` — screen elements + history → next action

**Why B exists:** path A needs the backend to physically reach the phone. A
cloud backend can't — no USB, no route through NAT. So B inverts it: the app
reads its own screen (AccessibilityService), asks what to do, and taps
itself. That makes the backend deployable anywhere, including a 1 GB
free-tier EC2 box.

Both paths deliberately share `build_deploy_prompt` and `call_text_llm`, so
they can't quietly diverge.

---

## 2. Running things

**The venv is at `backend/venv`, not `.venv` at the repo root.** Bash's `cd`
sometimes resets between tool calls here — prefer absolute paths.

```bash
cd /c/Users/bella/mobile-agent
source backend/venv/Scripts/activate

# backend (0.0.0.0 so a phone on the LAN can reach it)
python -m uvicorn backend.api.main:app --host 0.0.0.0 --port 8000

pytest backend/tests/ -q
ruff check backend/           # use exactly this path — pyproject scopes excludes
```

```bash
cd /c/Users/bella/mobile-agent/frontend
npm run dev          # :5173
npm run lint && npm run build
```

Auth: every request needs `X-API-Key` (REST) or `?token=` (WebSocket), value
from `API_KEY` in `.env`. Handy:

```bash
API_KEY=$(grep ^API_KEY= /c/Users/bella/mobile-agent/.env | cut -d= -f2)
curl -s localhost:8000/api/v1/device/health/detailed -H "X-API-Key: $API_KEY"
```

---

## 3. Android — hard-won specifics

**There is no `gradlew` script.** Build via Android Studio.

**Open `android/` as its own project root.** Opening the repo root and
linking the Gradle project *looks* like it works — it syncs and builds — but
every run configuration then fails with *"Run configuration … is not
supported in the current project. Cannot obtain the package."* Reopening
`android/` directly fixed it. Also set Gradle JDK → **Embedded JDK**, or the
first sync dies on "Invalid Gradle JDK configuration found".

**Syntax-checking Kotlin without Gradle** (fast iteration, no IDE):

```bash
cd /c/Users/bella/mobile-agent/android
export JAVA_HOME="/c/Program Files/Android/Android Studio/jbr"
KOTLINC="/c/Program Files/Android/Android Studio/plugins/Kotlin/kotlinc/bin/kotlinc-jvm.bat"
ANDROID_JAR="/c/Users/bella/AppData/Local/Android/Sdk/platforms/android-36.1/android.jar"
"$KOTLINC" -cp "$ANDROID_JAR" app/src/main/java/com/mobileagent/heyagent/*.kt
rm -rf META-INF com          # kotlinc drops build artifacts in cwd — clean up
```

This **cannot resolve okhttp/androidx** (no dependency fetching), so it always
reports a fixed cascade of `unresolved reference` errors for those. Filter
them out and look for anything else — that's how a real
variable-shadowing bug in `MainActivity` was caught. Only Gradle proves a
true build.

**Android files:**

| File | Role |
|---|---|
| `HeyAgentAccessibilityService.kt` | Wake-word wiring, `performAction()` (real gestures), screen dump |
| `OnDeviceAgentLoop.kt` | read → decide → act → repeat, own thread, sync HTTP |
| `ScreenElement.kt` | `AccessibilityNodeInfo` → backend element shape |
| `WakeWordListener.kt` | `ContinuousWakeWordListener` — the only wake-word impl |
| `VoiceActivityGate.kt` | Cheap energy gate before expensive STT |
| `CommandInterpreter.kt` | Pure mic → string. No dispatch logic |
| `BackendClient.kt` | HTTP. 90 s timeout client for `/decide` + `/interpret` |
| `AppRegistry.kt` | app_name → package, mirrors `backend/device/app_registry.py` |
| `Settings.kt` | SharedPreferences: backend URL, API key, device serial |
| `MainActivity.kt` | Settings UI + "Test Listen" |

**Element IDs must be assigned after sorting** (top-to-bottom, then
left-to-right), mirroring `xml_parser.py`. Otherwise "element 3" means
different things to the model and to the phone.

---

## 4. Environment / accounts

- **Phone:** Pixel, serial `47161VDJH00348`. Wireless ADB was enabled via
  `adb tcpip 5555` (persists until reboot; then `adb connect <ip>:5555`).
- **Wi-Fi is unreliable for phone↔laptop.** Campus network (`VITCHOSDNS`)
  uses client isolation — phone got a CGNAT `100.x` address and could not
  reach the laptop at all. A hotspot works. Windows Firewall also blocks
  inbound on a "Public" profile.
- **Gemini free tier: 20 requests/day per *project*.** Each agent round is
  one request. A new *key* in the same project does not help. `429
  RESOURCE_EXHAUSTED` is the quota, not a bug.
- **Langfuse:** configured and verified, **US region**
  (`https://us.cloud.langfuse.com` — the EU default 401s with these keys).
- **Neo4j:** Docker, `bolt://localhost:7687`, `neo4j/learnGraph123`. Optional
  and fail-soft; often just not running.
- **Ollama:** local, free, no quota. Good for testing when Gemini is
  exhausted — but weaker (it produced the schema/action-validity misses in
  `backend/eval/reports/`).

Secrets live in `.env` (gitignored). `API_KEY` is the *backend's own* auth
key — **not** the Gemini key. Both the web UI and the Android app need that
one, and it's a common mix-up.

---

## 5. Verified vs not

**Verified live:**
- Wake word → STT → backend → task inference, on the real phone
- `/agent/decide` across two rounds with Gemini (history genuinely reaches
  the prompt — round 2 typed the query instead of repeating round 1's tap)
- Backend under both cloud conditions: Neo4j down, **and no `adb` binary**
  (`discover_and_connect()` returns `[]`, doesn't raise)
- Android app: Gradle build → APK → installed → launched (emulator)
- Stop actually stops (30-round deploy halted on request, stayed halted)
- Session persistence across two real kill/restart cycles

**Not verified:**
- The joint phone → Tailscale → EC2 → phone loop (needs the box to exist)
- `OnDeviceAgentLoop` completing a real multi-round task on hardware
- Nav graph's actual benefit — unmeasured, and it's what justifies Neo4j

---

## 6. What to do next

**Blocked on the user:**
1. **AWS deploy.** `scripts/provision_ec2.sh` + `docs/DEPLOY_AWS_TAILSCALE.md`
   are ready. Needs their AWS account (costs money; no CLI creds on this
   machine). Tailscale chosen so nothing is publicly exposed.
2. **End-to-end mobile test** — depends on (1).

**Available now:**
3. **Fixed sleeps in `backend/device/controller.py`** — biggest remaining
   perf win. `tap()` pads **0.8 s every round**; `launch_app()` 2 s;
   `text()` ~1.5 s for 40 chars. Replace with UI-settle polling (compare
   consecutive XML dumps). **Needs a real device to validate** — a blind
   change risks acting on half-rendered screens. See `docs/PERFORMANCE.md` §3.
4. **`wait_idle()` wastes a `uiautomator dump`** — runs one, discards the
   output, then `pull_xml()` immediately runs another. Fold them together.
5. **Measure the nav graph.** Does feeding known transitions actually cut
   round count? If not, Neo4j is dead weight.
6. **Cache the ChromaDB client.** `KnowledgeBase(...)` opens a new
   `PersistentClient` per request (disk I/O every time).

**Before any public exposure:**
7. **`VITE_API_KEY` is inlined into the JS bundle** — anyone loading the web
   page can read the key and drive the phone. Fine on localhost, hard blocker
   otherwise. Needs real per-user auth (`backend/security/auth.py` is small
   and centralised, so the change is contained).

---

## 7. Conventions worth keeping

- **Verify live, don't just review.** Several bugs here survived code review
  and only showed up when actually run — the stop-does-nothing bug is the
  clearest example.
- Commit per logical unit, message explains *why*, not just what.
- Integrations are **fail-soft**: Neo4j, Langfuse, and persistence all log a
  warning and continue. Don't let them break the agent loop.
- Route ordering: literal paths (`/decide`, `/sessions`) must be registered
  **before** `/{session_id}`, or the wildcard swallows them.
- `git commit` messages here don't use a Co-Authored-By trailer (it breaks
  the Google CLA bot on other repos).
