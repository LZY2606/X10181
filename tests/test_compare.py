from gcode_review.compare import compare_programs
from gcode_review.lexer import parse_program


def cmp(a, b, **kw):
    return compare_programs(parse_program(a), parse_program(b), **kw)


def test_format_change_separated():
    c = cmp(b"G21 G90 G54\n", b"g21 G90 G054 (mm)\n".replace(b"G054", b"G54"))
    assert c.entries[0].kind == "format"
    assert not c.divergences


def test_equivalent_modal_restatement():
    # 重新声明当前模态（G90 重申）+ IJK/R 等价圆弧
    c = cmp(
        b"G21 G90 G17\nG0 X0 Y0\nG3 X1 Y1 I0 J1 F100\n",
        b"G21 G90 G17 G90\nG0 X0 Y0\nG3 X1 Y1 R1 F100\n")
    kinds = [e.kind for e in c.entries]
    assert "equivalent" in kinds
    assert not c.divergences


def test_real_motion_change_opens_divergence():
    c = cmp(b"G21 G90\nG1 X10 F100\nY5\nY0\nX0\n",
            b"G21 G90\nG1 X12 F100\nY5\nY0\nX0\n")
    d = c.divergences[0]
    assert d.a_line == 1 and d.b_line == 1
    assert "motion" in d.reason
    assert 2 in d.a_motion_ids and 2 in d.b_motion_ids
    # Y5/Y0 原文相同但位置被传播；X0 后两侧都回到 (0,..)，刀路汇合
    entry_y5 = next(e for e in c.entries if e.a_line == 2 and e.op == "equal")
    assert entry_y5.propagated and entry_y5.divergence_id == d.id
    entry_x0 = next(e for e in c.entries if e.a_line == 4 and e.op == "equal")
    assert entry_x0.kind == "equal"
    assert d.end_a == 3  # 最后受影响行是 Y0，X0 为汇合行


def test_incremental_switch_propagates_long_tail():
    a = b"G21 G90\nG0 X0\nG1 X10\nX20\nX30\nX0\n"
    b = b"G21 G91\nG0 X0\nG1 X10\nX20\nX30\nX0\n"
    c = cmp(a, b)
    d = c.divergences[0]
    # 最后 X0：绝对模式回 0，增量模式 X0 停在 60，永不汇合
    assert d.end_a is None
    affected_equal = [e for e in c.entries if e.propagated]
    assert {e.a_line for e in affected_equal} >= {1, 2, 3, 4, 5}


def test_unknown_instruction_kind_and_flag():
    c = cmp(b"G21 G90\nG0 X1\nM30\n",
            b"G21 G90\nG0 X1\nM77\nM30\n")
    assert c.has_unknown()
    assert any(e.kind == "unknown" for e in c.entries)


def test_added_and_removed_lines():
    c = cmp(b"G21\nG0 X1\n", b"G21\n(comment)\nG0 X1\n")
    assert any(e.kind == "added" for e in c.entries)
    c2 = cmp(b"G21\nG0 X1\nG4 P100\n", b"G21\nG0 X1\n")
    assert any(e.kind in ("removed", "changed") for e in c2.entries)
