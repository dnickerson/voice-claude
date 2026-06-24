# Voice-Claude Design Spec
**Date:** 2026-06-24  
**Status:** Approved

## Overview

A locally-hosted voice interface that lets users send voice commands from a phone or tablet (over Tailscale) to a running Claude Code session inside an existing tmux pane. Speech-to-text happens in the browser via the Web Speech API. The server injects the transcribed command into the selected tmux pane via `tmux send-keys`, captures the response output, streams it back to the browser, and reads it aloud via `speechSynthesis`.

Three users share the same server code but each runs an isolated instance under their own system account, on their own port, seeing only their own tmux sessions and configured with their own project list.

---

## Goals

- Hands-free voice command delivery to an active Claude Code tmux session
- Works from any phone or tablet connected to Tailscale
- Full response visible and scrollable; also read aloud via TTS
- No new system dependencies — built on `websockets` (already installed) and Python stdlib
- Per-user isolation: each user sees only their own tmux panes
- Auto-start via systemd user service; no manual intervention after setup

## Non-Goals

- Audio transcription on the server (Whisper, etc.)
- Authentication beyond Tailscale network membership
- Starting new tmux sessions or claude processes
- Recording or logging voice or responses
- Cross-user pane access

---

## Multi-User Model

Each user runs `server.py` under their own system account as a **systemd user service**. The service reads a per-user config file (`~/.config/voice-claude/config.json`) for port and project map. Complete isolation: one user's server cannot see another user's tmux sessions.

| User | Port | URL |
|---|---|---|
| dananickerson | 8899 | `http://100.124.184.126:8899` |
| jnickerson | 8900 | `http://100.124.184.126:8900` |
| amanda | 8901 | `http://100.124.184.126:8901` |

Each user bookmarks their own URL on their phone/tablet.

---

## Per-User Config

Loaded from `~/.config/voice-claude/config.json` at server startup. If the file is absent, server exits with a clear error message.

```json
{
  "port": 8899,
  "projects": [
    {"label": "flytab",    "path": "~/flytab"},
    {"label": "knitfit",   "path": "~/knitfit/platform"},
    {"label": "sharedlens","path": "~/sharedlens"},
    {"label": "engine",    "path": "~/engine_analysis"},
    {"label": "hypertherm","path": "~/hypertherm-project"},
    {"label": "home",      "path": "~"}
  ]
}
```

`~` in paths is expanded to the running user's home directory at startup. jnickerson and amanda have their own `config.json` with their own project lists and assigned ports.

---

## Architecture

Single Python file: `~/voice-claude/server.py` (shared, not copied per user — installed once, run by each user via their own systemd service).

Endpoints on the user's configured port:

| Endpoint | Protocol | Purpose |
|---|---|---|
| `GET /` | HTTP | Serve the voice UI HTML |
| `GET /panes` | HTTP | Return active tmux panes as JSON |
| `/ws` | WebSocket | Receive commands, stream responses |

---

## Server Component

### Startup

Reads `~/.config/voice-claude/config.json`. Expands `~` paths. Binds to `0.0.0.0:<port>`. Uses `websockets.serve()` with a `process_request` handler to serve HTTP GET requests alongside WebSocket upgrades.

### `GET /panes`

Runs:
```
tmux list-panes -a -F "#{session_name}:#{window_index}.#{pane_index}|#{pane_current_command}|#{pane_current_path}"
```

Returns JSON array with label matched from config:
```json
[
  {"id": "work:0.0", "command": "claude", "path": "/home/dananickerson/flytab", "label": "flytab"},
  {"id": "work:1.0", "command": "bash",   "path": "/home/dananickerson/knitfit/platform", "label": "knitfit"}
]
```

Label is matched by longest prefix of `pane_current_path` against the configured project paths. If no match, falls back to the last path segment. Panes running `claude` sort first.

### WebSocket `/ws`

Receives JSON:
```json
{"text": "add error handling to the auth module", "pane": "work:0.0"}
```

Response flow:
1. Snapshot current pane content: `tmux capture-pane -t <pane> -p -S -500`
2. Inject command: `tmux send-keys -t <pane> "<text>" Enter`
3. Poll every 500ms: `tmux capture-pane -t <pane> -p -S -500`
4. Diff each poll against snapshot; stream new lines to client as `{"chunk": "..."}`
5. Detect completion: no new output for **1.5 seconds** → send `{"done": true}`
6. Timeout: if no completion after **45 seconds** → send `{"done": true, "timeout": true}`

Error cases:
- No tmux session / invalid pane → `{"error": "Pane not found"}`
- tmux not available → `{"error": "tmux not running"}`
- Config missing → server exits at startup with a clear message; does not serve

### Process Management

Each WebSocket connection handles one command at a time. If a new command arrives while one is in flight, cancel the in-flight poll and start fresh. Single-user per instance; no cross-connection concurrency needed.

---

## UI Component

Single HTML page served inline from `server.py`. No build step, no external JS libraries.

### Layout

```
┌─────────────────────────────┐  ← fixed top bar
│  [pane picker ▼]  [↻]  [●] │     pane dropdown + refresh + status chip
├─────────────────────────────┤
│                             │
│  You said:                  │  ← scrollable page body (natural document flow)
│  "add error handling..."    │
│                             │
│  Claude:                    │
│  [streaming response text]  │
│  [more text...]             │
│  [more text...]             │
│                             │
├─────────────────────────────┤
│       [ 🎤  Hold to talk ]  │  ← sticky bottom bar
└─────────────────────────────┘
```

### Top Bar (fixed position)

- **Pane picker dropdown**: populated from `GET /panes` on load. Shows `label — session:window.pane` (e.g., `flytab — work:0.0`). Panes running `claude` appear first.
- **Refresh button (↻)**: re-fetches `/panes` and rebuilds dropdown.
- **Status chip**: one of `connecting` / `ready` / `listening` / `processing` / `error`. Color-coded.

### Scrollable Body

- Natural document flow — no fixed-height container. The whole page scrolls with a finger.
- "You said:" section shows transcribed text for each command.
- "Claude:" section streams response chunks as they arrive.
- Each new command appends below the previous one; history accumulates in the page.
- `touch-action: pan-y` ensures native touch scrolling on mobile.

### Sticky Bottom Bar

- Large **hold-to-talk** button. `touchstart`/`mousedown` starts `SpeechRecognition`; `touchend`/`mouseup` stops it.
- Shows `Listening...` while active.
- On final transcript: sends `{text, pane}` via WebSocket.
- Disabled (greyed out) while `processing`.
- Tapping while TTS is playing cancels TTS so the user can speak immediately.

### Speech Recognition

```javascript
const recognition = new webkitSpeechRecognition();
recognition.continuous = false;
recognition.interimResults = true;
recognition.lang = 'en-US';
```

Interim results shown in grey; final result shown in black and sent.

### TTS (speechSynthesis)

On `{"done": true}`: reads the full accumulated response aloud. Tapping the mic button cancels speech and starts listening.

---

## Deployment

### Installation (once, by dananickerson)

```bash
# Install server code
mkdir -p ~/voice-claude
# place server.py at ~/voice-claude/server.py
```

### Per-User Setup (each user on their account)

```bash
# 1. Create config
mkdir -p ~/.config/voice-claude
# edit ~/.config/voice-claude/config.json with port + projects

# 2. Install systemd user service
mkdir -p ~/.config/systemd/user
# place voice-claude.service at ~/.config/systemd/user/voice-claude.service

# 3. Enable and start
systemctl --user enable voice-claude
systemctl --user start voice-claude
```

### Systemd User Service Template

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

The `ExecStart` path to `server.py` is the same for all users (it lives in dananickerson's home). Each user's service reads their own `~/.config/voice-claude/config.json`.

Tailscale provides the security boundary — no login screen, no TLS needed on the Tailscale LAN.

---

## File Structure

```
~/voice-claude/                          ← owned by dananickerson, shared read
  server.py                             ← single server file, all users run this
  docs/
    superpowers/
      specs/
        2026-06-24-voice-claude-design.md

~/.config/voice-claude/config.json      ← per user (each user's home)
~/.config/systemd/user/voice-claude.service  ← per user (each user's home)
```
