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
