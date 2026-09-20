from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Any

from .models import Block

STATE_KEYS = (
    "unit", "distance", "motion", "plane", "wcs", "retract", "cutter_comp",
    "tool_length", "spindle", "coolant", "feed", "spindle_speed", "tool",
    "offset_h", "offset_d", "position", "wcs_offsets", "local_offset", "cycle",
)


def close(value_a: Any, value_b: Any, tolerance: float = 1e-7) -> bool:
    if isinstance(value_a, dict) or isinstance(value_b, dict):
        if not isinstance(value_a, dict) or not isinstance(value_b, dict) or set(value_a) != set(value_b):
            return False
        return all(close(value_a[key], value_b[key], tolerance) for key in value_a)
    if isinstance(value_a, list) or isinstance(value_b, list):
        if not isinstance(value_a, list) or not isinstance(value_b, list) or len(value_a) != len(value_b):
            return False
        return all(close(a, b, tolerance) for a, b in zip(value_a, value_b))
    if isinstance(value_a, (int, float)) and isinstance(value_b, (int, float)):
        return math_isclose(value_a, value_b, tolerance)
    return value_a == value_b


def math_isclose(a: float, b: float, tolerance: float) -> bool:
    import math
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=tolerance)


def state_delta(before: dict[str, Any] | None, after: dict[str, Any] | None) -> dict[str, Any]:
    if before is None or after is None:
        return {}
    return {key: {"before": before.get(key), "after": after.get(key)} for key in STATE_KEYS
            if not close(before.get(key), after.get(key))}


def semantic_key(block: Block) -> tuple[Any, ...]:
    if block.skipped:
        return ("skip",)
    if block.has_error:
        return ("error", block.unknown[:], tuple(issue.message for issue in block.parse_issues))
    words = tuple(sorted(word.canonical() for word in block.words if word.address != "N"))
    motions = tuple(motion_geometry(motion) for motion in block.motions)
    state_after = block.state or {}
    state_before = block.pre_state or {}
    changed_state = tuple(
        (key, state_before.get(key), state_after.get(key))
        for key in STATE_KEYS if not close(state_before.get(key), state_after.get(key))
    )
    return (words, changed_state, motions)


def motion_geometry(motion: dict[str, Any]) -> tuple[str, tuple[tuple[float, float, float], ...]]:
    points = tuple((point["X"], point["Y"], point["Z"]) for point in motion.get("points", []))
    return (motion["kind"], points)


def motions_equal(left: Block, right: Block) -> bool:
    return close([motion_geometry(motion) for motion in left.motions],
                 [motion_geometry(motion) for motion in right.motions])


@dataclass
class LineDifference:
    kind: str
    original_lines: list[int]
    modified_lines: list[int]
    first_original: int | None = None
    first_modified: int | None = None
    affected_motions: list[dict[str, int]] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "original_lines": self.original_lines,
            "modified_lines": self.modified_lines,
            "first_original": self.first_original,
            "first_modified": self.first_modified,
            "affected_motions": self.affected_motions,
            "reason": self.reason,
        }


def _matcher(original: list[Block], modified: list[Block]) -> list[tuple[str, int, int, int, int]]:
    left_keys = [line_identity(block) for block in original]
    right_keys = [line_identity(block) for block in modified]
    return list(difflib.SequenceMatcher(None, left_keys, right_keys, autojunk=False).get_opcodes())


def line_identity(block: Block) -> str:
    if block.skipped:
        return "skip"
    if block.has_error:
        return "error:" + block.raw
    motions = tuple(motion_geometry(motion) for motion in block.motions)
    state_after = block.state or {}
    return repr((
        tuple(sorted(word.canonical() for word in block.words if word.address != "N")),
        tuple((key, state_after.get(key)) for key in STATE_KEYS),
        motions,
    ))


def _motion_refs(blocks: list[Block], side: str) -> list[dict[str, int]]:
    refs = []
    for block in blocks:
        for motion in block.motions:
            refs.append({"side": side, "line": block.line_number, "motion_id": motion["id"]})
    return refs


def _classify_pair(left: Block, right: Block) -> str:
    if left.has_error or right.has_error:
        return "unknown"
    canonical_left = tuple(sorted(word.canonical() for word in left.words if word.address != "N"))
    canonical_right = tuple(sorted(word.canonical() for word in right.words if word.address != "N"))
    if not motions_equal(left, right):
        return "real_motion"
    if close(left.state, right.state):
        if canonical_left == canonical_right and not (
            any(motion.get("arc") for motion in left.motions)
            and any(motion.get("arc") for motion in right.motions)
        ):
            return "format" if left.raw != right.raw else "equal"
        if any(motion.get("arc") for motion in left.motions) and any(motion.get("arc") for motion in right.motions):
            return "equivalent_geometry"
        if _modal_restatement(left, right, canonical_left, canonical_right):
            return "equivalent_modal"
        return "format" if close(left.pre_state, right.pre_state) else "real_modal"
    if canonical_left != canonical_right:
        return "real_modal"
    if left.raw != right.raw:
        return "format"
    return "equal"


def _modal_restatement(left: Block, right: Block, canonical_left: tuple, canonical_right: tuple) -> bool:
    for block, canonical in ((left, canonical_left), (right, canonical_right)):
        for address, value in dict(canonical).items():
            if address in {"X", "Y", "Z", "I", "J", "K", "R", "F", "S", "P", "Q"}:
                return False
            pre = block.pre_state or {}
            if address == "G" and f"G{int(value)}" not in {
                pre.get("motion"), pre.get("plane"), pre.get("distance"), pre.get("unit"),
                pre.get("wcs"), pre.get("retract"), pre.get("cutter_comp"),
                pre.get("tool_length"), pre.get("cycle")
            }:
                return False
            if address == "M" and f"M{int(value)}" != pre.get("spindle"):
                return False
    return True


def compare_programs(original: list[Block], modified: list[Block]) -> dict[str, Any]:
    differences: list[LineDifference] = []
    aligned: list[dict[str, Any]] = []
    current_origin: dict[str, int | None] = {"original": None, "modified": None}

    for tag, i1, i2, j1, j2 in _matcher(original, modified):
        left_slice = original[i1:i2]
        right_slice = modified[j1:j2]
        if tag == "equal":
            for left, right in zip(left_slice, right_slice):
                aligned.append({"original": left.line_number, "modified": right.line_number})
                kind = _classify_pair(left, right)
                if kind in {"format", "equivalent_modal", "equivalent_geometry"}:
                    differences.append(LineDifference(
                        kind, [left.line_number], [right.line_number],
                        current_origin["original"], current_origin["modified"],
                        reason=_reason(kind)))
            continue

        kind = "unknown"
        if any(block.has_error for block in left_slice + right_slice):
            kind = "unknown"
        elif tag == "replace" and len(left_slice) == len(right_slice):
            kinds = [_classify_pair(left, right) for left, right in zip(left_slice, right_slice)]
            benign = {"format", "equal", "equivalent_modal", "equivalent_geometry"}
            if kinds and all(item in benign for item in kinds):
                kind = next((item for item in kinds if item.startswith("equivalent_")), "format")
            elif all(item in benign | {"real_modal"} for item in kinds):
                kind = "real_modal"
            else:
                kind = "real_motion"
        else:
            left_has_motion = any(block.motions for block in left_slice)
            right_has_motion = any(block.motions for block in right_slice)
            left_changes_state = any(not close(block.pre_state, block.state) for block in left_slice)
            right_changes_state = any(not close(block.pre_state, block.state) for block in right_slice)
            kind = "real_motion" if left_has_motion or right_has_motion else (
                "real_modal" if left_changes_state or right_changes_state else "format"
            )

        first_origin = dict(current_origin)
        affected = _motion_refs(left_slice, "original") + _motion_refs(right_slice, "modified")

        # 模态分叉后，直到状态重新汇合前，后续运动都属于受影响范围。
        if kind in {"real_modal", "real_motion", "unknown"}:
            affected.extend(_following_diverged_motions(
                original, modified, i2, j2, kind == "unknown"
            ))
            current_origin = {
                "original": left_slice[0].line_number if left_slice else first_origin["original"],
                "modified": right_slice[0].line_number if right_slice else first_origin["modified"],
            }

        differences.append(LineDifference(
            kind,
            [block.line_number for block in left_slice],
            [block.line_number for block in right_slice],
            first_origin["original"] or (left_slice[0].line_number if left_slice else None),
            first_origin["modified"] or (right_slice[0].line_number if right_slice else None),
            affected,
            _reason(kind),
        ))
        for left, right in zip(left_slice, right_slice):
            aligned.append({"original": left.line_number, "modified": right.line_number})

    safe = all(item.kind not in {"real_motion", "real_modal", "unknown"} for item in differences)
    return {
        "differences": [item.to_dict() for item in differences if item.kind != "equal"],
        "aligned": aligned,
        "safe_no_change": safe,
        "summary": {
            "format": sum(item.kind == "format" for item in differences),
            "real_modal": sum(item.kind == "real_modal" for item in differences),
            "real_motion": sum(item.kind == "real_motion" for item in differences),
            "unknown": sum(item.kind == "unknown" for item in differences),
            "equivalent_modal": sum(item.kind == "equivalent_modal" for item in differences),
            "equivalent_geometry": sum(item.kind == "equivalent_geometry" for item in differences),
        },
    }


def _following_diverged_motions(
    original: list[Block], modified: list[Block], left_start: int, right_start: int,
    always_include: bool,
) -> list[dict[str, int]]:
    refs: list[dict[str, int]] = []
    left_index, right_index = left_start, right_start
    while left_index < len(original) and right_index < len(modified):
        if not always_include and close(original[left_index].state, modified[right_index].state):
            return refs
        refs.extend(_motion_refs([original[left_index]], "original"))
        refs.extend(_motion_refs([modified[right_index]], "modified"))
        left_index += 1
        right_index += 1
    refs.extend(_motion_refs(original[left_index:], "original"))
    refs.extend(_motion_refs(modified[right_index:], "modified"))
    return refs


def _reason(kind: str) -> str:
    return {
        "format": "仅格式、空白、注释或可归一化写法变化",
        "real_modal": "模态状态改变；后续运动可能受影响",
        "real_motion": "解释后的真实运动发生变化",
        "unknown": "存在未确认厂商指令或解析错误，不能得出安全无变化",
        "equivalent_modal": "等价模态重申，不改变后续状态或运动",
        "equivalent_geometry": "R 与 IJK、平面参数等几何等价写法",
    }.get(kind, "")
