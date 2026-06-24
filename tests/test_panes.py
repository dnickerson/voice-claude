from server import match_label, parse_panes

PROJECTS = [
    {"label": "flytab",  "path": "/home/dana/flytab"},
    {"label": "knitfit", "path": "/home/dana/knitfit/platform"},
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
    projects = [
        {"label": "flytab",  "path": "/home/dana/flytab"},
        {"label": "knitfit", "path": "/home/dana/knitfit/platform"},
        {"label": "home",    "path": "/home/dana"},
    ]
    assert match_label("/home/dana", projects) == "home"


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
