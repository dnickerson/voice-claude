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

# ── HTML UI ───────────────────────────────────────────────────────────────────
# (added in Task 6)

HTML = "<html><body>placeholder</body></html>"

# ── HTTP + WebSocket handlers ─────────────────────────────────────────────────
# (added in Task 4)

# ── Entry point ───────────────────────────────────────────────────────────────
# (added in Task 4)
