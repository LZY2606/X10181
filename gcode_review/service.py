"""装配层：持久化字节 -> 比较 -> 安全分析 -> 前端 JSON。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .compare import Comparison, compare_programs
from .exporter import apply_patches, build_patches
from .interpreter import Motion, Result, Step
from .lexer import Program, parse_program
from .safety import Box, SafetyReport, analyze_comparison

DEFAULT_BOX = {"xmin": -50.0, "ymin": -50.0, "zmin": 0.0,
               "xmax": 50.0, "ymax": 50.0, "zmax": 30.0}


def _round_pt(p, n: int = 4):
    return [round(float(v), n) for v in p]


def _motion_json(mot: Motion, result: Result) -> dict:
    step = result.steps[mot.line_index]
    machine = mot.machine_points(step.after)
    return {
        "id": mot.id,
        "kind": mot.kind,
        "line": mot.line_index,
        "feed": round(mot.feed, 4),
        "points": [_round_pt(p) for p in mot.points],
        "machine_points": [_round_pt(p) for p in machine],
        "detail": mot.detail,
    }


def _line_json(prog: Program, index: int) -> dict:
    ln = prog.lines[index]
    return {
        "index": index,
        "text": ln.raw,
        "start": ln.start,
        "end": ln.end,
        "words": [{"letter": w.letter, "raw": w.raw, "value": w.value,
                   "span": list(w.span)} for w in ln.words],
        "comments": ln.comments,
        "vendor": ln.vendor_fragments,
    }


def _step_json(st: Step) -> dict:
    return {
        "line": st.line_index,
        "before": st.before.snapshot(),
        "after": st.after.snapshot(),
        "motion_ids": [m.id for m in st.motions],
        "declared": st.declared,
        "errors": st.errors,
        "unknown": {
            "g": [str(x) for x in st.unknown_g],
            "m": [str(x) for x in st.unknown_m],
            "fragments": st.unknown_fragments,
        },
        "dwell": st.dwell_seconds if st.is_dwell else None,
    }


def _divergence_json(cmp: Comparison) -> List[dict]:
    out = []
    for d in cmp.divergences:
        out.append({
            "id": d.id,
            "a_line": d.a_line,
            "b_line": d.b_line,
            "start_a": d.start_a,
            "start_b": d.start_b,
            "end_a": d.end_a,
            "end_b": d.end_b,
            "reasons": d.reason,
            "a_motion_ids": d.a_motion_ids,
            "b_motion_ids": d.b_motion_ids,
        })
    return out


def build_review(content_a: bytes, content_b: bytes,
                 settings: Optional[dict] = None) -> Dict[str, Any]:
    settings = dict(settings or {})
    box_dict = settings.get("stock_box", DEFAULT_BOX)
    offsets = settings.get("wcs_offsets")
    tool_offsets = settings.get("tool_offsets")
    pa, pb = parse_program(content_a), parse_program(content_b)
    cmp = compare_programs(pa, pb, offsets=offsets, tool_offsets=tool_offsets)
    box = Box.from_dict(box_dict) if settings.get("check_stock", True) else None
    report: SafetyReport = analyze_comparison(cmp, box)

    def side_payload(result: Result, prog: Program, side: str) -> dict:
        return {
            "side": side,
            "lines": [_line_json(prog, i) for i in range(len(prog.lines))],
            "steps": [_step_json(s) for s in result.steps],
            "motions": [_motion_json(m, result) for m in result.motions],
            "unknowns": result.unknowns,
            "errors": result.errors,
        }

    entries = [{
        "op": e.op, "kind": e.kind, "a_line": e.a_line, "b_line": e.b_line,
        "changed_fields": e.changed_fields, "divergence_id": e.divergence_id,
        "propagated": e.propagated, "text_a": e.text_a, "text_b": e.text_b,
    } for e in cmp.entries]

    return {
        "a": side_payload(cmp.a, pa, "a"),
        "b": side_payload(cmp.b, pb, "b"),
        "entries": entries,
        "divergences": _divergence_json(cmp),
        "safety": report.to_dict(),
        "settings": {"stock_box": box_dict, "check_stock": settings.get("check_stock", True),
                     "wcs_offsets": offsets or {},
                     "tool_offsets": tool_offsets or {}},
    }


def export_modified(content_a: bytes, content_b: bytes,
                    settings: Optional[dict] = None) -> Dict[str, Any]:
    pa, pb = parse_program(content_a), parse_program(content_b)
    cmp = compare_programs(pa, pb,
                           offsets=(settings or {}).get("wcs_offsets"),
                           tool_offsets=(settings or {}).get("tool_offsets"))
    patches = build_patches(cmp)
    exported = apply_patches(content_a, patches)
    if exported != content_b:
        # 补丁产物必须与修改版一致，否则不允许声称导出成功
        raise RuntimeError("patch export does not reproduce modified program")
    return {"bytes": exported, "patches": [p.to_dict() for p in patches]}
