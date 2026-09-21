"""G-code 语义解释器。

逐行推进模态状态（单位、绝对/增量、工作坐标系、刀补、进给、主轴、
固定循环等），并由解释后的状态产生运动序列。所有长度单位内部统一为毫米。
"""
from dataclasses import dataclass, field, replace
from typing import Callable, Dict, List, Optional, Tuple

from .lexer import Line, Word, tokenize
from .geometry import arc_points, TOL

MM_PER_INCH = 25.4

# 已建模的 G 代码 -> 所属模态组
G_GROUPS = {
    "G0": 1, "G1": 1, "G2": 1, "G3": 1,
    "G4": 0,
    "G10": 0,
    "G17": 2, "G18": 2, "G19": 2,
    "G20": 6, "G21": 6,
    "G28": 0, "G30": 0,
    "G40": 7, "G41": 7, "G42": 7,
    "G43": 8, "G44": 8, "G49": 8,
    "G53": 0,
    "G54": 12, "G55": 12, "G56": 12, "G57": 12, "G58": 12, "G59": 12,
    "G80": 1,
    "G81": 1, "G82": 1, "G83": 1, "G84": 1, "G85": 1,
    "G90": 3, "G91": 3,
    "G98": 10, "G99": 10,
}
MISC_G = {"G28", "G30", "G53"}  # 非模态
WCS_BASE = {f"G5{i}": i - 4 for i in range(4, 10)}  # G54->1 ... G59->6
CYCLES = {"G81", "G82", "G83", "G84", "G85"}
M_KNOWN = {"M0", "M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8", "M9",
           "M30", "M98", "M99"}
AXES = ("x", "y", "z", "a", "b", "c")
# 确认后允许的效果
EFFECTS = ("no_effect", "modal_only", "motion_unverified")


@dataclass
class State:
    units: str = "G21"               # G20 英寸 / G21 毫米
    positioning: str = "G90"         # G90 绝对 / G91 增量
    plane: str = "G17"
    motion: str = "G0"
    cutter_comp: str = "G40"
    length_comp: str = "G49"
    wcs: str = "G54"
    retract_mode: str = "G98"
    feed: Optional[float] = None     # mm/min
    spindle: str = "M5"
    spindle_speed: Optional[float] = None
    coolant: bool = False
    cycle: Optional[str] = None
    cycle_params: Dict[str, float] = field(default_factory=dict)
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    a: float = 0.0
    b: float = 0.0
    c: float = 0.0
    known_axes: Dict[str, bool] = field(
        default_factory=lambda: {k: False for k in AXES})
    confirmed_modals: Dict[str, str] = field(default_factory=dict)

    def pos(self) -> Dict[str, float]:
        return {k: getattr(self, k) for k in AXES}

    def fingerprint(self) -> str:
        return "|".join([
            self.units, self.positioning, self.plane, self.motion,
            self.cutter_comp, self.length_comp, self.wcs, self.retract_mode,
            _fmt(self.feed), self.spindle,
            _fmt(self.spindle_speed), "1" if self.coolant else "0",
            self.cycle or "-",
            ",".join("%s:%s" % (k, _fmt(self.cycle_params.get(k)))
                     for k in sorted(self.cycle_params)),
            ",".join("%s=%s" % (k, self.confirmed_modals[k])
                     for k in sorted(self.confirmed_modals)),
        ])

    def snapshot(self) -> dict:
        return {
            "units": self.units, "positioning": self.positioning,
            "plane": self.plane, "motion": self.motion,
            "cutter_comp": self.cutter_comp, "length_comp": self.length_comp,
            "wcs": self.wcs, "retract_mode": self.retract_mode,
            "feed": self.feed, "spindle": self.spindle,
            "spindle_speed": self.spindle_speed, "coolant": self.coolant,
            "cycle": self.cycle,
            "cycle_params": dict(self.cycle_params),
            "position": {k: round(getattr(self, k), 6) for k in AXES},
            "known_axes": dict(self.known_axes),
            "confirmed_modals": dict(self.confirmed_modals),
        }


def _fmt(v: Optional[float]) -> str:
    if v is None:
        return "-"
    return ("%.6f" % v).rstrip("0").rstrip(".")


@dataclass
class Segment:
    kind: str                # rapid / linear / arc / dwell / reference
    points: List[Tuple[float, float, float]]
    line_index: int
    cycle: Optional[str] = None
    feed: Optional[float] = None
    clockwise: Optional[bool] = None
    cycle_rapid: bool = False
    cycle_cut: bool = False
    axis_unknown: bool = False
    generated: bool = False  # 固定循环展开出的辅助段

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "points": [[round(p[0], 6), round(p[1], 6), round(p[2], 6)]
                       for p in self.points],
            "line_index": self.line_index, "cycle": self.cycle,
            "feed": self.feed, "clockwise": self.clockwise,
            "cycle_rapid": self.cycle_rapid, "cycle_cut": self.cycle_cut,
            "axis_unknown": self.axis_unknown, "generated": self.generated,
        }


@dataclass
class UnknownCode:
    line_index: int
    code: str
    raw_text: str
    kind: str                 # vendor / unmodeled / macro
    confirmed: bool = False
    effect: Optional[str] = None
    note: str = ""


@dataclass
class LineResult:
    index: int
    raw: str
    start: int
    end: int
    newline: str
    g_codes: List[str]
    m_codes: List[str]
    signature: str
    before: dict
    after: dict
    before_fp: str
    after_fp: str
    motion_types: List[str]
    segment_count: int
    unknowns: List[UnknownCode] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    macro: bool = False

    def to_dict(self) -> dict:
        return {
            "index": self.index, "raw": self.raw, "start": self.start,
            "end": self.end, "newline": self.newline,
            "g_codes": self.g_codes, "m_codes": self.m_codes,
            "signature": self.signature,
            "before": self.before, "after": self.after,
            "before_fp": self.before_fp, "after_fp": self.after_fp,
            "motion_types": self.motion_types,
            "segment_count": self.segment_count,
            "unknowns": [vars(u) for u in self.unknowns],
            "errors": self.errors, "macro": self.macro,
        }


@dataclass
class ProgramModel:
    lines: List[LineResult]
    segments: List[Segment]
    unknowns: List[UnknownCode]
    blocked: bool
    block_reasons: List[str]
    unit_switches: List[int]
    final_state: dict
    text: bytes

    def to_dict(self) -> dict:
        return {
            "lines": [l.to_dict() for l in self.lines],
            "segments": [s.to_dict() for s in self.segments],
            "unknowns": [vars(u) for u in self.unknowns],
            "blocked": self.blocked,
            "block_reasons": self.block_reasons,
            "unit_switches": self.unit_switches,
            "final_state": self.final_state,
        }


ConfirmationResolver = Callable[[str], Optional[dict]]


def _canon_code(letter: str, word: Word) -> str:
    """把 G1 / G01 / G1.0 规范为 G1，G54.1 保留点号形式。"""
    try:
        val = float(word.value_text)
    except ValueError:
        return letter + word.value_text
    if abs(val - round(val)) < 1e-9:
        return "%s%d" % (letter, int(round(val)))
    return "%s%s" % (letter, word.value_text)


class ProgramInterpreter:
    def __init__(self, resolver: Optional[ConfirmationResolver] = None):
        self.resolver = resolver

    def run(self, text: bytes) -> ProgramModel:
        raw_lines = tokenize(text)
        st = State()
        line_results: List[LineResult] = []
        segments: List[Segment] = []
        unknowns: List[UnknownCode] = []
        block_reasons: List[str] = []
        unit_switches: List[int] = []
        prev_units = st.units

        for ln in raw_lines:
            before = st.snapshot()
            before_fp = st.fingerprint()
            wm = ln.word_map()

            g_codes = [_canon_code("G", w) for w in ln.words if w.letter == "G"]
            m_codes = [_canon_code("M", w) for w in ln.words if w.letter == "M"]
            line_unknowns: List[UnknownCode] = []

            # 收集无法识别的代码
            for code in g_codes:
                if code in G_GROUPS or code in MISC_G:
                    continue
                if code.startswith("G54.1"):
                    continue
                line_unknowns.append(self._resolve_unknown(
                    ln, code, "unmodeled", st, block_reasons, unknowns))
            for code in m_codes:
                if code in M_KNOWN:
                    continue
                line_unknowns.append(self._resolve_unknown(
                    ln, code, "vendor", st, block_reasons, unknowns))
            if ln.has_macro:
                line_unknowns.append(self._resolve_unknown(
                    ln, "#macro", "macro", st, block_reasons, unknowns))
            for err in ln.errors:
                block_reasons.append("第%d行 %s" % (ln.index + 1, err))

            motion_types: List[str] = []
            unit_group = [c for c in g_codes if G_GROUPS.get(c) == 6]
            if unit_group:
                new_units = unit_group[-1]
                if st.units != new_units:
                    unit_switches.append(ln.index)
                st.units = new_units

            try:
                self._apply_modals(g_codes, m_codes, wm, ln, st)
                new_segs, mt = self._motions(g_codes, wm, ln, st)
            except _InterpError as exc:
                block_reasons.append("第%d行 %s" % (ln.index + 1, str(exc)))
                new_segs, mt = [], []
            segments.extend(new_segs)
            motion_types.extend(mt)

            signature = self._signature(g_codes, m_codes, ln)
            lr = LineResult(
                index=ln.index, raw=ln.raw, start=ln.start, end=ln.end,
                newline=ln.newline, g_codes=g_codes, m_codes=m_codes,
                signature=signature, before=before, after=st.snapshot(),
                before_fp=before_fp, after_fp=st.fingerprint(),
                motion_types=motion_types,
                segment_count=len(new_segs), unknowns=line_unknowns,
                errors=list(ln.errors), macro=ln.has_macro)
            line_results.append(lr)
            prev_units = st.units

        blocked = len(block_reasons) > 0
        return ProgramModel(
            lines=line_results, segments=segments, unknowns=unknowns,
            blocked=blocked, block_reasons=block_reasons,
            unit_switches=unit_switches, final_state=st.snapshot(),
            text=text if isinstance(text, bytes) else text.encode("latin-1"))

    # ---- 未知/厂商指令 --------------------------------------------------
    def _resolve_unknown(self, ln: Line, code: str, kind: str,
                         st: State, block_reasons: List[str],
                         all_unknowns: List[UnknownCode]) -> UnknownCode:
        effect = None
        confirmed = False
        note = ""
        if self.resolver is not None and code != "#macro":
            conf = self.resolver(code)
            if conf:
                confirmed = True
                effect = conf.get("effect")
                note = conf.get("note", "")
                if effect == "modal_only":
                    st.confirmed_modals[code] = note or "confirmed"
        uc = UnknownCode(ln.index, code, ln.raw, kind, confirmed, effect, note)
        all_unknowns.append(uc)
        if not confirmed:
            if kind == "macro":
                block_reasons.append("第%d行包含宏表达式，语义无法离线确定" % (ln.index + 1))
            else:
                block_reasons.append(
                    "第%d行无法识别的厂商指令 %s" % (ln.index + 1, code))
        elif effect == "motion_unverified":
            block_reasons.append(
                "第%d行 %s 经人工确认但运动语义未验证" % (ln.index + 1, code))
        return uc

    # ---- 模态更新 -------------------------------------------------------
    def _apply_modals(self, g_codes: List[str], m_codes: List[str],
                      wm: Dict[str, Word], ln: Line, st: State) -> None:
        group_seen = set()
        for code in g_codes:
            grp = G_GROUPS.get(code)
            if grp is None:
                continue
            if code in MISC_G:
                continue
            if grp == 0:
                continue  # 非模态（G4/G10 单独处理）
            if grp in group_seen and grp != 1:
                raise _InterpError("同一行内模态组%d冲突" % grp)
            group_seen.add(grp)
            if grp == 1:
                if code in CYCLES:
                    st.cycle = code
                    st.motion = code
                elif code == "G80":
                    st.cycle = None
                    st.cycle_params = {}
                    st.motion = "G0"
                else:
                    st.cycle = None
                    st.cycle_params = {}
                    st.motion = code
            elif grp == 2:
                st.plane = code
            elif grp == 3:
                st.positioning = code
            elif grp == 7:
                st.cutter_comp = code
            elif grp == 8:
                st.length_comp = code
            elif grp == 10:
                st.retract_mode = code
            elif grp == 12:
                st.wcs = code
        # 扩展工作偏置 G54.1 Pn
        for w in ln.words:
            if w.letter == "G" and w.value_text.startswith("54.1"):
                p = wm.get("P")
                st.wcs = "G54.1P%s" % (_fmt(p.number) if p and p.number is not None else "?")

        if "F" in wm and wm["F"].number is not None:
            st.feed = self._to_mm(wm["F"].number, st)
        if "S" in wm and wm["S"].number is not None:
            st.spindle_speed = wm["S"].number
        for code in m_codes:
            if code in ("M3", "M4", "M5"):
                st.spindle = code
            elif code in ("M8", "M7"):
                st.coolant = True
            elif code == "M9":
                st.coolant = False
            elif code in ("M2", "M30"):
                st.spindle = "M5"
                st.coolant = False

    def _to_mm(self, value: float, st: State) -> float:
        return value * MM_PER_INCH if st.units == "G20" else value

    # ---- 运动 -----------------------------------------------------------
    def _target(self, wm: Dict[str, Word], st: State,
                nonmodal: Optional[str]) -> Tuple[Dict[str, float], bool]:
        pos_mode = nonmodal if nonmodal is not None else st.positioning
        target = {k: getattr(st, k) for k in AXES}
        unknown_axis = False
        for axis in AXES:
            key = axis.upper()
            if key in wm and wm[key].number is not None:
                val = self._to_mm(wm[key].number, st)
                if pos_mode == "G91":
                    target[axis] = target[axis] + val
                else:
                    target[axis] = val
                st.known_axes[axis] = True
            elif not st.known_axes[axis]:
                unknown_axis = True
        return target, unknown_axis

    def _motions(self, g_codes: List[str], wm: Dict[str, Word],
                 ln: Line, st: State) -> Tuple[List[Segment], List[str]]:
        # G4 暂停
        if "G4" in g_codes:
            return [Segment("dwell", [_p3(st)], ln.index)], ["dwell"]
        # G28/G30 回参考点（经中间点；机床坐标未知）
        if "G28" in g_codes or "G30" in g_codes:
            target, unk = self._target(wm, st, None)
            seg = Segment("reference", [_p3(st), _p3(target)], ln.index,
                          axis_unknown=unk)
            for k in AXES:
                setattr(st, k, target[k])
            return [seg], ["reference"]
        # G53 机床坐标行（非模态绝对）
        nonmodal = "G90" if "G53" in g_codes else None
        motion_group = [c for c in g_codes if G_GROUPS.get(c) == 1]
        explicit = bool(motion_group)

        has_axes = any(a.upper() in wm for a in AXES)
        has_cycle_data = any(k in wm for k in ("X", "Y", "Z", "R"))

        # 固定循环模态执行
        if st.cycle and (has_cycle_data or (has_axes and not explicit)):
            return self._run_cycle(wm, ln, st), ["cycle"]

        if not has_axes:
            return [], []
        target, unk = self._target(wm, st, nonmodal)
        kind_code = motion_group[-1] if motion_group else st.motion
        if kind_code in CYCLES:  # 循环被取消前不会落到这里
            return [], []
        start = _p3(st)
        end = _p3(target)
        if kind_code == "G0":
            seg = Segment("rapid", [start, end], ln.index,
                          axis_unknown=unk, feed=None)
            mt = ["rapid"]
        elif kind_code == "G1":
            seg = Segment("linear", [start, end], ln.index, feed=st.feed,
                          axis_unknown=unk)
            mt = ["linear"]
        elif kind_code in ("G2", "G3"):
            pts, bad = self._arc(start, end, kind_code, wm, st, ln.index)
            seg = Segment("arc", pts, ln.index, feed=st.feed,
                          clockwise=(kind_code == "G2"), axis_unknown=unk or bad)
            mt = ["arc"]
        else:
            seg = Segment("linear", [start, end], ln.index, feed=st.feed,
                          axis_unknown=unk)
            mt = ["linear"]
        for k in AXES:
            setattr(st, k, target[k])
        return [seg], mt

    def _arc(self, start, end, code: str, wm: Dict[str, Word], st: State,
             line_index: int):
        plane = st.plane
        ijk = {}
        for key in ("I", "J", "K"):
            if key in wm and wm[key].number is not None:
                ijk[key.lower()] = self._to_mm(wm[key].number, st)
        rval = self._to_mm(wm["R"].number, st) if wm.get("R") and wm["R"].number is not None else None
        helical = {"G17": "z", "G18": "y", "G19": "x"}[plane]
        if not ijk and rval is None:
            # 缺少圆心参数，退化为直线但标记异常
            return [start, end], True
        pts = arc_points(start, end, plane, code == "G2",
                         ijk if ijk else None, rval,
                         helical_axis=helical)
        return pts, False

    # ---- 固定循环 -------------------------------------------------------
    def _run_cycle(self, wm: Dict[str, Word], ln: Line,
                   st: State) -> List[Segment]:
        p = st.cycle_params
        for key in ("Z", "R", "P", "Q", "F"):
            if key in wm and wm[key].number is not None:
                p[key.lower()] = self._to_mm(wm[key].number, st) if key in ("Z", "R", "Q", "F") else wm[key].number
        if "F" in wm and wm["F"].number is not None:
            st.feed = self._to_mm(wm["F"].number, st)
        target, unk = self._target(wm, st, None)
        initial_z = st.z
        z_safe = p.get("r")
        z_bottom = p.get("z")
        segs: List[Segment] = []
        cycle = st.cycle

        # 1) XY 快速定位到孔位（保持当前 Z）；低于 R 面时先抬刀
        start = _p3(st)
        plane_z = initial_z
        if z_safe is not None and initial_z < z_safe:
            segs.append(Segment("rapid", [start, (st.x, st.y, z_safe)],
                                ln.index, cycle=cycle, cycle_rapid=True,
                                generated=True))
            plane_z = z_safe
        segs.append(Segment("rapid",
                            [(st.x, st.y, plane_z),
                             (target["x"], target["y"], plane_z)],
                            ln.index, cycle=cycle, cycle_rapid=True,
                            generated=True))
        # 2) Z 向快进到 R 面
        if z_safe is not None and plane_z > z_safe:
            segs.append(Segment("rapid",
                                [(target["x"], target["y"], plane_z),
                                 (target["x"], target["y"], z_safe)],
                                ln.index, cycle=cycle, cycle_rapid=True,
                                generated=True))
        if z_safe is None or z_bottom is None:
            for k in AXES:
                setattr(st, k, target[k])
            return segs
        # 3) 从 R 面切削到孔底
        r_pt = (target["x"], target["y"], z_safe)
        if cycle == "G83":
            peck = p.get("q") or abs(z_safe - z_bottom)
            z_cur = z_safe
            while z_cur > z_bottom:
                nxt = max(z_bottom, z_cur - peck)
                segs.append(Segment("linear",
                                    [(target["x"], target["y"], z_cur),
                                     (target["x"], target["y"], nxt)],
                                    ln.index, cycle=cycle, feed=st.feed,
                                    cycle_cut=True, generated=True))
                z_cur = nxt
                if z_cur > z_bottom:
                    segs.append(Segment("rapid",
                                        [(target["x"], target["y"], z_cur),
                                         (target["x"], target["y"], z_cur + peck * 0.5)],
                                        ln.index, cycle=cycle,
                                        cycle_rapid=True, generated=True))
        else:
            segs.append(Segment("linear", [r_pt, (target["x"], target["y"], z_bottom)],
                                ln.index, cycle=cycle, feed=st.feed,
                                cycle_cut=True, generated=True))
        # 4) 退回
        if cycle in ("G84", "G85"):
            retract_kind = "linear"
        else:
            retract_kind = "rapid"
        retract_z = initial_z if st.retract_mode == "G98" else z_safe
        segs.append(Segment(retract_kind,
                            [(target["x"], target["y"], z_bottom),
                             (target["x"], target["y"], retract_z)],
                            ln.index, cycle=cycle,
                            feed=st.feed if retract_kind == "linear" else None,
                            cycle_cut=retract_kind == "linear",
                            cycle_rapid=retract_kind == "rapid",
                            generated=True))
        for k in AXES:
            setattr(st, k, target[k])
        st.z = retract_z
        return segs

    def _signature(self, g_codes: List[str], m_codes: List[str],
                   ln: Line) -> str:
        """规范化语义签名：忽略空白/注释/行号 N，保留全部地址词与代码。"""
        codes = list(dict.fromkeys(g_codes + m_codes))
        parts = list(codes)
        for w in ln.words:
            if w.letter in ("N", "O"):
                continue
            if w.letter in ("G", "M"):
                continue
            if w.number is None:
                parts.append(w.letter + w.value_text)
            else:
                num = w.number
                text = ("%.6f" % num).rstrip("0").rstrip(".")
                parts.append("%s%s" % (w.letter, text))
        return " ".join(parts)


def _p3(d) -> Tuple[float, float, float]:
    if isinstance(d, dict):
        return (d["x"], d["y"], d["z"])
    return (d.x, d.y, d.z)


class _InterpError(Exception):
    pass


def interpret(text: bytes, resolver: Optional[ConfirmationResolver] = None
              ) -> ProgramModel:
    return ProgramInterpreter(resolver).run(text)
