from server import _extract_response

SEP = "─" * 55  # long enough to trigger the input-area trim


def _pane(lines: list[str]) -> str:
    return "\n".join(lines)


def test_extract_basic():
    text = _pane([
        "❯ say hello",
        "",
        "● Hello there!",
        SEP,
        "❯ ",
        SEP,
        "  status bar",
    ])
    assert "Hello there!" in _extract_response(text, "say hello")


def test_extract_command_not_found():
    text = _pane(["● some previous response", "❯ "])
    assert _extract_response(text, "new command") == ""


def test_extract_multiline():
    text = _pane([
        "❯ list three things",
        "",
        "● 1. Apples",
        "  2. Bananas",
        "  3. Cherries",
        SEP,
        "❯ ",
    ])
    result = _extract_response(text, "list three things")
    assert "Apples" in result
    assert "Bananas" in result
    assert "Cherries" in result


def test_extract_streaming_no_sep_yet():
    # No input-area separator yet — response still streaming
    text = _pane([
        "❯ explain recursion",
        "",
        "● Recursion is when a function calls itself.",
    ])
    assert "Recursion" in _extract_response(text, "explain recursion")


def test_extract_preserves_short_separators_in_response():
    # Short ─ lines inside the response should NOT trigger trimming
    text = _pane([
        "❯ show table",
        "",
        "● Col1 | Col2",
        "  -----|-----",
        "  A    | B",
        SEP,
        "❯ ",
    ])
    result = _extract_response(text, "show table")
    assert "Col1" in result
    assert "A    | B" in result


def test_extract_excludes_ui_chrome():
    # The ❯ in the input area must NOT appear in the returned response
    text = _pane([
        "❯ test",
        "● Done.",
        SEP,
        "❯ ",
        SEP,
        "  status bar",
    ])
    result = _extract_response(text, "test")
    assert "Done." in result
    # The bare ❯ from the input area should be excluded
    assert result.strip().endswith("Done.")
