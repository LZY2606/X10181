import pytest

from gcode_review.compare import compare_programs
from gcode_review.exporter import apply_patches, build_patches
from gcode_review.lexer import parse_program


CASES = [
    (b"G21 G90\nG0 X0 Y0\nG1 X10 F100\nY10\nM30\n",
     b"G21 G90\nG0 X0 Y0\nG1 X12 F100\nY10\nM30\n"),
    (b"G21 G90\nG0 X0\r\nM30\n",
     b"G21 G90\n(added comment)\nG0 X0\r\nM30\n"),
    (b"line0\nline1\nline2\nline3\n",
     b"line0\nline1 changed\nline3\n"),
]


@pytest.mark.parametrize("a,b", CASES)
def test_patches_reproduce_modified(a, b):
    c = compare_programs(parse_program(a), parse_program(b))
    patches = build_patches(c)
    assert apply_patches(a, patches) == b


def test_unchanged_lines_byte_identical():
    a = b"aaa\nunchanged-keep\nbbb\n"
    b = b"aaa\nunchanged-keep\nBBB\n"
    c = compare_programs(parse_program(a), parse_program(b))
    out = apply_patches(a, build_patches(c))
    # 未改行在产物中的字节内容完全一致
    assert b"unchanged-keep\n" in out
    # 且相对原文件前部未改区域字节偏移一致
    idx = a.index(b"unchanged-keep")
    assert out[idx:idx + len(b"unchanged-keep\n")] == b"unchanged-keep\n"


def test_tampered_anchor_rejected():
    a, b = CASES[0]
    c = compare_programs(parse_program(a), parse_program(b))
    patches = build_patches(c)
    patches[0].expected = b"tampered"
    with pytest.raises(ValueError):
        apply_patches(a, patches)


def test_patch_spans_are_original_byte_ranges():
    a, b = CASES[0]
    c = compare_programs(parse_program(a), parse_program(b))
    p = build_patches(c)[0]
    assert a[p.start:p.start + p.length] == p.expected
