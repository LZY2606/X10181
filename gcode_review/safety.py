"""安全分析：所有结论都来自解释后的数值状态，不做文本猜测。

检查项：
1. rapid_through_stock  快速移动（G0 或循环内快速段）穿过毛坯安全 AABB；
2. unit_not_restored    程序结束时单位与开头不一致（单位切换遗漏）；
3. cutter_comp_left_on  G41/G42 在程序结束仍未 G40；
4. tool_length_left_on  G43 在程序结束仍未 G49；
5. cycle_not_cancelled  G81.. 固定循环到程序结束未 G80；
6. unit_change_in_diff  两版本单位设置不同（可能漏改/漏切）。
未知厂商指令存在时，verdict 一律不能是 safe。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .geometry import segment_box_intersect
from .interpreter import Result
from .compare import Comparison

Point = Tuple[float, float, float]


@dataclass
class Box:
    min: Point
    max: Point

    @classmethod
    def from_dict(cls, d: Dict[str, float]) -> "Box":
        return cls(min=(d["xmin"], d["ymin"], d["zmin"]),
                   max=(d["xmax"], d["ymax"], d["zmax"]))


RAPID_KINDS = {"rapid", "retract"}
# 循环内快速回退也算快速移动
CYCLE_RAPID_PHASES = {"position_xy", "to_r", "peck_retract"}


@dataclass
class Finding:
    code: str
    severity: str          # danger / warning
    version: str
    line: Optional[int]
    motion_id: Optional[int]
    message: str
    values: Dict[str, object] = field(default_factory=dict)


@dataclass
class SafetyReport:
    findings: List[Finding]
    verdict: str           # safe / review / unsafe
    checked: Dict[str, object]

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "findings": [vars(f) for f in self.findings],
            "checked": self.checked,
        }


def _rapid_segments(result: Result):
    """产出 (line, motion_id, p_machine, q_machine, is_initial) 的快速段。

    is_initial: 起点是解释器默认初始点（未被程序定位）的第一条快速段；
    该点在盒内只是建模约定，不代表刀已在毛坯里，故不作为命中。
    """
    started = False
    for mot in result.motions:
        phase = mot.detail.get("cycle_phase")
        is_rapid = mot.kind in RAPID_KINDS or phase in CYCLE_RAPID_PHASES
        if not is_rapid:
            started = True  # 任何工作运动之后，初始豁免失效
            continue
        step = result.steps[mot.line_index]
        mp = mot.machine_points(step.after)
        for p, q in zip(mp, mp[1:]):
            is_initial = not started and _near_origin(p)
            yield mot.line_index, mot.id, p, q, is_initial
        started = True


def _near_origin(p: Point, tol: float = 1e-9) -> bool:
    return all(abs(v) <= tol for v in p)


def analyze_version(result: Result, box: Optional[Box]) -> List[Finding]:
    findings: List[Finding] = []
    version = "a" if getattr(result, "_side", "a") == "a" else "b"

    # 1. 快速穿过毛坯安全区（机床坐标 AABB，slab 数值判定）
    if box is not None:
        for line, mid, p, q, is_initial in _rapid_segments(result):
            if is_initial and not _inside(q, box):
                continue  # 从默认原点出发的首次抬刀/定位
            hit = segment_box_intersect(p, q, box.min, box.max)
            if hit is None:
                continue
            p_in = _inside(p, box)
            q_in = _inside(q, box)
            if p_in and q_in:
                continue  # 整段在毛坯盒内（如循环内部段已按类型过滤）
            findings.append(Finding(
                code="rapid_through_stock", severity="danger", version=version,
                line=line, motion_id=mid,
                message="快速移动穿过毛坯安全区",
                values={"entry": [round(v, 4) for v in hit],
                        "from": [round(v, 4) for v in p],
                        "to": [round(v, 4) for v in q],
                        "box": {"min": list(box.min), "max": list(box.max)}}))

    if not result.steps:
        return findings
    first = result.steps[0].before
    last = result.steps[-1].after

    # 2. 单位未恢复（默认以程序首行前状态 mm 为基准；G20/G21 切换后应切回）
    if last.units != first.units:
        change_line = None
        for st in result.steps:
            if st.after.units != st.before.units:
                change_line = st.line_index
        findings.append(Finding(
            code="unit_not_restored", severity="warning", version=version,
            line=change_line, motion_id=None,
            message=f"单位在 {first.units} 与 {last.units} 间切换后未恢复",
            values={"start": first.units, "end": last.units}))

    # 3/4. 补偿未恢复
    if last.cutter_comp != "G40":
        findings.append(Finding(
            code="cutter_comp_left_on", severity="warning", version=version,
            line=_last_line_with(result, "cutter_comp"), motion_id=None,
            message=f"刀具半径补偿 {last.cutter_comp} 程序结束未取消",
            values={"state": last.cutter_comp}))
    if last.tool_length != "G49":
        findings.append(Finding(
            code="tool_length_left_on", severity="warning", version=version,
            line=_last_line_with(result, "tool_length"), motion_id=None,
            message=f"刀具长度补偿 {last.tool_length} 程序结束未取消",
            values={"state": last.tool_length}))

    # 5. 固定循环未取消
    if last.cycle != 80.0:
        findings.append(Finding(
            code="cycle_not_cancelled", severity="warning", version=version,
            line=_last_cycle_line(result), motion_id=None,
            message=f"固定循环 G{int(last.cycle)} 未用 G80 取消",
            values={"cycle": last.cycle}))

    return findings


def _inside(p: Point, box: Box, tol: float = 1e-9) -> bool:
    return all(box.min[i] - tol <= p[i] <= box.max[i] + tol for i in range(3))


def _last_line_with(result: Result, field_name: str) -> Optional[int]:
    for st in reversed(result.steps):
        if getattr(st.after, field_name) != getattr(st.before, field_name):
            return st.line_index
    return None


def _last_cycle_line(result: Result) -> Optional[int]:
    for st in reversed(result.steps):
        if st.after.cycle != st.before.cycle:
            return st.line_index
    return None


def analyze_comparison(cmp: Comparison, box: Optional[Box]) -> SafetyReport:
    cmp.a._side = "a"
    cmp.b._side = "b"
    findings = analyze_version(cmp.a, box) + analyze_version(cmp.b, box)

    # 两版本单位模态在任一行后不一致：可能单位切换漏改
    for ea, eb in zip(cmp.a.steps, cmp.b.steps):
        if ea.after.units != eb.after.units:
            findings.append(Finding(
                code="unit_change_in_diff", severity="danger", version="both",
                line=eb.line_index, motion_id=None,
                message="两版本单位设置不一致（G20/G21 切换遗漏）",
                values={"a": ea.after.units, "b": eb.after.units}))
            break

    # 解释错误（如圆弧参数非法）阻止 safe
    err_lines = {e["line"] for e in cmp.a.errors} | {e["line"] for e in cmp.b.errors}
    for line in sorted(err_lines):
        findings.append(Finding(
            code="interp_error", severity="warning", version="both",
            line=line, motion_id=None,
            message="存在无法解释的运动行", values={}))

    # 无法识别的厂商指令：系统不知道它做了什么，必须显式阻断“安全无变化”。
    seen = set()
    for u in (cmp.a.unknowns + cmp.b.unknowns):
        key = (u["line"], u["kind"], u["code"], u["text"])
        if key in seen:
            continue
        seen.add(key)
        side = "a" if any(x["line"] == u["line"] and x["code"] == u["code"]
                          for x in cmp.a.unknowns) else "b"
        findings.append(Finding(
            code="unrecognized_vendor_instruction", severity="danger",
            version=side, line=u["line"], motion_id=None,
            message=f"未识别的厂商指令 {u['code']}（{u['kind']}），语义未知，"
                    f"在人工确认前不得判定为安全",
            values={"text": u["text"]}))

    danger = [f for f in findings if f.severity == "danger"]
    if danger:
        verdict = "unsafe"
    elif findings:
        verdict = "review"
    else:
        # 存在真实差异本身需要人工复核，只有“完全无差异”才给 safe
        real_changes = [e for e in cmp.entries
                        if e.kind in ("changed", "added", "removed")]
        verdict = "safe" if not real_changes else "review"

    return SafetyReport(
        findings=findings, verdict=verdict,
        checked={"rapid_through_stock": box is not None,
                 "stock_box": (None if box is None
                               else {"min": list(box.min), "max": list(box.max)}),
                 "unknown_instructions": len(cmp.a.unknowns) + len(cmp.b.unknowns),
                 "real_change_entries": sum(
                     1 for e in cmp.entries
                     if e.kind in ("changed", "added", "removed"))})
