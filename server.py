#!/usr/bin/env python3
import asyncio
import json
import re
import secrets
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


# Claude Code ≥ some version prefixes the input-area separator with ✶ (U+2736).
# Allow 0–2 arbitrary chars before the 50+ dashes so both ─────… and ✶─────… match.
_INPUT_AREA_RE = re.compile(r'\n.{0,2}─{50,}[ ─]*\n❯')


def _find_input_sep(text: str) -> int:
    m = _INPUT_AREA_RE.search(text)
    return m.start() if m else -1


def _extract_last_bullet_block(pane_text: str) -> str:
    """
    Extract the last ● response block visible above the input area separator.

    Fallback for when the ❯ <command> marker has scrolled off the TUI's
    alternate-screen buffer (which has no scrollback).
    """
    sep = _find_input_sep(pane_text)
    visible = pane_text[:sep] if sep != -1 else pane_text
    pos = visible.rfind('\n●')
    if pos == -1:
        return ""
    return visible[pos + 1:].strip()


def _extract_response(pane_text: str, command: str) -> str:
    """
    Find command marker in pane text and return Claude's response.

    Trims at the terminal input area (long ─ separator + ❯ prompt, 50+ chars)
    to exclude Claude Code's UI chrome.  Short ─ sequences inside the response
    (markdown rules, table borders) are preserved.

    Claude Code TUI layout after a command:
        ❯ <command>
        ● <response…>
        [✶]─────────────────── (50+ chars; ✶ prefix optional in newer Claude Code)
        ❯                     (input area)
    """
    # Claude Code's input area uses a non-breaking space (U+00A0) after ❯,
    # but the conversation history uses a regular space — search both.
    for space in (' ', '\xa0'):
        marker = f"❯{space}{command}"
        pos = pane_text.find(marker)
        if pos != -1:
            break
    if pos == -1:
        return ""

    after = pane_text[pos + len(marker):]

    sep = _find_input_sep(after)
    if sep != -1:
        after = after[:sep]

    return after.strip()


async def capture_response(pane_id: str, before_text: str, command: str):
    # Snapshot the last ● block before the command so the fallback can
    # distinguish a pre-existing response from the new one.
    before_bullet = _extract_last_bullet_block(before_text)

    sent_len = 0
    no_change_ticks = 0
    for _ in range(90):  # 45-second ceiling
        await asyncio.sleep(0.5)
        lines = await _capture_pane_lines(pane_id)
        text = "\n".join(lines)
        response = _extract_response(text, command)

        # Fallback: ❯ <command> has scrolled off the TUI's alternate screen.
        # The last ● block above the input separator IS the current response.
        if not response:
            current_bullet = _extract_last_bullet_block(text)
            if current_bullet and current_bullet != before_bullet:
                response = current_bullet

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
const TOKEN    = '__TOKEN__';
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
  ws = new WebSocket(`${wsProto}//${location.host}/ws?token=${TOKEN}`);
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
    const panes = await fetch(`/panes?token=${TOKEN}`).then(r => r.json());
    paneSelect.innerHTML = panes.length
      ? panes.map(p => `<option value="${escHtml(p.id)}">[${escHtml(p.label)}] ${escHtml(p.id)} · ${escHtml(p.command)}</option>`).join('')
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
TOKEN: str = ""      # set at startup
PORT: int = 0        # set at startup; used by origin check
HOSTNAME: str = ""   # set at startup; added to allowed WebSocket origins
RATE_LIMIT_SECONDS: float = 1.0


def _get_query_param(path: str, key: str) -> str:
    if "?" not in path:
        return ""
    query = path.split("?", 1)[1]
    for param in query.split("&"):
        if "=" in param:
            k, v = param.split("=", 1)
            if k == key:
                return v
    return ""


def _check_token(request) -> bool:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        candidate = auth[7:]
    else:
        candidate = _get_query_param(request.path, "token")
    if not candidate:
        return False
    return secrets.compare_digest(candidate, TOKEN)


def _check_origin(request) -> bool:
    """Allow same-host origins and clients that send no Origin header (curl, CLI)."""
    origin = request.headers.get("Origin", "")
    if not origin:
        return True
    allowed = {
        f"http://localhost:{PORT}",
        f"https://localhost:{PORT}",
        f"http://127.0.0.1:{PORT}",
        f"https://127.0.0.1:{PORT}",
    }
    if HOSTNAME and HOSTNAME not in ("localhost", "127.0.0.1", "0.0.0.0"):
        allowed.add(f"http://{HOSTNAME}:{PORT}")
        allowed.add(f"https://{HOSTNAME}:{PORT}")
    return origin in allowed


def _http_response(content_type: str, body: bytes) -> Response:
    return Response(200, "OK", Headers([
        ("Content-Type", content_type),
        ("Content-Length", str(len(body))),
    ]), body)


def _error_response(status: int, reason: str, message: bytes) -> Response:
    return Response(status, reason, Headers([
        ("Content-Type", "text/plain"),
        ("Content-Length", str(len(message))),
    ]), message)


async def process_request(connection: ServerConnection, request) -> object:
    path = request.path.split("?")[0]

    if path == "/favicon.ico":
        return Response(204, "No Content", Headers([("Content-Length", "0")]), b"")

    if not _check_token(request):
        return _error_response(401, "Unauthorized", b"Unauthorized")

    if path == "/":
        # token is urlsafe base64 (A-Za-z0-9_-) so injection is not possible
        html = HTML.replace("'__TOKEN__'", f"'{TOKEN}'")
        body = html.encode("utf-8")
        return _http_response("text/html; charset=utf-8", body)

    if path == "/panes":
        panes = await get_panes(PROJECTS)
        return _http_response("application/json", json.dumps(panes).encode("utf-8"))

    # WebSocket upgrade path — reject cross-origin requests
    if not _check_origin(request):
        return _error_response(403, "Forbidden", b"Forbidden")

    return None  # proceed with WebSocket upgrade


async def ws_handler(websocket) -> None:
    capture_task = None
    last_command_time = 0.0

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

                now = asyncio.get_running_loop().time()
                if now - last_command_time < RATE_LIMIT_SECONDS:
                    await websocket.send(json.dumps({"error": "Rate limited — wait before sending another command"}))
                    continue
                last_command_time = now

                await run_command(text, pane)
            except json.JSONDecodeError:
                await websocket.send(json.dumps({"error": "Invalid JSON"}))
            except Exception as e:
                print(f"[ERROR] ws_handler: {e}", file=sys.stderr)
                await websocket.send(json.dumps({"error": "Internal server error"}))
    finally:
        if capture_task and not capture_task.done():
            capture_task.cancel()
            try:
                await capture_task
            except asyncio.CancelledError:
                pass


# ── Entry point ───────────────────────────────────────────────────────────────

async def main() -> None:
    global PROJECTS, TOKEN, PORT, HOSTNAME
    config = load_config()
    expand_paths(config)
    validate_config(config)
    PROJECTS = config["projects"]
    PORT = config["port"]
    TOKEN = config.get("token") or secrets.token_urlsafe(24)
    bind = config.get("bind", "127.0.0.1")
    HOSTNAME = config.get("hostname") or bind

    ssl_context = None
    if "tls_cert" in config and "tls_key" in config:
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
        ssl_context.load_cert_chain(config["tls_cert"], config["tls_key"])
        proto = "https"
    else:
        print(
            "WARNING: TLS not configured — traffic is unencrypted. "
            "Add tls_cert and tls_key to config.json to enable HTTPS.",
            file=sys.stderr,
        )
        proto = "http"

    print(f"Voice-Claude: {proto}://{HOSTNAME}:{PORT}/?token={TOKEN}", flush=True)
    stop = asyncio.get_running_loop().create_future()
    async with serve(ws_handler, bind, PORT, ssl=ssl_context, process_request=process_request):
        await stop


if __name__ == "__main__":
    asyncio.run(main())
