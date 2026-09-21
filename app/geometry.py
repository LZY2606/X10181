"""圆弧几何计算（从解释后的状态生成，不做正则抽点）。"""
import math
from typing import List, Optional, Sequence, Tuple

Vec3 = Tuple[float, float, float]

TOL = 1e-7


def _at(p, axis: str) -> float:
    if isinstance(p, dict):
        return p[axis]
    return p[{"x": 0, "y": 1, "z": 2}[axis]]

# 各平面内 (第一轴, 第二轴, 圆心对应第一轴, 圆心对应第二轴)
# 右手法则：平面法线 = cross(e1, e2)，逆时针 G3 使转角 sweep 为正
PLANES = {
    "G17": ("x", "y", "i", "j", 0.0, 0.0, 1.0),
    "G18": ("z", "x", "k", "i", 0.0, -1.0, 0.0),
    "G19": ("y", "z", "j", "k", 1.0, 0.0, 0.0),
}


def _norm_angle(a: float) -> float:
    while a <= -math.pi:
        a += 2.0 * math.pi
    while a > math.pi:
        a -= 2.0 * math.pi
    return a


def arc_points(start: Vec3, end: Vec3, plane: str, clockwise: bool,
               ijk: Optional[dict], r: Optional[float],
               helical_axis: Optional[str] = None,
               max_chord: float = 0.5) -> List[Vec3]:
    """由起终点与圆心（IJK）或半径 R 计算离散圆弧点（含起终点）。

    helical_axis: 非平面第三轴，圆弧角度线性插值到终点坐标。
    """
    u_axis, v_axis, i_axis, j_axis, nx, ny, nz = PLANES[plane]
    u0, v0 = _at(start, u_axis), _at(start, v_axis)
    u1, v1 = _at(end, u_axis), _at(end, v_axis)

    if ijk is not None:
        cu = u0 + float(ijk.get(i_axis, 0.0))
        cv = v0 + float(ijk.get(j_axis, 0.0))
    else:
        assert r is not None
        rr = abs(float(r))
        if rr < TOL:
            return [start, end]
        dx, dy = u1 - u0, v1 - v0
        d = math.hypot(dx, dy)
        if d < TOL:
            # R 写法下起止重合无法确定圆，退化为点
            return [start, end]
        if d > 2.0 * rr + 1e-6:
            return [start, end]
        mx, my = (u0 + u1) / 2.0, (v0 + v1) / 2.0
        h = math.sqrt(max(0.0, rr * rr - (d / 2.0) ** 2))
        ox, oy = -dy / d * h, dx / d * h
        if float(r) < 0.0:
            ox, oy = -ox, -oy
        cu, cv = mx + ox, my + oy

    ru0, rv0 = u0 - cu, v0 - cv
    ru1, rv1 = u1 - cu, v1 - cv
    rad0 = math.hypot(ru0, rv0)
    rad1 = math.hypot(ru1, rv1)
    if rad0 < TOL or rad1 < TOL:
        return [start, end]
    a0 = math.atan2(rv0, ru0)
    a1 = math.atan2(rv1, ru1)
    if clockwise:  # G2
        sweep = _norm_angle(a0 - a1)
        if abs(sweep) < TOL and math.hypot(u1 - u0, v1 - v0) > TOL:
            sweep = 2.0 * math.pi
        angles = [a0 - t * sweep for t in _fractions(sweep, rad0, max_chord)]
    else:  # G3
        sweep = _norm_angle(a1 - a0)
        if abs(sweep) < TOL and math.hypot(u1 - u0, v1 - v0) > TOL:
            sweep = 2.0 * math.pi
        angles = [a0 + t * sweep for t in _fractions(sweep, rad0, max_chord)]

    h_axis = helical_axis
    h0 = _at(start, h_axis) if h_axis else 0.0
    h1 = _at(end, h_axis) if h_axis else 0.0
    out: List[Vec3] = []
    for idx, ang in enumerate(angles):
        frac = idx / (len(angles) - 1) if len(angles) > 1 else 0.0
        u = cu + rad0 * math.cos(ang)
        v = cv + rad0 * math.sin(ang)
        p: dict = {"x": _at(start, "x"), "y": _at(start, "y"),
                   "z": _at(start, "z")}
        p[u_axis] = u
        p[v_axis] = v
        if h_axis:
            p[h_axis] = h0 + (h1 - h0) * frac
        out.append((p["x"], p["y"], p["z"]))
    out[-1] = (_at(end, "x"), _at(end, "y"), _at(end, "z"))
    return out


def _fractions(sweep: float, radius: float, max_chord: float) -> Sequence[float]:
    if abs(sweep) < TOL:
        return [0.0, 1.0]
    n = max(2, int(math.ceil(abs(sweep) * max(radius, 1e-9) / max(max_chord, 1e-9))) + 1)
    n = min(n, 400)
    return [k / (n - 1) for k in range(n)]


def segment_intersects_box(a: Vec3, b: Vec3, lo: Vec3, hi: Vec3,
                           steps: int = 24) -> bool:
    """线段是否进入轴对齐盒内部（细分采样）。

    采用严格内部判定：仅擦过盒表面/棱角的线段不算穿越，
    避免机床初始位置恰好落在边界上时误报。
    """
    eps = 1e-6
    for k in range(steps + 1):
        t = k / steps
        x = a[0] + (b[0] - a[0]) * t
        y = a[1] + (b[1] - a[1]) * t
        z = a[2] + (b[2] - a[2]) * t
        if (lo[0] + eps < x < hi[0] - eps and
                lo[1] + eps < y < hi[1] - eps and
                lo[2] + eps < z < hi[2] - eps):
            return True
    return False
