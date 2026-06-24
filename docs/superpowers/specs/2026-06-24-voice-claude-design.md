# Voice-Claude Design Spec
**Date:** 2026-06-24  
**Status:** Approved

## Overview

A locally-hosted voice interface that lets Dana send voice commands from a phone or tablet (over Tailscale) to a running Claude Code session inside an existing tmux pane. Speech-to-text happens in the browser via the Web Speech API. The server injects the transcribed command into the selected tmux pane via `tmux send-keys`, captures the response output, streams it back to the browser, and reads it aloud via `speechSynthesis`.

---

## Goals

- Hands-free voice command delivery to an active Claude Code tmux session
- Works from any phone or tablet connected to Tailscale
- Full response visible and scrollable; also read aloud via TTS
- No new system dependencies — built on `websockets` (already installed) and Python stdlib

## Non-Goals

- Audio transcription on the server (Whisper, etc.)
- Authentication beyond Tailscale network membership
- Starting new tmux sessions or claude processes
- Recording or logging voice or responses

---

## Architecture

Single Python file: `~/voice-claude/server.py`

Runs on port **8899**, accessible at `http://100.124.184.126:8899` from any Tailscale device.

Two endpoints on the same port:

| Endpoint | Protocol | Purpose |
|---|---|---|
| `GET /` | HTTP | Serve the voice UI HTML |
| `GET /panes` | HTTP | Return active tmux panes as JSON |
| `/ws` | WebSocket | Receive commands, stream responses |

---

## Server Component

### Startup

Server binds to `0.0.0.0:8899`. Uses `websockets.serve()` with a `process_request` handler to serve HTTP GET requests before the WebSocket upgrade.

### `GET /panes`

Runs:
```
tmux list-panes -a -F "#{session_name}:#{window_index}.#{pane_index}|#{pane_current_command}|#{pane_current_path}"
```

Returns JSON array:
```json
[
  {"id": "work:0.0", "command": "claude", "path": "/home/dananickerson/flytab", "label": "flytab"},
  {"id": "work:1.0", "command": "bash", "path": "/home/dananickerson/knitfit/platform", "label": "knitfit"}
]
```

The `label` field is derived by matching `pane_current_path` against the known project map:

| Display Label | Path |
|---|---|
| flytab | `~/flytab` |
| knitfit | `~/knitfit/platform` |
| sharedlens | `~/sharedlens` |
| engine | `~/engine_analysis` |
| hypertherm | `~/hypertherm-project` |
| home | `~` |

If no match, the label falls back to the last path segment.

### WebSocket `/ws`

Receives JSON:
```json
{"text": "add error handling to the auth module", "pane": "work:0.0"}
```

Response flow:
1. Snapshot current pane content: `tmux capture-pane -t <pane> -p -S -500`
2. Inject command: `tmux send-keys -t <pane> "<text>" Enter`
3. Poll every 500ms: `tmux capture-pane -t <pane> -p -S -500`
4. Diff each poll against the snapshot; stream new lines to client as `{"chunk": "..."}`
5. Detect completion: no new output for **1.5 seconds** → send `{"done": true}`
6. Timeout: if no completion after **45 seconds** → send `{"done": true, "timeout": true}`

Error cases:
- No tmux session / invalid pane → send `{"error": "Pane not found"}`
- tmux not available → send `{"error": "tmux not running"}`

### Process Management

Each WebSocket connection handles one command at a time. If a new command arrives while one is in flight, cancel the in-flight poll and start fresh. The server is single-user (no concurrency management needed beyond this).

---

## UI Component

Single HTML file served inline from `server.py`. No build step, no external JS libraries.

### Layout

```
┌─────────────────────────────┐  ← fixed top bar
│  [pane picker ▼]  [↻]  [●] │     pane dropdown + refresh + status chip
├─────────────────────────────┤
│                             │
│  You said:                  │  ← scrollable page body
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

### Top Bar (fixed)

- **Pane picker dropdown**: populated from `GET /panes` on load. Shows `label — session:window.pane` (e.g., `flytab — work:0.0`). Panes running `claude` appear first.
- **Refresh button (↻)**: re-fetches `/panes` and rebuilds dropdown.
- **Status chip**: one of `connecting` / `ready` / `listening` / `processing` / `error`. Color-coded.

### Scrollable Body

- "You said:" section shows the transcribed text after each command.
- "Claude:" section streams response chunks in as they arrive via WebSocket.
- Each new command session appends below the previous one (history accumulates in the page).
- Touch scrolling enabled (`touch-action: pan-y`).

### Sticky Bottom Bar

- Large **hold-to-talk** button. On `touchstart`/`mousedown`: starts `SpeechRecognition`. On `touchend`/`mouseup`: stops recognition.
- Shows `Listening...` while active.
- On final result: sends `{text, pane}` via WebSocket.
- Disabled (greyed out) while `processing`.

### Speech Recognition

```javascript
const recognition = new webkitSpeechRecognition();
recognition.continuous = false;
recognition.interimResults = true;
recognition.lang = 'en-US';
```

Interim results shown in "You said:" as grey text; final result shown in black and sent.

### TTS (speechSynthesis)

On receiving `{"done": true}`: reads the full accumulated response aloud.  
User can tap the mic button to interrupt TTS before speaking again.

---

## Deployment

No systemd service in scope for this phase. User starts the server manually:

```bash
cd ~/voice-claude && python3 server.py
```

Access from phone/tablet: `http://100.124.184.126:8899`

Tailscale provides the security boundary — no login screen, no TLS needed on the LAN.

---

## File Structure

```
~/voice-claude/
  server.py          ← entire application (server + HTML inline)
  docs/
    superpowers/
      specs/
        2026-06-24-voice-claude-design.md
```
