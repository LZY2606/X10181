"""几何：由解释后的模态与目标点产生运动折线；圆弧三平面 + IJK/R 写法。"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

Point = Tuple[float, float, float]

# 每个平面的两个圆弧轴与对应圆心地址
PLANE_AXES: Dict[int, Tuple[str, str, str, str]] = {
    17: ("x", "y", "i", "j"),  # XY 平面，圆心 I J
    18: ("x", "z", "i", "k"),  # XZ 平面，圆心 I K
    19: ("y", "z", "j", "k"),  # YZ 平面，圆心 J K
}
AXIS_INDEX = {"x": 0, "y": 1, "z": 2}


def arc_points(start: Point, end: Point, plane: int, clockwise: bool,
               center_addr: Optional[Dict[str, float]] = None,
               radius: Optional[float] = None,
               segments: int = 48) -> List[Point]:
    """把圆弧离散为折线（含起点）。

    - center_addr: {"i":.., "j"/"k":..} 增量圆心（相对起点，Fanuc 语义）；
    - radius: R 写法，正 R 取 <=180 度弧，负 R 取 >180 度；
    - 非圆弧面轴在起止间线性变化（支持螺旋）。
    几何非法时抛 ValueError，由解释器记入 errors，不静默产出伪刀轨。
    """
    u_axis, v_axis, cu, cv = PLANE_AXES[plane]
    iu, iv = AXIS_INDEX[u_axis], AXIS_INDEX[v_axis]
    su, sv = start[iu], start[iv]
    eu, ev = end[iu], end[iv]

    if center_addr is not None:
        du = center_addr.get(cu, 0.0)
        dv = center_addr.get(cv, 0.0)
        cu_pt, cv_pt = su + du, sv + dv
        r_start = math.hypot(su - cu_pt, sv - cv_pt)
        r_end = math.hypot(eu - cu_pt, ev - cv_pt)
        if r_start < 1e-9:
            raise ValueError("arc center equals start (IJK all zero)")
        if abs(r_start - r_end) > max(1e-4, 1e-4 * r_start):
            raise ValueError("arc start/end radii disagree")
        radius_value = r_start
    elif radius is not None:
        cu_pt, cv_pt, radius_value = _center_from_r(
            su, sv, eu, ev, radius, clockwise)
    else:
        raise ValueError("arc needs IJK or R")

    a0 = math.atan2(sv - cv_pt, su - cu_pt)
    a1 = math.atan2(ev - cv_pt, eu - cu_pt)
    full_circle = (abs(eu - su) < 1e-9 and abs(ev - sv) < 1e-9
                   and center_addr is not None
                   and math.hypot(center_addr.get(cu, 0.0),
                                  center_addr.get(cv, 0.0)) > 1e-9)
    if full_circle:
        sweep = -2 * math.pi if clockwise else 2 * math.pi
    else:
        # IJK 写法：取朝目标方向的、与 G2/G3 一致的 <=180 度有向扫角
        # （CCW 为正）；整圆只能由端点相同 + IJK 表达。
        # R 写法：圆心候选已按 R 符号选出大/小弧，这里同样取有向短角。
        d = a1 - a0
        d = math.atan2(math.sin(d), math.cos(d))  # 归一化到 (-pi, pi]
        if abs(d) < 1e-9:
            if radius is not None:
                raise ValueError("R arc with identical endpoints is ambiguous")
            sweep = -2 * math.pi if clockwise else 2 * math.pi
        else:
            sweep = -abs(d) if clockwise else abs(d)

    pts: List[Point] = []
    steps = max(2, segments)
    third = [AXIS_INDEX[a] for a in ("x", "y", "z")
             if AXIS_INDEX[a] not in (iu, iv)]
    for k in range(steps + 1):
        t = k / steps
        ang = a0 + sweep * t
        p = [0.0, 0.0, 0.0]
        p[iu] = cu_pt + radius_value * math.cos(ang)
        p[iv] = cv_pt + radius_value * math.sin(ang)
        for w in third:
            p[w] = start[w] + (end[w] - start[w]) * t
        pts.append((p[0], p[1], p[2]))
    return pts


def _center_from_r(su: float, sv: float, eu: float, ev: float,
                   r_signed: float, clockwise: bool) -> Tuple[float, float, float]:
    r = abs(r_signed)
    chord = math.hypot(eu - su, ev - sv)
    if chord < 1e-12:
        raise ValueError("R arc with identical endpoints is ambiguous")
    if r < chord / 2 - 1e-9:
        raise ValueError("R smaller than half chord")
    mx, my = (su + eu) / 2, (sv + ev) / 2
    h = math.sqrt(max(0.0, r * r - (chord / 2) ** 2))
    dx, dy = (eu - su) / chord, (ev - sv) / chord
    nx, ny = -dy, dx
    # 两个候选圆心：对 a0=atan2(start-center)，扫向终点。
    c1 = (mx + nx * h, my + ny * h)
    c2 = (mx - nx * h, my - ny * h)
    a01 = math.atan2(sv - c1[1], su - c1[0])
    a11 = math.atan2(ev - c1[1], eu - c1[0])
    d1 = _norm(a11 - a01)
    # 圆心候选决定扫角 d；|sweep|<=π 为小弧。
    # G3(CCW): sweep=d ; G2(CW): sweep=d-2π。
    minor = r_signed >= 0
    # CCW 小弧需要 d<=π；CW 小弧需要 d>=π（其 |d-2π|<=π）。
    c1_is_minor = (d1 <= math.pi + 1e-9)
    if not clockwise:
        choose_c1 = (c1_is_minor == minor)
    else:
        choose_c1 = (c1_is_minor != minor)
    cx, cy = c1 if choose_c1 else c2
    return cx, cy, r


def _norm(angle: float) -> float:
    while angle <= 0:
        angle += 2 * math.pi
    while angle > 2 * math.pi:
        angle -= 2 * math.pi
    return angle


def line_points(start: Point, end: Point) -> List[Point]:
    return [start, end]


def resample(polyline: Sequence[Point], count: int = 24) -> List[Point]:
    """按弧长等距重采样，使 IJK 弧与 R 弧可做几何等价比较。"""
    if len(polyline) == 1:
        return [polyline[0]] * count
    seg_lens = []
    total = 0.0
    for a, b in zip(polyline, polyline[1:]):
        d = math.sqrt(sum((b[i] - a[i]) ** 2 for i in range(3)))
        seg_lens.append(d)
        total += d
    if total < 1e-12:
        return [polyline[0]] * count
    out: List[Point] = []
    seg_idx = 0
    for k in range(count):
        target = total * k / (count - 1)
        acc = 0.0
        while seg_idx < len(seg_lens) - 1 and acc + seg_lens[seg_idx] < target - 1e-12:
            acc += seg_lens[seg_idx]
            seg_idx += 1
        seg_len = seg_lens[seg_idx] if seg_idx < len(seg_lens) else 0.0
        ratio = 0.0 if seg_len < 1e-12 else (target - acc) / seg_len
        a, b = polyline[seg_idx], polyline[seg_idx + 1]
        out.append(tuple(a[i] + (b[i] - a[i]) * ratio for i in range(3)))  # type: ignore
    out.append(polyline[-1])
    return out


def paths_close(a: Sequence[Point], b: Sequence[Point], tol: float = 1e-4) -> bool:
    """两条折线几何等价：等长重采样后逐点小于容差（mm）。"""
    if len(a) < 2 or len(b) < 2:
        return a == b
    ra, rb = resample(a), resample(b)
    return all(math.sqrt(sum((p[i] - q[i]) ** 2 for i in range(3))) <= tol
               for p, q in zip(ra, rb))


def segment_box_intersect(p: Point, q: Point,
                          box_min: Point, box_max: Point) -> Optional[Point]:
    """线段是否穿过 AABB（slab 法，数值稳定）。返回进入点（盒上或内部），否则 None。

    端点在盒内视为穿过；完全在盒外且不相交返回 None。
    """
    t_lo, t_hi = 0.0, 1.0
    enter_axis_point = None
    for i in range(3):
        d = q[i] - p[i]
        if abs(d) < 1e-15:
            if p[i] < box_min[i] - 1e-9 or p[i] > box_max[i] + 1e-9:
                return None
            continue
        t1 = (box_min[i] - p[i]) / d
        t2 = (box_max[i] - p[i]) / d
        t_near, t_far = (t1, t2) if t1 < t2 else (t2, t1)
        if t_near > t_lo:
            t_lo = t_near
        t_hi = min(t_hi, t_far)
        if t_lo > t_hi + 1e-12:
            return None
    if t_lo > 1.0 + 1e-12 or t_hi < -1e-12:
        return None
    t = min(1.0, max(0.0, t_lo))
    return tuple(p[i] + (q[i] - p[i]) * t for i in range(3))  # type: ignore
