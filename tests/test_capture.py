from server import diff_output


def test_diff_output_new_lines():
    before_count = 5
    after_lines = ["a", "b", "c", "d", "e", "new1", "new2"]
    assert diff_output(before_count, after_lines) == "new1\nnew2"


def test_diff_output_no_change():
    assert diff_output(5, ["a", "b", "c", "d", "e"]) == ""


def test_diff_output_empty_before():
    assert diff_output(0, ["line1", "line2"]) == "line1\nline2"


def test_diff_output_fewer_lines_than_before():
    # pane was cleared; return empty rather than negative slice
    assert diff_output(10, ["a", "b"]) == ""
