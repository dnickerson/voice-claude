# Voice-Claude Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a single-file Python server that lets users send voice commands from a phone/tablet to a running Claude Code session in a tmux pane, with responses streamed back and read aloud.

**Architecture:** `websockets.asyncio.server` handles both HTTP (serving the UI and pane list) and WebSocket (command injection + response streaming) on a single port via `process_request`. Voice commands are injected into tmux via `send-keys`; responses are captured by polling `capture-pane` and diffing line counts. Each user runs their own server instance under their own account using a per-user config file and systemd user service.

**Tech Stack:** Python 3 stdlib, `websockets` 16.0 (already installed), `pytest` 9.0.3 (already installed), `tmux`, Web Speech API + `speechSynthesis` (browser-native, no install)

## Global Constraints

- Python 3 stdlib + `websockets` 16.0 only — no new pip installs
- All paths in config use `~` (expanded at startup)
- Server must handle one WebSocket command at a time; cancel in-flight if new one arrives
- `connection.respond(http.HTTPStatus, text: str)` is the websockets v16 HTTP API
- tmux capture uses `-S -2000` (last 2000 lines of scroll buffer)
- Completion detection: 3 consecutive polls (1.5s) with no new output
- Timeout: 90 polls × 0.5s = 45 seconds; sends `{"done": true, "timeout": true}`
- Run tests with: `cd ~/voice-claude && python3 -m pytest tests/ -v`

---

## File Map

```
~/voice-claude/
  server.py                          ← entire application (created across Tasks 1–6)
  config.example.json                ← template for dananickerson (Task 1)
  voice-claude.service               ← systemd user service template (Task 7)
  tests/
    __init__.py                      ← empty, makes tests/ a package (Task 1)
    test_config.py                   ← config loader tests (Task 1)
    test_panes.py                    ← pane parsing + label matching tests (Task 2)
    test_capture.py                  ← response diff tests (Task 3)
```

---

## Task 1: Project scaffold + config loader

**Files:**
- Create: `~/voice-claude/server.py`
- Create: `~/voice-claude/tests/__init__.py`
- Create: `~/voice-claude/tests/test_config.py`
- Create: `~/voice-claude/config.example.json`

**Interfaces:**
- Produces:
  - `load_config(path: Path) -> dict` — reads and returns parsed JSON; calls `sys.exit` with message if file missing
  - `expand_paths(config: dict) -> dict` — expands `~` in every `project["path"]` in-place, returns config
  - `validate_config(config: dict) -> None` — raises `ValueError` if `port` or `projects` missing, or any project missing `label`/`path`

- [ ] **Step 1: Write failing tests**

Create `~/voice-claude/tests/__init__.py` (empty file), then create `~/voice-claude/tests/test_config.py`:

```python
import json
import pytest
from pathlib import Path
from server import load_config, expand_paths, validate_config


def test_load_config_valid(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({
        "port": 8899,
        "projects": [{"label": "flytab", "path": "~/flytab"}]
    }))
    result = load_config(cfg)
    assert result["port"] == 8899
    assert result["projects"][0]["label"] == "flytab"


def test_load_config_missing_exits(tmp_path):
    with pytest.raises(SystemExit):
        load_config(tmp_path / "nonexistent.json")


def test_expand_paths_replaces_tilde():
    home = str(Path.home())
    config = {"port": 8899, "projects": [
        {"label": "flytab", "path": "~/flytab"},
        {"label": "home",   "path": "~"},
    ]}
    result = expand_paths(config)
    assert result["projects"][0]["path"] == f"{home}/flytab"
    assert result["projects"][1]["path"] == home


def test_validate_config_valid():
    validate_config({"port": 8899, "projects": [{"label": "x", "path": "/x"}]})


def test_validate_config_missing_port():
    with pytest.raises(ValueError, match="port"):
        validate_config({"projects": []})


def test_validate_config_missing_projects():
    with pytest.raises(ValueError, match="projects"):
        validate_config({"port": 8899})


def test_validate_config_bad_project():
    with pytest.raises(ValueError):
        validate_config({"port": 8899, "projects": [{"label": "x"}]})
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
cd ~/voice-claude && python3 -m pytest tests/test_config.py -v
```

Expected: `ModuleNotFoundError: No module named 'server'` (server.py doesn't exist yet)

- [ ] **Step 3: Create server.py skeleton with config functions**

Create `~/voice-claude/server.py`:

```python
#!/usr/bin/env python3
import asyncio
import http
import json
import sys
from pathlib import Path

from websockets.asyncio.server import serve, ServerConnection

# ── Config ────────────────────────────────────────────────────────────────────

CONFIG_PATH = Path.home() / ".config" / "voice-claude" / "config.json"


def load_config(path: Path = CONFIG_PATH) -> dict:
    if not path.exists():
        sys.exit(
            f"Config not found: {path}\n"
            f"Copy ~/voice-claude/config.example.json to {path} and edit it."
        )
    with open(path) as f:
        return json.load(f)


def expand_paths(config: dict) -> dict:
    home = str(Path.home())
    for project in config["projects"]:
        project["path"] = project["path"].replace("~", home, 1)
    return config


def validate_config(config: dict) -> None:
    if "port" not in config:
        raise ValueError("Config missing 'port'")
    if "projects" not in config:
        raise ValueError("Config missing 'projects'")
    for p in config["projects"]:
        if "label" not in p or "path" not in p:
            raise ValueError(f"Project entry missing 'label' or 'path': {p}")


# ── Pane discovery ────────────────────────────────────────────────────────────
# (added in Task 2)

# ── Response capture ──────────────────────────────────────────────────────────
# (added in Task 3)

# ── HTML UI ───────────────────────────────────────────────────────────────────
# (added in Task 6)

HTML = "<html><body>placeholder</body></html>"

# ── HTTP + WebSocket handlers ─────────────────────────────────────────────────
# (added in Task 4)

# ── Entry point ───────────────────────────────────────────────────────────────
# (added in Task 4)
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
cd ~/voice-claude && python3 -m pytest tests/test_config.py -v
```

Expected: 7 tests PASSED

- [ ] **Step 5: Create config.example.json**

Create `~/voice-claude/config.example.json`:

```json
{
  "port": 8899,
  "projects": [
    {"label": "flytab",     "path": "~/flytab"},
    {"label": "knitfit",    "path": "~/knitfit/platform"},
    {"label": "sharedlens", "path": "~/sharedlens"},
    {"label": "engine",     "path": "~/engine_analysis"},
    {"label": "hypertherm", "path": "~/hypertherm-project"},
    {"label": "home",       "path": "~"}
  ]
}
```

- [ ] **Step 6: Commit**

```bash
cd ~/voice-claude && git add server.py tests/__init__.py tests/test_config.py config.example.json
git commit -m "feat: config loader with validation and tests"
```

---

## Task 2: Pane discovery

**Files:**
- Modify: `~/voice-claude/server.py` (add pane functions after config section)
- Create: `~/voice-claude/tests/test_panes.py`

**Interfaces:**
- Consumes: `config["projects"]` list from Task 1
- Produces:
  - `match_label(pane_path: str, projects: list) -> str` — returns longest-prefix matched label, or last path segment if no match
  - `parse_panes(tmux_output: str, projects: list) -> list` — parses pipe-delimited tmux output; claude panes sort first
  - `get_panes(projects: list) -> list` — async, runs tmux, returns parsed pane list; returns `[]` if tmux not running

- [ ] **Step 1: Write failing tests**

Create `~/voice-claude/tests/test_panes.py`:

```python
from server import match_label, parse_panes

PROJECTS = [
    {"label": "flytab",  "path": "/home/dana/flytab"},
    {"label": "knitfit", "path": "/home/dana/knitfit/platform"},
    {"label": "home",    "path": "/home/dana"},
]


def test_match_label_exact():
    assert match_label("/home/dana/flytab", PROJECTS) == "flytab"


def test_match_label_subdir():
    assert match_label("/home/dana/flytab/src/components", PROJECTS) == "flytab"


def test_match_label_longest_prefix_wins():
    projects = [
        {"label": "knitfit-root", "path": "/home/dana/knitfit"},
        {"label": "knitfit",      "path": "/home/dana/knitfit/platform"},
    ]
    assert match_label("/home/dana/knitfit/platform/src", projects) == "knitfit"


def test_match_label_no_match_returns_last_segment():
    assert match_label("/home/dana/mystery/project", PROJECTS) == "project"


def test_match_label_home():
    assert match_label("/home/dana", PROJECTS) == "home"


def test_parse_panes_basic():
    output = (
        "work:0.0|claude|/home/dana/flytab\n"
        "work:1.0|bash|/home/dana/knitfit/platform\n"
    )
    panes = parse_panes(output, PROJECTS)
    assert len(panes) == 2
    assert panes[0] == {
        "id": "work:0.0",
        "command": "claude",
        "path": "/home/dana/flytab",
        "label": "flytab",
    }
    assert panes[1]["label"] == "knitfit"


def test_parse_panes_claude_sorts_first():
    output = (
        "work:0.0|bash|/home/dana/flytab\n"
        "work:1.0|claude|/home/dana/knitfit/platform\n"
    )
    panes = parse_panes(output, PROJECTS)
    assert panes[0]["command"] == "claude"


def test_parse_panes_empty_output():
    assert parse_panes("", PROJECTS) == []


def test_parse_panes_skips_malformed_lines():
    output = "work:0.0|claude|/home/dana/flytab\nbadline\n"
    panes = parse_panes(output, PROJECTS)
    assert len(panes) == 1
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
cd ~/voice-claude && python3 -m pytest tests/test_panes.py -v
```

Expected: `ImportError: cannot import name 'match_label' from 'server'`

- [ ] **Step 3: Add pane discovery functions to server.py**

Replace the `# ── Pane discovery ──` comment block in `server.py` with:

```python
# ── Pane discovery ────────────────────────────────────────────────────────────

def match_label(pane_path: str, projects: list) -> str:
    best_label = pane_path.rstrip("/").split("/")[-1]
    best_len = -1
    for proj in projects:
        proj_path = proj["path"].rstrip("/")
        if pane_path == proj_path or pane_path.startswith(proj_path + "/"):
            if len(proj_path) > best_len:
                best_len = len(proj_path)
                best_label = proj["label"]
    return best_label


def parse_panes(tmux_output: str, projects: list) -> list:
    panes = []
    for line in tmux_output.strip().splitlines():
        parts = line.split("|", 2)
        if len(parts) != 3:
            continue
        pane_id, command, path = parts
        panes.append({
            "id": pane_id,
            "command": command,
            "path": path,
            "label": match_label(path, projects),
        })
    panes.sort(key=lambda p: (0 if p["command"] == "claude" else 1, p["id"]))
    return panes


async def get_panes(projects: list) -> list:
    try:
        proc = await asyncio.create_subprocess_exec(
            "tmux", "list-panes", "-a", "-F",
            "#{session_name}:#{window_index}.#{pane_index}"
            "|#{pane_current_command}"
            "|#{pane_current_path}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return parse_panes(stdout.decode(), projects)
    except FileNotFoundError:
        return []
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
cd ~/voice-claude && python3 -m pytest tests/test_panes.py -v
```

Expected: 9 tests PASSED

- [ ] **Step 5: Commit**

```bash
cd ~/voice-claude && git add server.py tests/test_panes.py
git commit -m "feat: pane discovery with label matching and tests"
```

---

## Task 3: Response capture

**Files:**
- Modify: `~/voice-claude/server.py` (add capture functions)
- Create: `~/voice-claude/tests/test_capture.py`

**Interfaces:**
- Produces:
  - `diff_output(before_count: int, after_lines: list) -> str` — returns lines beyond `before_count` joined with newlines; `""` if none
  - `snapshot_pane(pane_id: str) -> int` — async, returns line count of current pane buffer
  - `capture_response(pane_id: str, before_count: int)` — async generator; yields `str` chunks as they appear, yields `None` on 45s timeout

- [ ] **Step 1: Write failing tests**

Create `~/voice-claude/tests/test_capture.py`:

```python
from server import diff_output


def test_diff_output_new_lines():
    before_count = 5
    after_lines = ["a", "b", "c", "d", "e", "new1", "new2"]
    assert diff_output(before_count, after_lines) == "new1\nnew2"


def test_diff_output_no_change():
    assert diff_output(5, ["a", "b", "c", "d", "e"]) == ""


def test_diff_output_empty_before():
    assert diff_output(0, ["line1", "line2"]) == "line1\nline2"


def test_diff_output_fewer_lines_than_before():
    # pane was cleared; return empty rather than negative slice
    assert diff_output(10, ["a", "b"]) == ""
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
cd ~/voice-claude && python3 -m pytest tests/test_capture.py -v
```

Expected: `ImportError: cannot import name 'diff_output' from 'server'`

- [ ] **Step 3: Add capture functions to server.py**

Replace the `# ── Response capture ──` comment block in `server.py` with:

```python
# ── Response capture ──────────────────────────────────────────────────────────

async def _capture_pane_lines(pane_id: str) -> list:
    proc = await asyncio.create_subprocess_exec(
        "tmux", "capture-pane", "-t", pane_id, "-p", "-S", "-2000",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    return stdout.decode().splitlines()


async def snapshot_pane(pane_id: str) -> int:
    lines = await _capture_pane_lines(pane_id)
    return len(lines)


def diff_output(before_count: int, after_lines: list) -> str:
    new_lines = after_lines[before_count:]
    return "\n".join(new_lines) if new_lines else ""


async def capture_response(pane_id: str, before_count: int):
    sent_count = before_count
    no_change_ticks = 0
    for _ in range(90):  # 45-second ceiling
        await asyncio.sleep(0.5)
        lines = await _capture_pane_lines(pane_id)
        chunk = diff_output(sent_count, lines)
        if chunk:
            sent_count = len(lines)
            no_change_ticks = 0
            yield chunk
        else:
            no_change_ticks += 1
            if no_change_ticks >= 3:
                return
    yield None  # timeout sentinel
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
cd ~/voice-claude && python3 -m pytest tests/test_capture.py -v
```

Expected: 4 tests PASSED

- [ ] **Step 5: Run full test suite — verify nothing broke**

```bash
cd ~/voice-claude && python3 -m pytest tests/ -v
```

Expected: 20 tests PASSED

- [ ] **Step 6: Commit**

```bash
cd ~/voice-claude && git add server.py tests/test_capture.py
git commit -m "feat: response capture with pane diffing and tests"
```

---

## Task 4: HTTP + WebSocket server wiring

**Files:**
- Modify: `~/voice-claude/server.py` (add process_request, stub ws_handler, main)

**Interfaces:**
- Consumes: `get_panes`, `HTML`, `validate_config`, `load_config`, `expand_paths` from earlier tasks
- Produces: running server at `http://0.0.0.0:<port>` serving `/` and `/panes`; WebSocket endpoint at `ws://0.0.0.0:<port>/ws` (handler stubbed, completed in Task 5)

- [ ] **Step 1: Install dananickerson's config**

```bash
mkdir -p ~/.config/voice-claude
cp ~/voice-claude/config.example.json ~/.config/voice-claude/config.json
```

Verify it was created:

```bash
cat ~/.config/voice-claude/config.json
```

Expected: JSON with port 8899 and 6 projects

- [ ] **Step 2: Add HTTP handler, stub ws_handler, and main() to server.py**

Replace the `# ── HTTP + WebSocket handlers ──` comment block and everything after it in `server.py` with:

```python
# ── HTTP + WebSocket handlers ─────────────────────────────────────────────────

PROJECTS: list = []  # populated at startup


async def process_request(connection: ServerConnection, request) -> object:
    if request.path == "/":
        return connection.respond(http.HTTPStatus.OK, HTML)
    if request.path == "/panes":
        panes = await get_panes(PROJECTS)
        return connection.respond(http.HTTPStatus.OK, json.dumps(panes))
    return None  # proceed with WebSocket upgrade


async def ws_handler(websocket) -> None:
    await websocket.send(json.dumps({"chunk": "server connected (stub)\n", "done": True}))


# ── Entry point ───────────────────────────────────────────────────────────────

async def main() -> None:
    config = load_config()
    expand_paths(config)
    validate_config(config)
    global PROJECTS
    PROJECTS = config["projects"]
    port = config["port"]
    print(f"Voice-Claude on http://0.0.0.0:{port}")
    async with serve(ws_handler, "0.0.0.0", port, process_request=process_request):
        await asyncio.get_event_loop().run_forever()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 3: Start server and verify HTTP endpoints**

```bash
cd ~/voice-claude && python3 server.py &
sleep 1
curl -s http://localhost:8899/ | head -5
curl -s http://localhost:8899/panes
```

Expected:
- First `curl`: returns `<html><body>placeholder</body></html>`
- Second `curl`: returns JSON array of your active tmux panes

- [ ] **Step 4: Stop the test server**

```bash
kill %1
```

- [ ] **Step 5: Commit**

```bash
cd ~/voice-claude && git add server.py
git commit -m "feat: HTTP routing and server wiring with stub WebSocket handler"
```

---

## Task 5: WebSocket command handler

**Files:**
- Modify: `~/voice-claude/server.py` (replace stub ws_handler with full implementation)

**Interfaces:**
- Consumes: `snapshot_pane`, `capture_response` from Task 3
- Produces: `ws_handler` that receives `{"text": str, "pane": str}`, injects into tmux, streams `{"chunk": str}` messages, ends with `{"done": true}` or `{"done": true, "timeout": true}`, sends `{"error": str}` on failure

- [ ] **Step 1: Replace stub ws_handler in server.py**

Find and replace the `ws_handler` function in `server.py`:

```python
async def ws_handler(websocket) -> None:
    capture_task = None

    async def run_command(text: str, pane: str) -> None:
        nonlocal capture_task
        if capture_task and not capture_task.done():
            capture_task.cancel()

        try:
            before_count = await snapshot_pane(pane)
        except Exception:
            await websocket.send(json.dumps({"error": f"Pane not found: {pane}"}))
            return

        inject = await asyncio.create_subprocess_exec(
            "tmux", "send-keys", "-t", pane, text, "Enter",
        )
        await inject.wait()

        async def stream() -> None:
            timed_out = False
            async for chunk in capture_response(pane, before_count):
                if chunk is None:
                    timed_out = True
                    break
                await websocket.send(json.dumps({"chunk": chunk}))
            await websocket.send(json.dumps({"done": True, "timeout": timed_out}))

        capture_task = asyncio.create_task(stream())

    async for message in websocket:
        try:
            data = json.loads(message)
            text = data.get("text", "").strip()
            pane = data.get("pane", "").strip()
            if not text or not pane:
                await websocket.send(json.dumps({"error": "Missing text or pane"}))
                continue
            await run_command(text, pane)
        except json.JSONDecodeError:
            await websocket.send(json.dumps({"error": "Invalid JSON"}))
        except Exception as e:
            await websocket.send(json.dumps({"error": str(e)}))
```

- [ ] **Step 2: Start server and test WebSocket with a live tmux pane**

In a separate terminal, start a tmux session with claude if not already running:

```bash
tmux new-session -d -s test -x 200 -y 50
tmux send-keys -t test "claude" Enter
```

Start the voice server:

```bash
cd ~/voice-claude && python3 server.py &
sleep 1
```

Send a test command via Python WebSocket client:

```bash
python3 - <<'EOF'
import asyncio, json, websockets

async def test():
    async with websockets.connect("ws://localhost:8899/ws") as ws:
        await ws.send(json.dumps({"text": "say hello in one sentence", "pane": "test:0.0"}))
        while True:
            msg = json.loads(await ws.recv())
            if msg.get("chunk"):
                print(msg["chunk"], end="", flush=True)
            if msg.get("done"):
                print(f"\n[done, timeout={msg.get('timeout', False)}]")
                break

asyncio.run(test())
EOF
```

Expected: Claude's response streams to the terminal, ends with `[done, timeout=False]`

- [ ] **Step 3: Verify in-flight cancellation — send two rapid commands**

```bash
python3 - <<'EOF'
import asyncio, json, websockets

async def test():
    async with websockets.connect("ws://localhost:8899/ws") as ws:
        await ws.send(json.dumps({"text": "count slowly to 20", "pane": "test:0.0"}))
        await asyncio.sleep(1)
        await ws.send(json.dumps({"text": "say INTERRUPTED", "pane": "test:0.0"}))
        chunks = []
        while True:
            msg = json.loads(await ws.recv())
            if msg.get("chunk"):
                chunks.append(msg["chunk"])
            if msg.get("done"):
                print("Final chunks received:", len(chunks))
                break

asyncio.run(test())
EOF
```

Expected: final output contains "INTERRUPTED" response, not the count-to-20 response

- [ ] **Step 4: Stop test server**

```bash
kill %1
```

- [ ] **Step 5: Commit**

```bash
cd ~/voice-claude && git add server.py
git commit -m "feat: WebSocket command handler with tmux injection and streaming"
```

---

## Task 6: HTML/JS UI

**Files:**
- Modify: `~/voice-claude/server.py` (replace placeholder HTML constant with full UI)

**Interfaces:**
- Consumes: `GET /panes` → JSON array; `ws://host:port/ws` WebSocket
- Produces: mobile-first page with fixed top bar (pane picker + refresh + status), scrollable body (transcript history), sticky bottom bar (hold-to-talk mic button), TTS on completion

- [ ] **Step 1: Replace the HTML placeholder in server.py**

Find the line `HTML = "<html><body>placeholder</body></html>"` in `server.py` and replace it with:

```python
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
<title>Voice Claude</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: system-ui, -apple-system, sans-serif; background: #111; color: #e0e0e0; }

#topbar {
  position: fixed; top: 0; left: 0; right: 0; z-index: 100;
  background: #1e1e1e; border-bottom: 1px solid #333;
  padding: 10px 12px; display: flex; gap: 8px; align-items: center;
}
#pane-select {
  flex: 1; background: #2a2a2a; color: #e0e0e0;
  border: 1px solid #444; border-radius: 6px;
  padding: 8px 10px; font-size: 14px;
}
#refresh-btn {
  background: #2a2a2a; color: #aaa; border: 1px solid #444;
  border-radius: 6px; padding: 8px 12px; font-size: 16px; cursor: pointer;
}
#status {
  padding: 4px 10px; border-radius: 20px;
  font-size: 11px; font-weight: 700; white-space: nowrap;
}
.s-connecting { background: #333; color: #888; }
.s-ready      { background: #14532d; color: #4ade80; }
.s-listening  { background: #1e3a5f; color: #60a5fa; }
.s-processing { background: #451a03; color: #fb923c; }
.s-error      { background: #450a0a; color: #f87171; }

#content {
  padding: 70px 16px 90px;
  min-height: 100vh;
  touch-action: pan-y;
}
.exchange { margin-bottom: 28px; border-top: 1px solid #222; padding-top: 16px; }
.you-label { font-size: 12px; color: #6b7280; margin-bottom: 4px; }
.you-text { font-size: 15px; color: #9ca3af; margin-bottom: 12px; font-style: italic; }
.claude-label { font-size: 12px; color: #60a5fa; margin-bottom: 6px; }
.claude-text { font-size: 15px; line-height: 1.65; white-space: pre-wrap; color: #e5e7eb; }
.interim-text { font-size: 15px; color: #4b5563; font-style: italic; margin-top: 8px; }

#bottombar {
  position: fixed; bottom: 0; left: 0; right: 0; z-index: 100;
  background: #1e1e1e; border-top: 1px solid #333;
  padding: 10px 16px;
}
#mic-btn {
  display: block; width: 100%; max-width: 480px; margin: 0 auto;
  background: #1d4ed8; color: #fff; border: none; border-radius: 50px;
  padding: 18px 32px; font-size: 17px; font-weight: 600;
  cursor: pointer; user-select: none; -webkit-user-select: none;
  touch-action: none;
  transition: background 0.15s;
}
#mic-btn.listening  { background: #b91c1c; }
#mic-btn:disabled   { background: #1f2937; color: #4b5563; cursor: not-allowed; }
</style>
</head>
<body>

<div id="topbar">
  <select id="pane-select"><option value="">Loading panes…</option></select>
  <button id="refresh-btn" title="Refresh panes">↻</button>
  <span id="status" class="s-connecting">connecting</span>
</div>

<div id="content" id="content">
  <div style="padding:16px 0; color:#4b5563; font-size:14px;">
    Select a pane above and hold the button below to speak.
  </div>
</div>

<div id="bottombar">
  <button id="mic-btn" disabled>Hold to talk</button>
</div>

<script>
const content    = document.getElementById('content');
const paneSelect = document.getElementById('pane-select');
const statusEl   = document.getElementById('status');
const micBtn     = document.getElementById('mic-btn');

// ── Status ──────────────────────────────────────────────────────────────────
function setStatus(s, label) {
  statusEl.className = `s-${s}`;
  statusEl.textContent = label || s;
}

// ── WebSocket ────────────────────────────────────────────────────────────────
let ws, currentResponseEl, currentResponse = '';

function connect() {
  setStatus('connecting');
  micBtn.disabled = true;
  ws = new WebSocket(`ws://${location.host}/ws`);

  ws.onopen = () => {
    setStatus('ready');
    micBtn.disabled = false;
    loadPanes();
  };
  ws.onclose = () => {
    setStatus('error', 'disconnected');
    micBtn.disabled = true;
    setTimeout(connect, 3000);
  };
  ws.onerror = () => setStatus('error');
  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (msg.error) {
      appendChunk(`[Error: ${msg.error}]`);
      endResponse(false);
      return;
    }
    if (msg.chunk) appendChunk(msg.chunk);
    if (msg.done)  endResponse(msg.timeout || false);
  };
}

// ── Pane picker ──────────────────────────────────────────────────────────────
async function loadPanes() {
  try {
    const r = await fetch('/panes');
    const panes = await r.json();
    if (!panes.length) {
      paneSelect.innerHTML = '<option value="">No panes found — start tmux + claude</option>';
      return;
    }
    paneSelect.innerHTML = panes.map(p =>
      `<option value="${p.id}">[${p.label}] ${p.id} · ${p.command}</option>`
    ).join('');
  } catch {
    paneSelect.innerHTML = '<option value="">Error loading panes</option>';
  }
}
document.getElementById('refresh-btn').onclick = loadPanes;

// ── Content ──────────────────────────────────────────────────────────────────
function startExchange(text) {
  currentResponse = '';
  const ex = document.createElement('div');
  ex.className = 'exchange';
  ex.innerHTML = `
    <div class="you-label">You said</div>
    <div class="you-text">${escHtml(text)}</div>
    <div class="claude-label">Claude</div>
    <div class="claude-text"></div>
  `;
  content.appendChild(ex);
  currentResponseEl = ex.querySelector('.claude-text');
}

function appendChunk(chunk) {
  currentResponse += chunk;
  currentResponseEl.textContent = currentResponse;
}

function endResponse(timedOut) {
  if (timedOut) appendChunk('\n[Response timed out after 45s]');
  setStatus('ready');
  micBtn.disabled = false;
  if (currentResponse && window.speechSynthesis) {
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(currentResponse);
    window.speechSynthesis.speak(u);
  }
}

function escHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ── Speech recognition ───────────────────────────────────────────────────────
const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognition, interimEl;

if (SpeechRec) {
  recognition = new SpeechRec();
  recognition.continuous = false;
  recognition.interimResults = true;
  recognition.lang = 'en-US';

  recognition.onstart = () => {
    setStatus('listening');
    micBtn.classList.add('listening');
    micBtn.textContent = 'Listening…';
  };

  recognition.onresult = (e) => {
    const transcript = Array.from(e.results).map(r => r[0].transcript).join('');
    if (!interimEl) {
      interimEl = document.createElement('div');
      interimEl.className = 'interim-text';
      content.appendChild(interimEl);
    }
    interimEl.textContent = transcript;
    if (e.results[e.results.length - 1].isFinal) {
      if (interimEl) { interimEl.remove(); interimEl = null; }
      sendCommand(transcript.trim());
    }
  };

  recognition.onend = () => {
    micBtn.classList.remove('listening');
    micBtn.textContent = 'Hold to talk';
    if (statusEl.textContent === 'listening') setStatus('ready');
  };

  recognition.onerror = (e) => {
    micBtn.classList.remove('listening');
    micBtn.textContent = 'Hold to talk';
    setStatus('error', e.error);
  };
} else {
  micBtn.textContent = 'Speech not supported in this browser';
}

// ── Send command ─────────────────────────────────────────────────────────────
function sendCommand(text) {
  if (!text) return;
  const pane = paneSelect.value;
  if (!pane) { setStatus('error', 'no pane selected'); return; }
  window.speechSynthesis?.cancel();
  startExchange(text);
  setStatus('processing');
  micBtn.disabled = true;
  ws.send(JSON.stringify({ text, pane }));
}

// ── Mic button ───────────────────────────────────────────────────────────────
function startListening(e) {
  e.preventDefault();
  if (micBtn.disabled || !recognition) return;
  window.speechSynthesis?.cancel();
  try { recognition.start(); } catch {}
}
function stopListening(e) {
  e.preventDefault();
  try { recognition.stop(); } catch {}
}

micBtn.addEventListener('mousedown',  startListening);
micBtn.addEventListener('touchstart', startListening, { passive: false });
micBtn.addEventListener('mouseup',    stopListening);
micBtn.addEventListener('touchend',   stopListening,  { passive: false });

connect();
</script>
</body>
</html>"""
```

- [ ] **Step 2: Start server and open UI in browser**

```bash
cd ~/voice-claude && python3 server.py &
```

Open `http://localhost:8899` in Chrome or Safari.

Manual checks:
- [ ] Page renders with dark background, top bar, sticky mic button at bottom
- [ ] Status chip shows `connecting` then `ready`
- [ ] Pane dropdown populates with your tmux panes
- [ ] Pane with `claude` in command appears first
- [ ] Hold mic button — status changes to `listening`, button turns red
- [ ] Speak a short phrase — interim text appears on page
- [ ] Release — command sends, status changes to `processing`
- [ ] Response streams in line by line
- [ ] On completion, `speechSynthesis` reads response aloud
- [ ] Page scrolls smoothly with finger/mouse
- [ ] Mic button re-enables after response

- [ ] **Step 3: Test from Tailscale device**

On your phone or tablet, open `http://100.124.184.126:8899` in Chrome or Safari.

Manual checks:
- [ ] Page loads over Tailscale
- [ ] Hold-to-talk works with touch
- [ ] Page scrolls with finger to read long responses

- [ ] **Step 4: Stop test server**

```bash
kill %1
```

- [ ] **Step 5: Commit**

```bash
cd ~/voice-claude && git add server.py
git commit -m "feat: complete HTML/JS UI with Web Speech API and TTS"
```

---

## Task 7: Systemd user service + user config files

**Files:**
- Create: `~/voice-claude/voice-claude.service`

**Interfaces:**
- Produces: auto-starting server for any user who follows the setup steps; service reads `~/.config/voice-claude/config.json` at startup

- [ ] **Step 1: Create systemd service template**

Create `~/voice-claude/voice-claude.service`:

```ini
[Unit]
Description=Voice-Claude server
After=network.target

[Service]
ExecStart=/usr/bin/python3 /home/dananickerson/voice-claude/server.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

- [ ] **Step 2: Install and start service for dananickerson**

```bash
mkdir -p ~/.config/systemd/user
cp ~/voice-claude/voice-claude.service ~/.config/systemd/user/voice-claude.service
systemctl --user daemon-reload
systemctl --user enable voice-claude
systemctl --user start voice-claude
```

- [ ] **Step 3: Verify service is running**

```bash
systemctl --user status voice-claude
```

Expected: `active (running)` with the port logged

```bash
curl -s http://localhost:8899/panes
```

Expected: JSON array of panes

- [ ] **Step 4: Set up jnickerson (run as jnickerson)**

```bash
sudo -u jnickerson bash -c "
  mkdir -p ~/.config/voice-claude ~/.config/systemd/user
  cat > ~/.config/voice-claude/config.json <<'EOF'
{
  \"port\": 8900,
  \"projects\": []
}
EOF
  cp /home/dananickerson/voice-claude/voice-claude.service ~/.config/systemd/user/voice-claude.service
  systemctl --user daemon-reload
  systemctl --user enable voice-claude
  systemctl --user start voice-claude
"
```

Note: jnickerson should edit `~/.config/voice-claude/config.json` to add their own project paths.

- [ ] **Step 5: Set up amanda (run as amanda)**

```bash
sudo -u amanda bash -c "
  mkdir -p ~/.config/voice-claude ~/.config/systemd/user
  cat > ~/.config/voice-claude/config.json <<'EOF'
{
  \"port\": 8901,
  \"projects\": []
}
EOF
  cp /home/dananickerson/voice-claude/voice-claude.service ~/.config/systemd/user/voice-claude.service
  systemctl --user daemon-reload
  systemctl --user enable voice-claude
  systemctl --user start voice-claude
"
```

- [ ] **Step 6: Verify all three services are running**

```bash
systemctl --user status voice-claude
sudo -u jnickerson systemctl --user status voice-claude
sudo -u amanda systemctl --user status voice-claude
```

Expected: all three show `active (running)`

```bash
curl -s http://localhost:8899/panes | python3 -m json.tool | head -5
curl -s http://localhost:8900/ | head -1
curl -s http://localhost:8901/ | head -1
```

Expected: all three return valid responses

- [ ] **Step 7: Commit**

```bash
cd ~/voice-claude && git add voice-claude.service
git commit -m "feat: systemd user service template and per-user setup"
```

---

## Self-Review

**Spec coverage:**
- [x] Voice commands over Tailscale — Task 6 (Tailscale URL tested in Task 6 Step 3)
- [x] Web Speech API STT — Task 6 (SpeechRecognition)
- [x] WebSocket injection into tmux — Task 5 (send-keys)
- [x] Response streaming — Task 5 (capture_response generator)
- [x] TTS via speechSynthesis — Task 6 (endResponse function)
- [x] Scrollable full-page layout — Task 6 (natural document flow + padding)
- [x] Project-labeled pane picker — Tasks 2 + 6
- [x] Panes running claude sort first — Task 2 (parse_panes sort)
- [x] Per-user config — Tasks 1 + 7
- [x] Per-user systemd service — Task 7
- [x] 1.5s completion detection — Task 3 (3 ticks × 0.5s)
- [x] 45s timeout — Task 3 (90 polls × 0.5s)
- [x] In-flight cancellation — Task 5 (capture_task.cancel)
- [x] No new pip installs — websockets + pytest already present
- [x] Three users on ports 8899/8900/8901 — Tasks 1 + 7

**Placeholder scan:** None found.

**Type consistency:** `diff_output(before_count: int, after_lines: list) -> str` used consistently in Tasks 3 and 5. `get_panes(projects: list) -> list` consistent between Tasks 2 and 4.
