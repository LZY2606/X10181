from __future__ import annotations

from typing import Any

from .interpreter import SafetyConfig
from .models import Block, SafetyFinding


def _point_inside(point: dict[str, float], config: SafetyConfig) -> bool:
    return all(
        minimum - 1e-9 <= point[axis] <= maximum + 1e-9
        for axis, minimum, maximum in zip("XYZ", config.stock_min, config.stock_max)
    )


def _segment_crosses_box(start: dict[str, float], end: dict[str, float], config: SafetyConfig) -> bool:
    if _point_inside(start, config) or _point_inside(end, config):
        return True
    samples = 200
    for index in range(1, samples):
        fraction = index / samples
        point = {axis: start[axis] + (end[axis] - start[axis]) * fraction for axis in "XYZ"}
        if _point_inside(point, config):
            return True
    return False


def analyze_side(name: str, blocks: list[Block], config: SafetyConfig) -> list[SafetyFinding]:
    findings: list[SafetyFinding] = []
    for block in blocks:
        if block.unknown or block.parse_issues:
            findings.append(SafetyFinding(
                "unknown_instruction", "danger",
                f"第 {block.line_number} 行存在未确认或无法解析的厂商指令，禁止判定安全无变化",
                name, block.line_number,
            ))
        for motion in block.motions:
            if motion["kind"] != "rapid":
                continue
            if motion.get("cycle_phase") and motion["cycle_phase"] != "cycle_positioning":
                continue
            points = motion.get("points", [])
            for previous, current in zip(points, points[1:]):
                if _segment_crosses_box(previous, current, config):
                    findings.append(SafetyFinding(
                        "rapid_through_stock", "danger",
                        f"第 {block.line_number} 行快速移动穿过或进入毛坯安全区",
                        name, block.line_number, motion["id"],
                    ))
                    break
    final = blocks[-1].state if blocks and blocks[-1].state else None
    if final:
        if final.get("cutter_comp") != "G40":
            findings.append(SafetyFinding("cutter_comp_not_restored", "danger",
                                          "程序结束时刀具半径补偿未恢复为 G40", name))
        if final.get("tool_length") != "G49":
            findings.append(SafetyFinding("tool_length_not_restored", "danger",
                                          "程序结束时刀具长度补偿未恢复为 G49", name))
        if final.get("cycle"):
            findings.append(SafetyFinding("fixed_cycle_not_cancelled", "danger",
                                          f"程序结束时固定循环 {final['cycle']} 未取消", name))
    return findings


def compare_units(left: list[Block], right: list[Block]) -> SafetyFinding | None:
    def first_unit(blocks: list[Block]) -> str | None:
        for block in blocks:
            if block.state and any(event[:1] == ("unit",) for event in block.semantic_events):
                return block.state["unit"]
            if block.motions:
                return block.state["unit"] if block.state else None
        return blocks[0].state["unit"] if blocks and blocks[0].state else None
    if first_unit(left) != first_unit(right):
        return SafetyFinding("unit_switch_missing", "warning",
                             "两个程序使用的首个有效单位不同，可能遗漏 G20/G21 单位切换", "comparison")
    return None


def analyze_comparison(left: list[Block], right: list[Block], config: SafetyConfig | None = None) -> list[SafetyFinding]:
    config = config or SafetyConfig()
    findings = analyze_side("original", left, config) + analyze_side("modified", right, config)
    unit_finding = compare_units(left, right)
    if unit_finding:
        findings.append(unit_finding)
    return findings


def findings_to_dicts(findings: list[SafetyFinding]) -> list[dict[str, Any]]:
    return [finding.to_dict() for finding in findings]
