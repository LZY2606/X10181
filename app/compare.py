"""两个程序版本的机床执行语义比较。

对齐策略：先按规范化语义签名做最长公共子序列（LCS），再把未匹配的
连续“删除段/插入段”做贪心配对；随后逐对判定纯格式变化、等价模态重申
与真实运动变化，并为每个分叉找出最早分叉点与所有受影响运动。
"""
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .interpreter import ProgramModel, Segment

POS_EPS = 1e-6
FEED_EPS = 1e-6


@dataclass
class Fork:
    fork_id: int
    line_a: Optional[int]
    line_b: Optional[int]
    reason: str
    detail: str
    affected_a: List[int]
    affected_b: List[int]
    converged: bool
    converges_at: Optional[Tuple[Optional[int], Optional[int]]]

    def to_dict(self) -> dict:
        return {
            "fork_id": self.fork_id, "line_a": self.line_a,
            "line_b": self.line_b, "reason": self.reason,
            "detail": self.detail, "affected_a": self.affected_a,
            "affected_b": self.affected_b, "converged": self.converged,
            "converges_at": self.converges_at,
        }


def _lcs_pairs(a: List[str], b: List[str]) -> List[Tuple[int, int]]:
    n, m = len(a), len(b)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            dp[i][j] = (dp[i + 1][j + 1] + 1 if a[i] == b[j]
                        else max(dp[i + 1][j], dp[i][j + 1]))
    pairs, i, j = [], 0, 0
    while i < n and j < m:
        if a[i] == b[j]:
            pairs.append((i, j))
            i += 1
            j += 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            i += 1
        else:
            j += 1
    return pairs


def segments_equal(s1: Segment, s2: Segment) -> bool:
    if s1.kind != s2.kind or len(s1.points) != len(s2.points):
        return False
    for p1, p2 in zip(s1.points, s2.points):
        for c1, c2 in zip(p1, p2):
            if abs(c1 - c2) > POS_EPS:
                return False
    f1 = s1.feed if s1.feed is not None else -1.0
    f2 = s2.feed if s2.feed is not None else -1.0
    if abs(f1 - f2) > FEED_EPS:
        return False
    if bool(s1.axis_unknown) != bool(s2.axis_unknown):
        return False
    return True


def _pos_equal(sa: dict, sb: dict) -> bool:
    for axis in ("x", "y", "z"):
        if abs(sa["position"][axis] - sb["position"][axis]) > POS_EPS:
            return False
    return True


def _line_motion(model: ProgramModel, line_index: int) -> List[Segment]:
    return [s for s in model.segments if s.line_index == line_index]


def _motions_equal(a: ProgramModel, ia: Optional[int],
                   b: ProgramModel, ib: Optional[int]) -> bool:
    if ia is None or ib is None:
        return False
    sa, sb = _line_motion(a, ia), _line_motion(b, ib)
    if len(sa) != len(sb):
        return False
    return all(segments_equal(x, y) for x, y in zip(sa, sb))


def _classify_pair(a: ProgramModel, b: ProgramModel,
                   ia: Optional[int], ib: Optional[int]) -> Tuple[str, str]:
    """返回 (类别, 原因)。"""
    la = a.lines[ia] if ia is not None else None
    lb = b.lines[ib] if ib is not None else None
    if la is not None and lb is not None:
        if la.raw == lb.raw:
            return "unchanged", ""
        if la.signature == lb.signature:
            # 语义签名一致：仅空白/注释/大小写/数字写法不同
            return "format", "空白/注释/大小写/数字写法不同"
        if (la.after_fp == lb.after_fp
                and _pos_equal(la.after, lb.after)
                and _motions_equal(a, ia, b, ib)):
            return "restate", "等价模态重申（进给/主轴/冷却等）"
        return "changed", _motion_detail(a, ia, b, ib)
    if la is not None:
        return "delete", "修改版删除此行：" + (la.signature or la.raw.strip())
    return "insert", "修改版新增此行：" + (lb.signature or lb.raw.strip())


def _motion_detail(a: ProgramModel, ia: int, b: ProgramModel, ib: int) -> str:
    pa = a.lines[ia].after["position"]
    pb = b.lines[ib].after["position"]
    diffs = [ax for ax in ("x", "y", "z") if abs(pa[ax] - pb[ax]) > POS_EPS]
    sa, sb = _line_motion(a, ia), _line_motion(b, ib)
    if len(sa) != len(sb) or any(not segments_equal(x, y)
                                 for x, y in zip(sa, sb)):
        base = "本行运动语义不同"
    else:
        base = "本行模态分叉"
    if diffs:
        base += "（终点 %s 不同）" % "/".join(ax.upper() for ax in diffs)
    fp_diffs = [name for name, (va, vb) in
                _fp_fields(a.lines[ia].after, b.lines[ib].after).items()
                if va != vb and name not in ("position",)]
    if fp_diffs and not diffs:
        base += "（模态：%s）" % "、".join(fp_diffs[:4])
    return base


def _fp_fields(sa: dict, sb: dict) -> Dict[str, Tuple[object, object]]:
    keys = ("units", "positioning", "plane", "motion", "cutter_comp",
            "length_comp", "wcs", "retract_mode", "feed", "spindle",
            "spindle_speed", "coolant", "cycle", "position")
    return {k: (sa.get(k), sb.get(k)) for k in keys}


def _align(a: ProgramModel, b: ProgramModel) -> List[dict]:
    sig_a = [l.signature for l in a.lines]
    sig_b = [l.signature for l in b.lines]
    pairs = _lcs_pairs(sig_a, sig_b)
    n, m = len(a.lines), len(b.lines)

    raw: List[Tuple[Optional[int], Optional[int]]] = []
    prev_a, prev_b = -1, -1
    for pa, pb in pairs:
        deletes = list(range(prev_a + 1, pa))
        inserts = list(range(prev_b + 1, pb))
        k = min(len(deletes), len(inserts))
        for i in range(k):
            raw.append((deletes[i], inserts[i]))
        for i in range(k, len(deletes)):
            raw.append((deletes[i], None))
        for i in range(k, len(inserts)):
            raw.append((None, inserts[i]))
        raw.append((pa, pb))
        prev_a, prev_b = pa, pb
    deletes = list(range(prev_a + 1, n))
    inserts = list(range(prev_b + 1, m))
    k = min(len(deletes), len(inserts))
    for i in range(k):
        raw.append((deletes[i], inserts[i]))
    for i in range(k, len(deletes)):
        raw.append((deletes[i], None))
    for i in range(k, len(inserts)):
        raw.append((None, inserts[i]))

    out: List[dict] = []
    for ia, ib in raw:
        kind, detail = _classify_pair(a, b, ia, ib)
        out.append({"a": ia, "b": ib, "kind": kind, "detail": detail})
    return out


def _seg_line_ids(model: ProgramModel) -> Dict[int, List[int]]:
    ids: Dict[int, List[int]] = {}
    for idx, seg in enumerate(model.segments):
        ids.setdefault(seg.line_index, []).append(idx)
    return ids


def _line_segments_equal(a: ProgramModel, b: ProgramModel,
                         ia: int, ib: int) -> bool:
    sa = [s for s in a.segments if s.line_index == ia]
    sb = [s for s in b.segments if s.line_index == ib]
    if len(sa) != len(sb):
        return False
    return all(segments_equal(x, y) for x, y in zip(sa, sb))


def _build_forks(alignment: List[dict], a: ProgramModel, b: ProgramModel):
    forks: List[Fork] = []
    changed_a: set = set()
    changed_b: set = set()
    seg_ids_a = _seg_line_ids(a)
    seg_ids_b = _seg_line_ids(b)
    na, nb = len(a.lines), len(b.lines)

    fork_id = 0
    pos = 0
    while pos < len(alignment):
        item = alignment[pos]
        kind = item["kind"]
        ia, ib = item["a"], item["b"]

        if kind not in ("changed", "insert", "delete"):
            pos += 1
            continue

        if ia is not None:
            changed_a.add(ia)
        if ib is not None:
            changed_b.add(ib)
        fork_id += 1
        reason, detail = _fork_reason(a, ia, b, ib, item.get("detail", ""))
        aff_a = list(seg_ids_a.get(ia, [])) if ia is not None else []
        aff_b = list(seg_ids_b.get(ib, [])) if ib is not None else []
        fk = Fork(fork_id, ia, ib, reason, detail, aff_a, aff_b, False, None)

        j = pos + 1
        converged = False
        while j < len(alignment):
            cj = alignment[j]
            ca, cb = cj["a"], cj["b"]
            if ca is not None and cb is not None:
                lca, lcb = a.lines[ca], b.lines[cb]
                future_ok = _future_motions_equal(a, b, ca, cb,
                                                  alignment, j)
                if (lca.after_fp == lcb.after_fp
                        and _pos_equal(lca.after, lcb.after)
                        and future_ok):
                    fk.converged = True
                    fk.converges_at = (ca, cb)
                    converged = True
                    break
            if ca is not None:
                changed_a.add(ca)
                fk.affected_a.extend(seg_ids_a.get(ca, []))
            if cb is not None:
                changed_b.add(cb)
                fk.affected_b.extend(seg_ids_b.get(cb, []))
            j += 1

        if not converged:
            tail_a_start = (ia + 1) if ia is not None else na
            tail_b_start = (ib + 1) if ib is not None else nb
            for ln in range(tail_a_start, na):
                changed_a.add(ln)
                fk.affected_a.extend(seg_ids_a.get(ln, []))
            for ln in range(tail_b_start, nb):
                changed_b.add(ln)
                fk.affected_b.extend(seg_ids_b.get(ln, []))

        fk.affected_a = sorted(set(fk.affected_a))
        fk.affected_b = sorted(set(fk.affected_b))
        forks.append(fk)
        pos = j if converged else len(alignment)

    return forks, changed_a, changed_b


def _future_motions_equal(a: ProgramModel, b: ProgramModel,
                          ia: int, ib: int,
                          alignment: List[dict], start: int) -> bool:
    """收敛点之后所有对齐行均为配对行且每行运动逐段相同。"""
    for cj in alignment[start:]:
        ca, cb = cj["a"], cj["b"]
        if ca is None or cb is None:
            return False
        if not _line_segments_equal(a, b, ca, cb):
            return False
    # 当前行本身也要一致
    if not _line_segments_equal(a, b, ia, ib):
        return False
    return True


def _fork_reason(a, ia, b, ib, detail: str) -> Tuple[str, str]:
    la = a.lines[ia] if ia is not None else None
    lb = b.lines[ib] if ib is not None else None
    if la is not None and lb is not None:
        if not _line_segments_equal(a, b, ia, ib):
            return "motion", detail or "运动几何/类型不同"
        return "modal_only", detail or "模态状态分叉（位置相同）"
    return "motion", detail


def compare_models(a: ProgramModel, b: ProgramModel) -> dict:
    alignment = _align(a, b)
    forks, changed_a, changed_b = _build_forks(alignment, a, b)

    counts = {"unchanged": 0, "format": 0, "restate": 0,
              "changed": 0, "insert": 0, "delete": 0}
    for item in alignment:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1

    unknown_blocked = a.blocked or b.blocked
    has_real = (counts["changed"] > 0 or counts["insert"] > 0
                or counts["delete"] > 0)
    modal_only_only = (not has_real and all(
        f.reason == "modal_only" for f in forks))

    if unknown_blocked:
        verdict = "blocked"
    elif not has_real:
        verdict = "equivalent"
    else:
        verdict = "different"

    return {
        "verdict": verdict,
        "counts": counts,
        "real_change": has_real,
        "modal_only_only": modal_only_only,
        "unknown_blocked": unknown_blocked,
        "block_reasons": {"a": a.block_reasons, "b": b.block_reasons},
        "alignment": alignment,
        "forks": [f.to_dict() for f in forks],
        "changed_lines_a": sorted(changed_a),
        "changed_lines_b": sorted(changed_b),
    }


def compare_texts(a_text: bytes, b_text: bytes, resolver=None) -> dict:
    a = __import__("app.interpreter", fromlist=["interpret"]).interpret(
        a_text, resolver)
    b = __import__("app.interpreter", fromlist=["interpret"]).interpret(
        b_text, resolver)
    return compare_models(a, b), a, b
