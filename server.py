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
