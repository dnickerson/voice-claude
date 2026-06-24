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

# ── Response capture ──────────────────────────────────────────────────────────

async def _capture_pane_lines(pane_id: str) -> list:
    proc = await asyncio.create_subprocess_exec(
        "tmux", "capture-pane", "-t", pane_id, "-p", "-S", "-2000",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(stderr.decode().strip() or f"tmux capture-pane failed for {pane_id}")
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

# ── HTML UI ───────────────────────────────────────────────────────────────────
# (added in Task 6)

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

<div id="content">
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
      if (!currentResponseEl) startExchange('[server error]');
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
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
           .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
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
    capture_task = None

    async def run_command(text: str, pane: str) -> None:
        nonlocal capture_task
        if capture_task and not capture_task.done():
            capture_task.cancel()
            try:
                await capture_task
            except asyncio.CancelledError:
                pass

        # Allowlist check: pane must be in the current pane list
        valid_panes = await get_panes(PROJECTS)
        valid_ids = {p["id"] for p in valid_panes}
        if pane not in valid_ids:
            await websocket.send(json.dumps({"error": f"Pane not found: {pane}"}))
            return

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
            cancelled = False
            try:
                async for chunk in capture_response(pane, before_count):
                    if chunk is None:
                        timed_out = True
                        break
                    await websocket.send(json.dumps({"chunk": chunk}))
            except asyncio.CancelledError:
                cancelled = True
                raise
            finally:
                if not cancelled:
                    try:
                        await websocket.send(json.dumps({"done": True, "timeout": timed_out}))
                    except Exception:
                        pass

        capture_task = asyncio.create_task(stream())

    try:
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
    finally:
        if capture_task and not capture_task.done():
            capture_task.cancel()
            try:
                await capture_task
            except asyncio.CancelledError:
                pass


# ── Entry point ───────────────────────────────────────────────────────────────

async def main() -> None:
    config = load_config()
    expand_paths(config)
    validate_config(config)
    global PROJECTS
    PROJECTS = config["projects"]
    port = config["port"]
    print(f"Voice-Claude on http://0.0.0.0:{port}")
    stop = asyncio.get_running_loop().create_future()
    async with serve(ws_handler, "0.0.0.0", port, process_request=process_request):
        await stop


if __name__ == "__main__":
    asyncio.run(main())
