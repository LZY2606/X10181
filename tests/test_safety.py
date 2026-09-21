import pytest

from gcode_review.compare import compare_programs
from gcode_review.interpreter import interpret_text
from gcode_review.lexer import parse_program
from gcode_review.safety import Box, analyze_comparison, analyze_version

BOX = Box(min=(-10, -10, 0), max=(10, 10, 30))


def test_rapid_through_stock_numeric():
    # 从 (0,0,50) 快速到 (0,0,-20)：进入点必须恰为 z=30 面
    r = interpret_text(b"G21 G90 G54\nG0 X0 Y0 Z50\nG0 Z-20\nM30\n")
    findings = analyze_version(r, BOX)
    hit = [f for f in findings if f.code == "rapid_through_stock"
           and f.line == 2]
    assert hit, findings
    assert hit[0].values["entry"] == [0.0, 0.0, 30.0]
    assert hit[0].severity == "danger"


def test_rapid_above_stock_is_safe():
    # 先把刀定位到毛坯上方 z=40，之后所有快速移动保持在盒顶 z=30 之上
    r = interpret_text(b"G21 G90\nG0 X0 Y0 Z40\nG0 X-5 Y0 Z40\nG0 X5 Y0 Z40\n")
    assert not [f for f in analyze_version(r, BOX)
                if f.code == "rapid_through_stock"]


def test_feed_through_stock_allowed():
    # 进给穿过毛坯不告警；仅最终快速抬刀从盒内穿出（line 3）才报
    r = interpret_text(
        b"G21 G90\nG0 X0 Y0 Z50\nG0 X0 Y0 Z50\nG1 Z-20 F100\nG0 Z50\n")
    feed_alerts = [f for f in analyze_version(r, BOX)
                   if f.code == "rapid_through_stock" and f.line == 2]
    assert not feed_alerts


def test_diagonal_rapid_entry_point_numeric():
    # 先定位到盒外 (0,0,50)，再斜向快速到 (20,0,-10)
    r = interpret_text(
        b"G21 G90\nG0 X0 Y0 Z50\nG0 X0 Y0 Z50\nG0 X20 Y0 Z-10\n")
    findings = [f for f in analyze_version(r, BOX)
                if f.code == "rapid_through_stock"]
    # z=30 时 t=(50-30)/60=1/3, x=20/3
    e = findings[0].values["entry"]
    assert e[2] == pytest.approx(30.0, abs=1e-4)
    assert e[0] == pytest.approx(20.0 / 3, abs=1e-4)


def test_unit_not_restored():
    r = interpret_text(b"G21 G90\nG0 X1\nG20\nM30\n")
    f = [x for x in analyze_version(r, None) if x.code == "unit_not_restored"]
    assert f and f[0].values == {"start": "mm", "end": "inch"}


def test_unit_change_missing_in_diff_is_danger():
    c = compare_programs(parse_program(b"G21 G90\nG0 X1\n"),
                         parse_program(b"G20 G90\nG0 X1\n"))
    rep = analyze_comparison(c, BOX)
    assert rep.verdict == "unsafe"
    assert any(f.code == "unit_change_in_diff" for f in rep.findings)


def test_comp_left_on_and_cycle_not_cancelled():
    r = interpret_text(
        b"G21 G90\nG41 D1 G1 X1 F10\nG43 H1 Z2\nG81 R1 Z-2\n")
    codes = {f.code for f in analyze_version(r, None)}
    assert {"cutter_comp_left_on", "tool_length_left_on",
            "cycle_not_cancelled"} <= codes


def test_unknown_blocks_safe_conclusion():
    c = compare_programs(parse_program(b"G21 G90\nG0 X1\n"),
                         parse_program(b"G21 G90\nG0 X1\nG187 P3\n"))
    rep = analyze_comparison(c, BOX)
    # 未知厂商指令必须阻止“安全”结论：直接判危险（需人工确认语义）
    assert rep.verdict == "unsafe"
    assert any(f.code == "unrecognized_vendor_instruction"
               for f in rep.findings)


def test_format_only_diff_is_safe():
    c = compare_programs(parse_program(b"G21 G90 G54\nG0 X1 Y1 Z40\n"),
                         parse_program(b"g21 G90 G54 (mm)\nG00 X1.0 Y1 Z40\n"))
    assert analyze_comparison(c, BOX).verdict == "safe"


def test_identical_programs_are_safe():
    text = b"G21 G90\nG0 X1 Y1 Z40\nM30\n"
    c = compare_programs(parse_program(text), parse_program(text))
    rep = analyze_comparison(c, BOX)
    assert rep.verdict == "safe"
