#!/usr/bin/env python3
import asyncio
import http
import json
import re
import ssl
import sys
from pathlib import Path

from websockets.asyncio.server import serve, ServerConnection
from websockets.http11 import Response
from websockets.datastructures import Headers

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
    for key in ("tls_cert", "tls_key"):
        if key in config:
            config[key] = config[key].replace("~", home, 1)
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


async def snapshot_pane(pane_id: str) -> str:
    lines = await _capture_pane_lines(pane_id)
    return "\n".join(lines)


def _extract_response(pane_text: str, command: str) -> str:
    """
    Find command marker in pane text and return Claude's response.

    Trims at the terminal input area (long ─ separator + ❯ prompt, 50+ chars)
    to exclude Claude Code's UI chrome.  Short ─ sequences inside the response
    (markdown rules, table borders) are preserved.

    Claude Code TUI layout after a command:
        ❯ <command>
        ● <response…>
        ─────────────────── (50+ chars, full terminal width)
        ❯                   (input area — always present, not a reliable done signal)
        ───────────────────
        <status bar>
    """
    # Claude Code's input area uses a non-breaking space (U+00A0) after ❯,
    # but the conversation history uses a regular space — search both.
    for space in (' ', ' '):
        marker = f"❯{space}{command}"
        pos = pane_text.find(marker)
        if pos != -1:
            break
    if pos == -1:
        return ""

    after = pane_text[pos + len(marker):]

    # Trim at the terminal input area: a long ─ separator (50+ chars) followed
    # by ❯ (with optional trailing whitespace / NBSP on the separator line).
    input_area = re.search(r'\n─{50,}[ ─]*\n❯', after)
    if input_area:
        after = after[:input_area.start()]

    return after.strip()


async def capture_response(pane_id: str, before_text: str, command: str):
    sent_len = 0
    no_change_ticks = 0
    for _ in range(90):  # 45-second ceiling
        await asyncio.sleep(0.5)
        lines = await _capture_pane_lines(pane_id)
        text = "\n".join(lines)
        response = _extract_response(text, command)
        if len(response) > sent_len:
            yield response[sent_len:]
            sent_len = len(response)
            no_change_ticks = 0
        else:
            no_change_ticks += 1
            if no_change_ticks >= 8:  # 4s of no new output = done
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
.s-processing { background: #451a03; color: #fb923c; }
.s-error      { background: #450a0a; color: #f87171; }

#content {
  padding: 70px 16px 100px;
  min-height: 100vh;
  touch-action: pan-y;
}
.exchange { margin-bottom: 28px; border-top: 1px solid #222; padding-top: 16px; }
.you-label  { font-size: 12px; color: #6b7280; margin-bottom: 4px; }
.you-text   { font-size: 15px; color: #9ca3af; margin-bottom: 12px; font-style: italic; }
.claude-label { font-size: 12px; color: #60a5fa; margin-bottom: 6px; }
.claude-text  { font-size: 15px; line-height: 1.65; white-space: pre-wrap; color: #e5e7eb; }

#bottombar {
  position: fixed; bottom: 0; left: 0; right: 0; z-index: 100;
  background: #1e1e1e; border-top: 1px solid #333;
  padding: 10px 12px;
}
#input-row { display: flex; gap: 8px; align-items: flex-end; }
#text-input {
  flex: 1; background: #2a2a2a; color: #e0e0e0;
  border: 1px solid #444; border-radius: 12px;
  padding: 12px 14px; font-size: 16px; font-family: inherit;
  line-height: 1.4; resize: none; overflow-y: auto;
  max-height: 120px;
}
#text-input:focus { outline: none; border-color: #3b82f6; }
#send-btn {
  background: #1d4ed8; color: #fff; border: none; border-radius: 12px;
  padding: 12px 20px; font-size: 16px; font-weight: 600; cursor: pointer;
}
#send-btn:disabled { background: #1f2937; color: #4b5563; cursor: not-allowed; }
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
    Select a pane above, then type or dictate below and tap Send.
  </div>
</div>

<div id="bottombar">
  <div id="input-row">
    <textarea id="text-input" rows="1" placeholder="Type or use keyboard mic…" disabled></textarea>
    <button id="send-btn" disabled>Send</button>
  </div>
</div>

<script>
const content    = document.getElementById('content');
const paneSelect = document.getElementById('pane-select');
const statusEl   = document.getElementById('status');
const textInput  = document.getElementById('text-input');
const sendBtn    = document.getElementById('send-btn');

// ── Status ───────────────────────────────────────────────────────────────────
function setStatus(s, label) {
  statusEl.className = `s-${s}`;
  statusEl.textContent = label || s;
}

// ── WebSocket ─────────────────────────────────────────────────────────────────
let ws, currentResponseEl, currentResponse = '';

function connect() {
  setStatus('connecting');
  setInputEnabled(false);
  const wsProto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(`${wsProto}//${location.host}/ws`);
  ws.onopen = () => { setStatus('ready'); setInputEnabled(true); loadPanes(); };
  ws.onclose = () => { setStatus('error', 'disconnected'); setInputEnabled(false); setTimeout(connect, 3000); };
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

function setInputEnabled(on) {
  textInput.disabled = !on;
  sendBtn.disabled   = !on;
}

// ── Pane picker ───────────────────────────────────────────────────────────────
async function loadPanes() {
  try {
    const panes = await fetch('/panes').then(r => r.json());
    paneSelect.innerHTML = panes.length
      ? panes.map(p => `<option value="${p.id}">[${p.label}] ${p.id} · ${p.command}</option>`).join('')
      : '<option value="">No panes found — start tmux + claude</option>';
  } catch {
    paneSelect.innerHTML = '<option value="">Error loading panes</option>';
  }
}
document.getElementById('refresh-btn').onclick = loadPanes;

// ── Content ───────────────────────────────────────────────────────────────────
function escHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
           .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

function startExchange(text) {
  currentResponse = '';
  const ex = document.createElement('div');
  ex.className = 'exchange';
  ex.innerHTML = `
    <div class="you-label">You</div>
    <div class="you-text">${escHtml(text)}</div>
    <div class="claude-label">Claude</div>
    <div class="claude-text"></div>`;
  content.appendChild(ex);
  currentResponseEl = ex.querySelector('.claude-text');
  ex.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function appendChunk(chunk) {
  currentResponse += chunk;
  currentResponseEl.textContent = currentResponse;
}

function endResponse(timedOut) {
  if (timedOut) appendChunk('\n[timed out after 45s]');
  setStatus('ready');
  setInputEnabled(true);
  textInput.focus();
}

// ── Send ──────────────────────────────────────────────────────────────────────
function sendCommand() {
  const text = textInput.value.trim();
  if (!text) return;
  const pane = paneSelect.value;
  if (!pane) { setStatus('error', 'no pane selected'); return; }
  textInput.value = '';
  textInput.style.height = '';
  startExchange(text);
  setStatus('processing');
  setInputEnabled(false);
  ws.send(JSON.stringify({ text, pane }));
}

sendBtn.addEventListener('click', sendCommand);
textInput.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendCommand(); }
});

// Auto-grow textarea up to max-height
textInput.addEventListener('input', () => {
  textInput.style.height = '';
  textInput.style.height = Math.min(textInput.scrollHeight, 120) + 'px';
});

connect();
</script>
</body>
</html>"""

# ── HTTP + WebSocket handlers ─────────────────────────────────────────────────

PROJECTS: list = []  # populated at startup


def _response(content_type: str, body: bytes) -> Response:
    return Response(200, "OK", Headers([
        ("Content-Type", content_type),
        ("Content-Length", str(len(body))),
    ]), body)


async def process_request(connection: ServerConnection, request) -> object:
    if request.path == "/":
        return _response("text/html; charset=utf-8", HTML.encode("utf-8"))
    if request.path == "/panes":
        panes = await get_panes(PROJECTS)
        return _response("application/json", json.dumps(panes).encode("utf-8"))
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
            before_text = await snapshot_pane(pane)
        except Exception:
            await websocket.send(json.dumps({"error": f"Pane not found: {pane}"}))
            return

        # Send text with -l (literal) so special characters aren't parsed as
        # key names, then send Enter in a separate call so it's always received.
        proc = await asyncio.create_subprocess_exec(
            "tmux", "send-keys", "-l", "-t", pane, text,
        )
        await proc.wait()
        proc = await asyncio.create_subprocess_exec(
            "tmux", "send-keys", "-t", pane, "Enter",
        )
        await proc.wait()

        async def stream() -> None:
            timed_out = False
            cancelled = False
            try:
                async for chunk in capture_response(pane, before_text, text):
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
    ssl_context = None
    if "tls_cert" in config and "tls_key" in config:
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_context.load_cert_chain(config["tls_cert"], config["tls_key"])
        print(f"Voice-Claude on https://0.0.0.0:{port}")
    else:
        print(f"Voice-Claude on http://0.0.0.0:{port}")
    stop = asyncio.get_running_loop().create_future()
    async with serve(ws_handler, "0.0.0.0", port, ssl=ssl_context, process_request=process_request):
        await stop


if __name__ == "__main__":
    asyncio.run(main())
