"""解释器：绝对/增量、三个圆弧平面、坐标系、刀补、固定循环、未知指令。"""
import math

from app.interpreter import interpret
from app.geometry import arc_points


def seg_endpoints(model):
    return [(s.kind, tuple(round(v, 5) for v in s.points[-1]))
            for s in model.segments]


def test_absolute_vs_incremental_same_geometry():
    absolute = b"G21 G90\nG1 X10 Y0 F100\nG1 X10 Y10\nG1 X0 Y10\n"
    incremental = b"G21 G91\nG1 X10 Y0 F100\nG1 X0 Y10\nG1 X-10 Y0\n"
    ma, mb = interpret(absolute), interpret(incremental)
    ea = seg_endpoints(ma)
    eb = seg_endpoints(mb)
    assert ea == eb
    assert ea[1][1] == (10.0, 10.0, 0.0)


def test_incremental_change_propagates_downstream():
    a = interpret(b"G21 G91\nG1 X10\nG1 Y10\nG1 X-5\n")
    b = interpret(b"G21 G91\nG1 X12\nG1 Y10\nG1 X-5\n")
    # 后续 Y10 行的绝对位置：A 为 (10,10)，B 为 (12,10)
    ya = [s for s in a.segments if s.line_index == 1][0].points[-1]
    yb = [s for s in b.segments if s.line_index == 1][0].points[-1]
    assert abs(ya[0] - 10) < 1e-9 and abs(yb[0] - 12) < 1e-9


def test_arc_g17_ij_vs_r_equivalent():
    start = (0.0, 0.0, 0.0)
    end = (20.0, 0.0, 0.0)
    ij = arc_points(start, end, "G17", True, {"i": 10, "j": 0}, None, max_chord=0.02)
    rpos = arc_points(start, end, "G17", True, None, 10.0, max_chord=0.02)
    assert len(ij) == len(rpos)
    for p, q in zip(ij, rpos):
        for c1, c2 in zip(p, q):
            assert abs(c1 - c2) < 1e-6
    # 半圆圆心 (10,0)：G2 从 9 点钟方向顺时针经 12 点钟到 3 点钟
    near = min(ij, key=lambda p: (p[0] - 10) ** 2 + (p[1] - 10) ** 2)
    assert abs(near[0] - 10) < 0.05 and abs(near[1] - 10) < 0.05


def test_arc_three_planes_g2_direction():
    # G17: G2 从 (10,0) 到 (-10,0)，圆心原点，中点经过 (0,-10)
    g17 = arc_points((10, 0, 0), (-10, 0, 0), "G17", True,
                     {"i": -10, "j": 0}, None, max_chord=0.02)
    near = min(g17, key=lambda p: p[0] ** 2 + (p[1] + 10) ** 2)
    assert abs(near[0]) < 0.05 and abs(near[1] + 10) < 0.05
    # G18 平面 ZX（从 +Y 看）：G2 从 12 点钟 (z=10) 顺时针经 3 点钟到 6 点钟
    g18 = arc_points({"x": 0, "y": 0, "z": 10},
                     {"x": 0, "y": 0, "z": -10},
                     "G18", True, {"k": -10, "i": 0}, None, max_chord=0.02)
    near = min(g18, key=lambda p: (p[0] + 10) ** 2 + p[2] ** 2)
    assert abs(near[0] + 10) < 0.05 and abs(near[2]) < 0.05
    # G19 平面 YZ（从 +X 看）：G2 从 3 点钟 (y=10) 顺时针经 6 点钟到 9 点钟
    g19 = arc_points({"x": 0, "y": 10, "z": 0},
                     {"x": 0, "y": -10, "z": 0},
                     "G19", True, {"j": -10, "k": 0}, None, max_chord=0.02)
    near = min(g19, key=lambda p: p[1] ** 2 + (p[2] + 10) ** 2)
    assert abs(near[1]) < 0.05 and abs(near[2] + 10) < 0.05


def test_wcs_offset_switch_recorded():
    m = interpret(b"G21 G90 G54\nG1 X5\nG55\nG1 X5\n")
    assert m.lines[0].after["wcs"] == "G54"
    assert m.lines[2].after["wcs"] == "G55"
    assert m.lines[0].after_fp != m.lines[2].after_fp


def test_cutter_comp_modal():
    m = interpret(b"G21 G90\nG41 D1\nG1 X5 F100\nG40\n")
    assert m.lines[1].after["cutter_comp"] == "G41"
    assert m.lines[2].before["cutter_comp"] == "G41"
    assert m.final_state["cutter_comp"] == "G40"


def test_canned_cycle_expansion_and_g98_return():
    m = interpret(b"G21 G90\nG0 X0 Y0 Z50\n"
                  b"G81 G98 X10 Y10 R2 Z-5 F100\nX30 Y30\nG80\n")
    cyc = [s for s in m.segments if s.cycle]
    # 每孔：定位快移、Z向进R快移、切削、退回 = 4 段
    assert len([s for s in cyc if s.line_index == 2]) == 4
    retract = [s for s in cyc if s.line_index == 2][-1]
    assert retract.points[-1] == (10.0, 10.0, 50.0)
    second = [s for s in cyc if s.line_index == 3]
    assert second[0].points[0] == (10.0, 10.0, 50.0)
    assert m.final_state["cycle"] is None


def test_unknown_vendor_code_blocks_safe_conclusion():
    m = interpret(b"G21 G90\nM200\nG1 X5\nM30\n")
    assert m.blocked is True
    assert any("M200" in r for r in m.block_reasons)
    assert m.unknowns[0].code == "M200"
    assert m.unknowns[0].confirmed is False
    # 原文保留
    assert b"M200" in m.text


def test_macro_line_blocks():
    m = interpret(b"G21 G90\nG1 X[#1+2] F100\n")
    assert m.blocked is True
    assert any("宏" in r for r in m.block_reasons)


def test_confirmed_unknown_effects():
    resolver = lambda code: (
        {"effect": "no_effect", "note": "probe ack"} if code == "M200" else None)
    m = interpret(b"G21 G90\nM200\nG1 X5\nM30\n", resolver)
    assert m.blocked is False
    assert m.unknowns[0].confirmed is True
    assert m.unknowns[0].effect == "no_effect"

    resolver2 = lambda code: (
        {"effect": "motion_unverified", "note": "x"} if code == "M200" else None)
    m2 = interpret(b"G21 G90\nM200\nM30\n", resolver2)
    assert m2.blocked is True


def test_units_inch_converted_to_mm():
    mm = interpret(b"G21 G90\nG1 X1\n")
    inch = interpret(b"G20 G90\nG1 X1\n")
    xm = mm.segments[0].points[-1][0]
    xi = inch.segments[0].points[-1][0]
    assert abs(xm - 1.0) < 1e-9
    assert abs(xi - 25.4) < 1e-9
