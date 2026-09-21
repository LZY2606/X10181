import math

import pytest

from gcode_review.interpreter import interpret_text
from gcode_review.lexer import parse_program


def end_pos(text, **kw):
    r = interpret_text(text, **kw)
    return r.steps[-1].after.pos, r


def test_absolute_vs_incremental():
    pos_a, _ = end_pos(b"G21 G90\nG0 X10 Y10 Z5\n")
    pos_b, _ = end_pos(b"G21 G91\nG0 X10 Y10 Z5\n")
    assert pos_a == (10.0, 10.0, 5.0)
    # 增量：从初始 (0,0,0) 起也算 10，但两行增量会累加
    pos_c, _ = end_pos(b"G21 G91\nG0 X10\nX5\n")
    assert pos_c[0] == 15.0
    pos_d, _ = end_pos(b"G21 G90\nG0 X10\nX5\n")
    assert pos_d[0] == 5.0
    assert pos_b == (10.0, 10.0, 5.0)


def test_inch_units_converted_to_mm():
    pos, r = end_pos(b"G20 G90\nG0 X1 Z-0.5\n")
    assert pos[0] == pytest.approx(25.4)
    assert pos[2] == pytest.approx(-12.7)
    assert r.steps[1].after.units == "inch"


def test_three_arc_planes_axes():
    # G17 XY、G18 XZ、G19 YZ：90 度弧终点分别落在正确轴上
    r17 = interpret_text(b"G17 G90 G0 X1 Y0\nG3 X0 Y1 I-1 J0\n")
    assert r17.motions[-1].points[-1] == pytest.approx((0, 1, 0), abs=1e-6)
    r18 = interpret_text(b"G18 G90 G0 X1 Z0\nG3 X0 Z1 I-1 K0\n")
    assert r18.motions[-1].points[-1] == pytest.approx((0, 0, 1), abs=1e-6)
    r19 = interpret_text(b"G19 G90 G0 Y1 Z0\nG3 Y0 Z1 J-1 K0\n")
    assert r19.motions[-1].points[-1] == pytest.approx((0, 0, 1), abs=1e-6)


def test_ijk_and_r_geometrically_equivalent():
    r1 = interpret_text(b"G17 G90\nG0 X0 Y0\nG3 X1 Y1 I0 J1 F100\n")
    r2 = interpret_text(b"G17 G90\nG0 X0 Y0\nG3 X1 Y1 R1 F100\n")
    a = r1.motions[-1].points
    b = r2.motions[-1].points
    assert len(a) == len(b)
    for pa, pb in zip(a, b):
        for i in range(3):
            assert pa[i] == pytest.approx(pb[i], abs=1e-6)


def test_r_sign_picks_major_minor():
    minor = interpret_text(b"G17 G90 G0 X0 Y0\nG3 X1 Y1 R1\n").motions[-1]
    major = interpret_text(b"G17 G90 G0 X0 Y0\nG3 X1 Y1 R-1\n").motions[-1]
    ys_minor = [p[1] for p in minor.points]
    ys_major = [p[1] for p in major.points]
    assert max(ys_minor) == pytest.approx(1.0, abs=1e-6)
    assert min(ys_major) < -0.5  # 大弧绕远路
    assert not math.isclose(minor.points[len(minor.points)//2][0],
                            major.points[len(major.points)//2][0], abs_tol=1e-3)


def test_wcs_offsets_shift_machine_position_not_work_position():
    text = b"G21 G90 G54\nG0 X10 Y0 Z0\n"
    r = interpret_text(text, offsets={"G54": {"x": 100.0, "y": 200.0, "z": 0.0}})
    st = r.steps[-1]
    assert st.after.pos == (10.0, 0.0, 0.0)
    assert st.after.machine_pos() == (110.0, 200.0, 0.0)


def test_g54_to_g55_switch_uses_different_offset():
    r = interpret_text(
        b"G21 G90 G54\nG0 X0\nG55 G0 X0\n",
        offsets={"G54": {"x": 1.0, "y": 0, "z": 0},
                 "G55": {"x": 50.0, "y": 0, "z": 0}})
    assert r.steps[1].after.machine_pos()[0] == 1.0
    assert r.steps[2].after.machine_pos()[0] == 50.0


def test_tool_length_compensation_apply_and_cancel():
    r = interpret_text(b"G21 G90 G43 H1\nG0 Z1\nG49 G0 Z1\n",
                       tool_offsets={1: 7.0})
    assert r.steps[1].after.tool_length == "G43"
    assert r.steps[1].after.machine_pos()[2] == pytest.approx(8.0)
    assert r.steps[2].after.tool_length == "G49"
    assert r.steps[2].after.machine_pos()[2] == pytest.approx(1.0)


def test_cutter_comp_modal_state():
    r = interpret_text(b"G21 G90\nG41 D1 G1 X1 F100\nG40 X2\n")
    assert r.steps[1].after.cutter_comp == "G41"
    assert r.steps[2].after.cutter_comp == "G40"


def test_canned_cycle_g81_modal_repeat_and_cancel():
    r = interpret_text(b"G21 G90\nG0 Z5\nG81 R2 Z-10 F100\nX10\nG80\n")
    # 两个孔，每个 3 个运动（到R、钻、退），第二孔含 XY 定位
    second_hole = [m for m in r.motions if m.line_index == 3]
    assert [m.kind for m in second_hole] == ["rapid", "rapid", "plunge", "retract"]
    assert r.steps[4].after.cycle == 80.0
    # G80 后普通轴行不再钻孔
    extra = interpret_text(b"G21 G90\nG0 Z5\nG81 R2 Z-10 F100\nG80\nX1\n")
    assert [m.kind for m in extra.motions if m.line_index == 4] == ["feed"]


def test_g82_dwell_and_g83_peck():
    g82 = interpret_text(b"G21 G90 G0 Z5\nG82 R2 Z-4 P500 F100\nX5\n")
    assert any(m.kind == "dwell" and m.detail["seconds"] == pytest.approx(0.5)
               for m in g82.motions)
    g83 = interpret_text(b"G21 G90 G0 Z5\nG83 R2 Z-6 Q2 F100\n")
    plunges = [m for m in g83.motions if m.kind == "plunge"]
    # R=2 -> 0 -> -2 -> -4 -> -6，共 4 次啄进，中间 3 次回退到 R，
    # 外加循环结束的 1 次最终回退
    assert len(plunges) == 4
    assert len([m for m in g83.motions
                if m.kind == "retract" and m.detail.get("cycle_phase") == "peck_retract"]) == 3


def test_unknown_mcode_recorded_but_no_fake_motion():
    r = interpret_text(b"G21 G90\nG187 P3\nG0 X1\n")
    codes = [u["code"] for u in r.unknowns]
    assert "G187" in codes
    # 未知行不应凭空产生运动
    assert all(m.line_index != 1 for m in r.motions)


def test_macro_fragment_unknown_blocks_safe():
    r = interpret_text(b"G1 X[#100+1] F100\n")
    assert r.unknowns and r.unknowns[0]["kind"] == "fragment"


def test_bad_arc_is_error_not_guessed_path():
    r = interpret_text(b"G17 G90 G0 X0 Y0\nG3 X10 Y10 R1\n")
    assert r.errors
    assert "R smaller than half chord" in r.errors[0]["error"]
