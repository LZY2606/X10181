"""字节补丁：未改行严格字节一致；补丁由原始文本显式应用。"""
from app.interpreter import interpret
from app.compare import compare_models
from app.patching import apply_patch, build_patch


def test_unchanged_lines_byte_identical():
    src = b"G21 G90   ( units )\r\nG1 X1 F100\r\nG1 Y2\r\nM30"
    out = apply_patch(src, [{"op": "replace", "line": 1,
                             "text": "G1 X2 F100"}])
    lines = out.split(b"\r\n")
    assert lines[0] == b"G21 G90   ( units )"
    assert lines[2] == b"G1 Y2"
    assert lines[3] == b"M30"
    assert out.startswith(b"G21 G90   ( units )\r\n")


def test_insert_and_delete():
    src = b"G21\nG1 X1\nM30\n"
    out = apply_patch(src, [
        {"op": "insert", "after": 0, "text": "G0 Z50"},
        {"op": "delete", "line": 1},
    ])
    assert out == b"G21\nG0 Z50\nM30\n"


def test_insert_at_beginning():
    src = b"G21\nM30\n"
    out = apply_patch(src, [{"op": "insert", "after": -1, "text": "%"}])
    assert out == b"%\nG21\nM30\n"


def test_replace_preserves_crlf():
    src = b"G21\r\nG1 X1\r\n"
    out = apply_patch(src, [{"op": "replace", "line": 1, "text": "G1 X2"}])
    assert out == b"G21\r\nG1 X2\r\n"


def test_build_patch_roundtrip():
    a_text = b"G21 G90\nG1 X10 F100\nG1 Y10\nM30\n"
    b_text = b"G21 G90 (mm)\nG1 X12 F100\nG0 Z5\nG1 Y10\nM30\n"
    ma, mb = interpret(a_text), interpret(b_text)
    cmp_report = compare_models(ma, mb)
    ops = build_patch(cmp_report["alignment"], mb.lines, a_text)
    out = apply_patch(a_text, ops)
    assert out == b_text


def test_patch_rejects_overlap():
    import pytest
    from app.patching import PatchError
    with pytest.raises(PatchError):
        apply_patch(b"G21\n", [{"op": "replace", "line": 0, "text": "a"},
                               {"op": "delete", "line": 0}])
