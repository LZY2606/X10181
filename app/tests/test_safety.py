"""安全规则：快速穿毛坯、单位切换、补偿未恢复、循环未取消（数值断言）。"""
from app.interpreter import interpret
from app.safety import safety_report, Stock


def test_rapid_through_stock_detected():
    # 毛坯 X[0,10] Y[0,10] Z<=1：快移在 Z0 横穿毛坯中心
    m = interpret(b"G21 G90\nG0 X-5 Y5 Z0\nG0 X15 Y5\nM30\n")
    r = safety_report(m, Stock(xmin=0, xmax=10, ymin=0, ymax=10,
                               zmin=-5, z_clear=1))
    hits = [v for v in r["violations"] if v["rule"] == "rapid_through_stock"]
    # 第3行（索引2）G0 X15 Y5 在 Z0 横穿毛坯内部
    assert hits and hits[0]["line_index"] == 2
    assert r["safe"] is False


def test_rapid_above_clearance_ok():
    m = interpret(b"G21 G90\nG0 X-20 Y-20 Z50\nG0 X-5 Y5 Z5\nG0 X15 Y5\nM30\n")
    r = safety_report(m, Stock(xmin=0, xmax=10, ymin=0, ymax=10,
                               zmin=-5, z_clear=1))
    assert not [v for v in r["violations"]
                if v["rule"] == "rapid_through_stock"]


def test_cycle_internal_rapid_not_flagged():
    # 固定循环在孔轴线上的进退刀不算穿越毛坯
    m = interpret(b"G21 G90\nG0 X-20 Y-20 Z50\nG0 X5 Y5\n"
                  b"G81 G98 X5 Y5 R2 Z-3 F100\nG80\nM30\n")
    r = safety_report(m, Stock(xmin=0, xmax=10, ymin=0, ymax=10,
                               zmin=-5, z_clear=1))
    assert not [v for v in r["violations"]
                if v["rule"] == "rapid_through_stock"], r["violations"]


def test_unit_switch_flagged():
    m = interpret(b"G21 G90\nG1 X5\nG20\nG1 X1\nM30\n")
    r = safety_report(m)
    us = [v for v in r["violations"] if v["rule"] == "unit_switch"]
    assert us and us[0]["line_index"] == 2


def test_comp_not_restored():
    m = interpret(b"G21 G90\nG41 D1\nG1 X5 F100\nM30\n")
    r = safety_report(m)
    comp = [v for v in r["violations"] if v["rule"] == "comp_not_restored"]
    assert comp and comp[0]["severity"] == "error"
    assert r["safe"] is False


def test_comp_restored_ok():
    m = interpret(b"G21 G90\nG41 D1\nG1 X5 F100\nG40\nM30\n")
    r = safety_report(m)
    assert not [v for v in r["violations"]
                if v["rule"] == "comp_not_restored"]


def test_cycle_not_cancelled():
    m = interpret(b"G21 G90\nG0 Z50\nG81 X1 R2 Z-1 F100\nM30\n")
    r = safety_report(m)
    cyc = [v for v in r["violations"] if v["rule"] == "cycle_not_cancelled"]
    assert cyc and cyc[0]["severity"] == "error"


def test_cycle_cancelled_ok():
    m = interpret(b"G21 G90\nG0 Z50\nG81 X1 R2 Z-1 F100\nG80\nM30\n")
    r = safety_report(m)
    assert not [v for v in r["violations"]
                if v["rule"] == "cycle_not_cancelled"]
