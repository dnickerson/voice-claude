from server import _extract_response


def _pane(lines: list[str]) -> str:
    return "\n".join(lines)


def test_extract_response_basic():
    text = _pane([
        "❯ say hello",
        "",
        "● Hello!",
        "──────────────────────────────────",
        "❯ ",
        "──────────────────────────────────",
        "  status bar",
    ])
    response, done = _extract_response(text, "say hello")
    assert "Hello!" in response
    assert done is True


def test_extract_response_still_streaming():
    # No bare ❯ prompt yet — Claude still responding
    text = _pane([
        "❯ explain recursion",
        "",
        "● Recursion is when a function calls itself.",
        "  More detail coming…",
    ])
    response, done = _extract_response(text, "explain recursion")
    assert "Recursion" in response
    assert done is False


def test_extract_response_command_not_yet_visible():
    text = _pane([
        "● some previous response",
        "❯ ",
    ])
    response, done = _extract_response(text, "new command")
    assert response == ""
    assert done is False


def test_extract_response_strips_separators():
    text = _pane([
        "❯ test",
        "──────────────────────────────",
        "● Result here",
        "──────────────────────────────",
        "❯ ",
    ])
    response, done = _extract_response(text, "test")
    assert "──" not in response
    assert "Result here" in response
    assert done is True


def test_extract_response_multiline():
    text = _pane([
        "❯ list three things",
        "",
        "● 1. Apples",
        "  2. Bananas",
        "  3. Cherries",
        "──────────────────────────────",
        "❯ ",
    ])
    response, done = _extract_response(text, "list three things")
    assert "Apples" in response
    assert "Bananas" in response
    assert "Cherries" in response
    assert done is True
