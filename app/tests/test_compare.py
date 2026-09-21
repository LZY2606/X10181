"""比较：格式变化、等价模态重申、真实运动、圆弧等价、增量影响范围。"""
from app.interpreter import interpret
from app.compare import compare_models


def test_format_only_is_equivalent():
    a = interpret(b"G21 G90\nG1 X10 F100 (cut)\nM30\n")
    b = interpret(b"g21 g90\nG01 X10.0 F100. ; cut\nM30\n")
    r = compare_models(a, b)
    assert r["verdict"] == "equivalent"
    assert r["counts"]["format"] >= 1
    assert r["forks"] == []


def test_equivalent_modal_restatement():
    # 同一行内重申 F（数值相同）：签名不同但状态与运动完全一致
    a = interpret(b"G21 G90\nG1 X10 F200\nG1 X20\nM30\n")
    b = interpret(b"G21 G90\nG1 X10 F200 F200\nG1 X20\nM30\n")
    r = compare_models(a, b)
    assert r["verdict"] == "equivalent"
    assert r["counts"]["restate"] == 1
    assert r["forks"] == []


def test_real_motion_change_detected():
    a = interpret(b"G21 G90\nG1 X10 F100\nG1 Y10\nM30\n")
    b = interpret(b"G21 G90\nG1 X12 F100\nG1 Y10\nM30\n")
    r = compare_models(a, b)
    assert r["verdict"] == "different"
    assert r["counts"]["changed"] == 1
    assert len(r["forks"]) == 1
    f = r["forks"][0]
    assert f["reason"] == "motion"
    assert f["line_a"] == 1
    # 位置偏移永不收敛：受影响运动覆盖到末尾
    assert f["converged"] is False
    assert set(f["affected_a"]) == {0, 1}


def test_arc_r_vs_ij_equivalent_across_plane():
    a = interpret(b"G21 G90 G17\nG1 X0 Y10 F100\n"
                  b"G2 X20 Y10 I10 J0\nG1 X30\nM30\n")
    bprog = interpret(b"G21 G90 G17\nG1 X0 Y10 F100\n"
                      b"G2 X20 Y10 R10\nG1 X30\nM30\n")
    r = compare_models(a, bprog)
    assert r["verdict"] == "equivalent", r["alignment"]


def test_absolute_incremental_switch_real_change():
    # 绝对/增量切换：第二句 X10 在 G90 下原地不动，在 G91 下走到 20
    a = interpret(b"G21 G90\nG1 X10 F100\nG1 X10\nM30\n")
    b = interpret(b"G21 G91\nG1 X10 F100\nG1 X10\nM30\n")
    r = compare_models(a, b)
    assert r["verdict"] == "different"
    pa = a.lines[2].after["position"]
    pb = b.lines[2].after["position"]
    assert abs(pa["x"] - 10) < 1e-9
    assert abs(pb["x"] - 20) < 1e-9
    # 模态分叉（G90/G91）不收敛，影响延续到程序末尾
    assert r["forks"][0]["converged"] is False


def test_fork_converges_when_paths_rejoin():
    # 改走一条不同的中间路径，但回到同一点与模态，之后完全一致
    a = interpret(b"G21 G90\nG1 X10 Y0 F100\nG1 X20 Y0\nG1 X20 Y10\nM30\n")
    b = interpret(b"G21 G90\nG1 X10 Y5 F100\nG1 X20 Y0\nG1 X20 Y10\nM30\n")
    r = compare_models(a, b)
    fork = r["forks"][0]
    assert fork["converged"] is True
    # X20 Y0 行（索引2）的运动本身起点不同，仍受影响；
    # 到 X20 Y10 行（索引3）运动完全一致，在此收敛
    assert fork["converges_at"][0] == 3
    assert 3 not in r["changed_lines_a"]
    assert set(r["changed_lines_a"]) == {1, 2}


def test_insert_line_affects_range():
    a = interpret(b"G21 G90\nG1 X10 F100\nG1 X20\nM30\n")
    b = interpret(b"G21 G90\nG1 X10 F100\nG0 Z5\nG1 X20\nM30\n")
    r = compare_models(a, b)
    assert r["counts"]["insert"] == 1
    fork = r["forks"][0]
    assert fork["line_b"] == 2
    # Z 抬刀后未落回：直到程序末都不收敛
    assert fork["converged"] is False


def test_unknown_code_forces_blocked_not_equivalent():
    a = interpret(b"G21 G90\nM200\nM30\n")
    b = interpret(b"G21 G90\nM200\nM30\n")
    r = compare_models(a, b)
    assert r["verdict"] == "blocked"
    assert r["unknown_blocked"] is True


def test_wcs_switch_is_modal_fork():
    a = interpret(b"G21 G90 G54\nG1 X10 F100\nG1 Y10\nM30\n")
    b = interpret(b"G21 G90 G55\nG1 X10 F100\nG1 Y10\nM30\n")
    r = compare_models(a, b)
    assert r["verdict"] == "different"
    assert r["forks"][0]["reason"] in ("motion", "modal_only")
