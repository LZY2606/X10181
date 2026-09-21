"""原版 vs 修改版的语义比较。

对齐在原文行上进行（difflib），但差异判定走解释器状态：
- format        原文不同、词元规范化后相同（空格/注释/前导零）
- equivalent    等价模态重申/同词换序，前后状态与运动均一致
- changed       真实运动/模态变化
- unknown       含无法识别的厂商指令（阻断安全结论）
- added/removed 单边行
分叉段：从首个状态/运动不一致的对齐行开始，直到两侧状态重新汇合；
段内后续原文相同但语义不同的行（如 G91 传播）也标为受影响。
"""
from __future__ import annotations

import difflib
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .geometry import paths_close
from .interpreter import Motion, Result, Step, interpret
from .lexer import Program

POS_TOL = 1e-5


@dataclass
class Divergence:
    id: int
    a_line: Optional[int]
    b_line: Optional[int]
    reason: List[str]
    a_motion_ids: List[int] = field(default_factory=list)
    b_motion_ids: List[int] = field(default_factory=list)
    start_a: Optional[int] = None
    start_b: Optional[int] = None
    end_a: Optional[int] = None
    end_b: Optional[int] = None


@dataclass
class Entry:
    op: str                 # equal / replace / insert / delete
    a_line: Optional[int]
    b_line: Optional[int]
    kind: str               # equal/format/equivalent/changed/unknown/added/removed
    changed_fields: List[str] = field(default_factory=list)
    divergence_id: Optional[int] = None
    propagated: bool = False  # 原文相同但受上游分叉影响
    text_a: str = ""
    text_b: str = ""


@dataclass
class Comparison:
    a: Result
    b: Result
    entries: List[Entry]
    divergences: List[Divergence]

    def has_unknown(self) -> bool:
        return any(e.kind == "unknown" for e in self.entries)

# 参与“状态是否汇合”判断的模态字段（位置单独比较）
_STATE_FIELDS = (
    "units", "distance", "plane", "wcs", "cutter_comp", "tool_length",
    "cycle", "cycle_retract", "feed_mode", "motion", "motion_code",
    "feed", "spindle_speed", "spindle_on", "spindle_dir", "coolant", "tool",
)


def _pos_close(p, q, tol: float = POS_TOL) -> bool:
    return all(abs(p[i] - q[i]) <= tol * max(1.0, abs(p[i]), abs(q[i]))
               for i in range(3))


def modal_diff(sa, sb) -> List[str]:
    out: List[str] = []
    for name in _STATE_FIELDS:
        va, vb = getattr(sa, name), getattr(sb, name)
        if isinstance(va, float) or isinstance(vb, float):
            if not math.isclose(float(va), float(vb), abs_tol=1e-9, rel_tol=1e-7):
                out.append(name)
        elif va != vb:
            out.append(name)
    if not _pos_close(sa.pos, sb.pos):
        out.append("position")
    if sa.g92_shift != sb.g92_shift and not _pos_close(sa.g92_shift, sb.g92_shift):
        out.append("g92_shift")
    if sa.wcs_offsets != sb.wcs_offsets:
        offs_equal = all(
            _pos_close(sa.wcs_offsets.get(k, (0, 0, 0)),
                       sb.wcs_offsets.get(k, (0, 0, 0)))
            for k in set(sa.wcs_offsets) | set(sb.wcs_offsets))
        if not offs_equal:
            out.append("wcs_offsets")
    if sa.tool_offsets != sb.tool_offsets:
        out.append("tool_offsets")
    return out


def _motion_equiv(ma: Optional[Motion], mb: Optional[Motion]) -> bool:
    # 几何等价只看运动类型与刀路折线；进给率差异属于工艺量，
    # 由状态字段 feed 标记，不应让 IJK/R 等价圆弧被误判为不同运动。
    if ma is None and mb is None:
        return True
    if ma is None or mb is None:
        return False
    if ma.kind != mb.kind:
        return False
    return paths_close(ma.points, mb.points, tol=POS_TOL)


NON_GEOM_CONST = {"feed", "spindle_speed", "spindle_on", "spindle_dir",
                  "coolant", "tool"}


def _classify_pair(la_line: str, lb_line: str, sa: Step, sb: Step,
                   allow_non_geom: bool = False) -> Tuple[str, List[str]]:
    """前置几何状态相等时，对一对原文不同的行做行级语义分类。

    allow_non_geom=True：若仅进给/主轴等工艺量不同，返回 changed + 工艺标签；
    默认工艺量差异不影响“几何等价”，但行仍由调用方标 changed 展示。
    """
    if sa.unknown_g or sa.unknown_m or sa.unknown_fragments or \
            sb.unknown_g or sb.unknown_m or sb.unknown_fragments:
        return "unknown", ["unknown_instruction"]
    fields = modal_diff(sa.after, sb.after)
    geom = [f for f in fields if f not in NON_GEOM_CONST]
    motion_diff = not (
        len(sa.motions) == len(sb.motions) and
        all(_motion_equiv(x, y) for x, y in zip(sa.motions, sb.motions)))
    tags = list(geom)
    if motion_diff:
        tags.append("motion")
    if sa.errors or sb.errors:
        tags.append("interp_error")
    if not tags and not (fields and not allow_non_geom):
        return "equivalent", fields
    if not tags and fields:
        return ("changed", fields) if allow_non_geom else ("equivalent", [])
    return "changed", tags


def compare_programs(pa: Program, pb: Program,
                     offsets: Optional[Dict[str, Any]] = None,
                     tool_offsets: Optional[Dict[int, float]] = None) -> Comparison:
    ra = interpret(pa, offsets=offsets, tool_offsets=tool_offsets)
    rb = interpret(pb, offsets=offsets, tool_offsets=tool_offsets)

    a_texts = [ln.raw for ln in pa.lines]
    b_texts = [ln.raw for ln in pb.lines]
    matcher = difflib.SequenceMatcher(a=a_texts, b=b_texts, autojunk=False)

    pairs: List[Tuple[str, Optional[int], Optional[int]]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                pairs.append(("equal", i1 + k, j1 + k))
        elif tag == "replace":
            la, lb = i2 - i1, j2 - j1
            n = max(la, lb)
            for k in range(n):
                ai = i1 + k if k < la else None
                bj = j1 + k if k < lb else None
                pairs.append(("replace", ai, bj))
        elif tag == "delete":
            for k in range(i1, i2):
                pairs.append(("delete", k, None))
        else:
            for k in range(j1, j2):
                pairs.append(("insert", None, k))

    step_a = {st.line_index: st for st in ra.steps}
    step_b = {st.line_index: st for st in rb.steps}
    entries: List[Entry] = []
    divs: List[Divergence] = []
    div_seq = 0
    current: Optional[Divergence] = None
    # 两侧解释器游标：最近处理的行号
    cur_a = -1
    cur_b = -1
    # 分叉段内最后一条实际受影响的行
    last_aff_a: Optional[int] = None
    last_aff_b: Optional[int] = None

    def latest_states():
        sa = ra.steps[cur_a] if cur_a >= 0 else None
        sb = rb.steps[cur_b] if cur_b >= 0 else None
        return sa, sb

    def motions_equal(sa, sb) -> bool:
        return (len(sa.motions) == len(sb.motions) and
                all(_motion_equiv(x, y)
                    for x, y in zip(sa.motions, sb.motions)))

    # 决定“刀路是否已回到同一条轨迹”的几何核心状态；
    # 进给率/主轴转速/冷却/刀具号等工艺量差异不阻断刀轨汇合（行上仍标记）。
    NON_GEOM = {"feed", "spindle_speed", "spindle_on", "spindle_dir",
                "coolant", "tool"}
    GEOM_FIELDS = (
        "units", "distance", "plane", "wcs", "cutter_comp", "tool_length",
        "cycle", "cycle_retract", "feed_mode", "motion", "motion_code",
        "position", "g92_shift", "wcs_offsets", "tool_offsets",
    )

    def geom_diff(sa, sb) -> List[str]:
        return [f for f in modal_diff(sa.after, sb.after) if f in GEOM_FIELDS]

    def converged(sa, sb) -> bool:
        # 汇合看“执行后几何状态”是否一致；本行运动即使刀点相同，
        # 只要它落在共同位置上，后续即不再受分叉影响。
        if sa is None or sb is None:
            return False
        return not geom_diff(sa, sb)

    def open_div(ai, bj, reasons):
        nonlocal div_seq, current, last_aff_a, last_aff_b
        div_seq += 1
        current = Divergence(id=div_seq, a_line=ai, b_line=bj,
                             reason=list(reasons), start_a=ai, start_b=bj)
        divs.append(current)
        last_aff_a, last_aff_b = ai, bj

    def close_if(sa, sb):
        nonlocal current, last_aff_a, last_aff_b
        if current is not None and converged(sa, sb):
            current.end_a = last_aff_a
            current.end_b = last_aff_b
            current = None
            last_aff_a = last_aff_b = None

    def unknown_of(st):
        return bool(st.unknown_g or st.unknown_m or st.unknown_fragments)

    for op, ai, bj in pairs:
        ta = a_texts[ai] if ai is not None else ""
        tb = b_texts[bj] if bj is not None else ""
        entry = Entry(op=op, a_line=ai, b_line=bj, kind="equal",
                      text_a=ta, text_b=tb)

        if op == "equal":
            cur_a, cur_b = ai, bj
            sa, sb = step_a[ai], step_b[bj]
            if current is None:
                fields = modal_diff(sa.after, sb.after)
                mdiff = not (len(sa.motions) == len(sb.motions) and
                             all(_motion_equiv(x, y)
                                 for x, y in zip(sa.motions, sb.motions)))
                unk = unknown_of(sa) or unknown_of(sb)
                geom = [f for f in fields if f not in NON_GEOM]
                if unk:
                    open_div(ai, bj, ["unknown_instruction"])
                    entry.kind, entry.changed_fields = "unknown", ["unknown_instruction"]
                    entry.divergence_id = current.id
                    _attach_motions(current, sa, sb)
                    last_aff_a, last_aff_b = ai, bj
                elif geom or mdiff:
                    open_div(ai, bj, geom or ["motion"])
                    entry.kind, entry.changed_fields = "changed", fields or ["motion"]
                    entry.divergence_id = current.id
                    _attach_motions(current, sa, sb)
                    last_aff_a, last_aff_b = ai, bj
                elif fields:
                    # 仅工艺量差异：标记变化但不产生几何分叉
                    entry.kind, entry.changed_fields = "changed", fields
            else:
                if converged(sa, sb):
                    close_if(sa, sb)
                else:
                    fields = modal_diff(sa.after, sb.after)
                    unk = unknown_of(sa) or unknown_of(sb)
                    entry.kind = "unknown" if unk else "changed"
                    entry.propagated = True
                    entry.changed_fields = ["unknown_instruction"] if unk else fields
                    entry.divergence_id = current.id
                    # 只附加“与对侧不同”的运动，汇合行（仅残留位置差但刀路已一致）
                    # 已在 converged() 分支关闭分叉
                    if not motions_equal(sa, sb):
                        _attach_motions(current, sa, sb)
                    last_aff_a, last_aff_b = ai, bj
            entries.append(entry)
            continue

        if op in ("insert", "delete"):
            if op == "insert":
                cur_b = bj
                st = step_b[bj]
            else:
                cur_a = ai
                st = step_a[ai]
            sa_l, sb_l = latest_states()
            unk = unknown_of(st)
            if current is None:
                open_div(ai, bj, ["unknown_instruction"] if unk
                         else (["removed_line"] if op == "delete" else ["added_line"]))
            entry.kind = "unknown" if unk else ("removed" if op == "delete" else "added")
            entry.changed_fields = ["unknown_instruction"] if unk else []
            entry.divergence_id = current.id
            sa_now, sb_now = latest_states()
            if not converged(sa_now, sb_now):
                _attach_motions(current,
                                st if op == "delete" else None,
                                st if op == "insert" else None)
                last_aff_a, last_aff_b = cur_a, cur_b
            close_if(sa_now, sb_now)
            entries.append(entry)
            continue

        # replace 配对中一侧可能为 None（删除行数 != 新增行数的 hunk）：
        # 退化为 insert/delete 处理
        if ai is None or bj is None:
            side = "insert" if ai is None else "delete"
            if side == "insert":
                cur_b = bj
                st = step_b[bj]
            else:
                cur_a = ai
                st = step_a[ai]
            sa_l, sb_l = latest_states()
            unk = unknown_of(st)
            if current is None:
                open_div(ai, bj, ["unknown_instruction"] if unk
                         else (["removed_line"] if side == "delete" else ["added_line"]))
            entry.op = side
            entry.kind = "unknown" if unk else ("removed" if side == "delete" else "added")
            entry.changed_fields = ["unknown_instruction"] if unk else []
            entry.divergence_id = current.id
            sa_now, sb_now = latest_states()
            if not converged(sa_now, sb_now):
                _attach_motions(current,
                                st if side == "delete" else None,
                                st if side == "insert" else None)
                last_aff_a, last_aff_b = cur_a, cur_b
            close_if(sa_now, sb_now)
            entries.append(entry)
            continue

        # replace
        cur_a, cur_b = ai, bj
        sa, sb = step_a[ai], step_b[bj]
        unk = unknown_of(sa) or unknown_of(sb)
        if current is None:
            # 纯格式：词元规范化完全一致（状态必然一致）
            if not unk and pa.lines[ai].canonical_text() == pb.lines[bj].canonical_text():
                entry.kind = "format"
                entries.append(entry)
                continue
            # 即使前置状态在工艺量（如进给率）上有差异，只要几何核心一致，
            # 且本行两侧运动几何等价，就仍按“仅工艺量变化”处理。
            pre_fields = modal_diff(sa.before, sb.before)
            pre_geom_differ = any(f not in NON_GEOM for f in pre_fields)
            after_fields = modal_diff(sa.after, sb.after)
            geom = [f for f in after_fields if f not in NON_GEOM]
            motions_same = (len(sa.motions) == len(sb.motions) and
                            all(_motion_equiv(x, y)
                                for x, y in zip(sa.motions, sb.motions)))
            if not pre_geom_differ and not unk and not geom and not (
                    sa.unknown_g or sa.unknown_m or sb.unknown_g or sb.unknown_m):
                # 运动几何一致、仅工艺量差异（如转速）：变化但无几何分叉
                kind, tags = _classify_pair(ta, tb, sa, sb)
                if kind == "equivalent" and not after_fields:
                    entry.kind = "equivalent"
                    entries.append(entry)
                    continue
                entry.kind = "changed"
                entry.changed_fields = after_fields
                entries.append(entry)
                continue
            if not pre_geom_differ and not unk:
                kind, tags = _classify_pair(ta, tb, sa, sb,
                                            allow_non_geom=True)
                # 仅工艺量（进给/主轴）差异：行标变化但不产生几何分叉
                if kind == "changed" and all(t in NON_GEOM for t in tags):
                    entry.kind = "changed"
                    entry.changed_fields = tags
                    entries.append(entry)
                    continue
                # 运动几何实际等价（IJK vs R 等）且无几何状态差异：等价
                if kind == "equivalent" and not [
                        t for t in tags if t not in NON_GEOM]:
                    entry.kind = "equivalent"
                    entry.changed_fields = [t for t in tags if t in NON_GEOM]
                    if not entry.changed_fields:
                        entries.append(entry)
                        continue
                if kind == "equivalent" and not tags:
                    entry.kind = "equivalent"
                    entries.append(entry)
                    continue
                open_div(ai, bj, [t for t in tags if t not in NON_GEOM] or ["changed"])
                entry.kind, entry.changed_fields = kind, tags
            else:
                reasons = (["unknown_instruction"] if unk
                           else geom or ["changed"])
                open_div(ai, bj, reasons)
                entry.kind = "unknown" if unk else "changed"
                entry.changed_fields = [] if unk else after_fields
            entry.divergence_id = current.id
            _attach_motions(current, sa, sb)
            last_aff_a, last_aff_b = ai, bj
            close_if(sa, sb)
        else:
            if not unk and converged(sa, sb):
                close_if(sa, sb)
                entry.kind = "equivalent"
            else:
                entry.kind = "unknown" if unk else "changed"
                entry.changed_fields = [] if unk else modal_diff(sa.after, sb.after)
                entry.divergence_id = current.id
                _attach_motions(current, sa, sb)
                last_aff_a, last_aff_b = ai, bj
        entries.append(entry)

    # 未汇合到文件尾的分叉 end_* 保持 None，表示影响范围延续到程序结束
    return Comparison(ra, rb, entries, divs)


def _attach_motions(div: Divergence, sa: Optional[Step], sb: Optional[Step]) -> None:
    if sa is not None:
        for m in sa.motions:
            if m.id not in div.a_motion_ids:
                div.a_motion_ids.append(m.id)
    if sb is not None:
        for m in sb.motions:
            if m.id not in div.b_motion_ids:
                div.b_motion_ids.append(m.id)
