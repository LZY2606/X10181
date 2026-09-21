"""安全规则检测（数值断言）。

- 快速移动穿过毛坯安全区（含 XY 投影与 Z 高度盒）
- 单位中途切换（可能遗漏）
- 刀具半径补偿 G41/G42 未恢复 G40
- 固定循环结束未 G80 取消
"""
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .geometry import segment_intersects_box
from .interpreter import ProgramModel


@dataclass
class Stock:
    xmin: float = -50.0
    xmax: float = 50.0
    ymin: float = -50.0
    ymax: float = 50.0
    zmin: float = -100.0
    z_clear: float = 1.0   # 毛坯顶面上方安全高度（毛坯顶面=0）

    def box(self) -> Tuple[Tuple[float, float, float],
                           Tuple[float, float, float]]:
        return ((self.xmin, self.ymin, self.zmin),
                (self.xmax, self.ymax, self.z_clear))


@dataclass
class Violation:
    rule: str
    severity: str
    line_index: Optional[int]
    segment_index: Optional[int]
    message: str

    def to_dict(self) -> dict:
        return vars(self)


def check_rapid_through_stock(model: ProgramModel,
                              stock: Optional[Stock] = None) -> List[Violation]:
    stock = stock or Stock()
    lo, hi = stock.box()
    out: List[Violation] = []
    for idx, seg in enumerate(model.segments):
        if seg.kind != "rapid":
            continue
        # 固定循环内部孔位附近的进退刀在孔轴线上，属正常工艺动作；
        # 只检查真正跨孔位/跨区域的外部快移
        if seg.generated and seg.points[0][:2] == seg.points[-1][:2]:
            continue
        if len(seg.points) == 2:
            pts = seg.points
        else:
            pts = seg.points
        hit = False
        for p, q in zip(pts[:-1], pts[1:]):
            if segment_intersects_box(p, q, lo, hi):
                hit = True
                break
        if hit:
            out.append(Violation(
                "rapid_through_stock", "error", seg.line_index, idx,
                "第%d行快速移动进入毛坯安全盒 "
                "X[%.2f,%.2f] Y[%.2f,%.2f] Z<=%.2f"
                % (seg.line_index + 1, stock.xmin, stock.xmax,
                   stock.ymin, stock.ymax, stock.z_clear)))
    return out


def check_unit_switch(model: ProgramModel) -> List[Violation]:
    out: List[Violation] = []
    for ln in model.unit_switches:
        out.append(Violation(
            "unit_switch", "warning", ln, None,
            "第%d行发生 G20/G21 单位切换，需确认后续坐标与进给已换算"
            % (ln + 1)))
    return out


def check_comp_left_on(model: ProgramModel) -> List[Violation]:
    fs = model.final_state
    out: List[Violation] = []
    if fs.get("cutter_comp") != "G40":
        # 找最后一次开启所在行
        line_idx = None
        for line in model.lines:
            if line.after.get("cutter_comp") in ("G41", "G42"):
                line_idx = line.index
        out.append(Violation(
            "comp_not_restored", "error", line_idx, None,
            "程序结束时刀具半径补偿仍为 %s，未用 G40 恢复"
            % fs.get("cutter_comp")))
    return out


def check_cycle_left_on(model: ProgramModel) -> List[Violation]:
    fs = model.final_state
    out: List[Violation] = []
    if fs.get("cycle"):
        line_idx = None
        for line in model.lines:
            if line.after.get("cycle"):
                line_idx = line.index
        out.append(Violation(
            "cycle_not_cancelled", "error", line_idx, None,
            "程序结束时固定循环 %s 仍模态有效，未用 G80 取消"
            % fs.get("cycle")))
    return out


def safety_report(model: ProgramModel, stock: Optional[Stock] = None) -> dict:
    violations = (check_rapid_through_stock(model, stock)
                  + check_unit_switch(model)
                  + check_comp_left_on(model)
                  + check_cycle_left_on(model))
    errors = [v for v in violations if v.severity == "error"]
    return {
        "safe": len(errors) == 0,
        "violations": [v.to_dict() for v in violations],
        "error_count": len(errors),
    }
