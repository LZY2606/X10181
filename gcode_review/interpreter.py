"""G-code 解释器：逐行消费词元，维护模态状态并产出语义运动。

关键原则：
- 几何预览只来自本模块解释后的 Motion，绝不直接抽词元坐标；
- 无法识别的 G/M 码与厂商片段保留原文、标记 unknown，但不伪造运动，
  调用方据此阻止"安全无变化"结论；
- 每行产出 Step（前/后状态、语义、运动、错误、未知项）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .geometry import arc_points, line_points
from .lexer import Line, Program, parse_program
from .state import (CYCLE_CODES, GROUPS, MOTION_CODES, WCS_CODES, ModalState,
                    group_of)

INCH_TO_MM = 25.4
AXIS_ADDR = {"X": 0, "Y": 1, "Z": 2}


@dataclass
class Motion:
    id: int                      # 程序内全局运动序号
    kind: str                    # rapid/feed/arc_cw/arc_ccw/cycle/plunge/retract/dwell
    points: List[Tuple[float, float, float]]  # 工件坐标折线 mm（含起点）
    line_index: int
    feed: float
    detail: Dict[str, object] = field(default_factory=dict)

    def machine_points(self, state: ModalState) -> List[Tuple[float, float, float]]:
        ox, oy, oz = state.offset()
        length = 0.0
        if state.tool_length in ("G43", "G43.1") and state.tool in state.tool_offsets:
            length = state.tool_offsets[state.tool]
        return [(x + ox, y + oy, z + oz + length) for x, y, z in self.points]


@dataclass
class Step:
    line_index: int
    before: ModalState
    after: ModalState
    motions: List[Motion] = field(default_factory=list)
    g_codes: List[float] = field(default_factory=list)
    m_codes: List[float] = field(default_factory=list)
    unknown_g: List[float] = field(default_factory=list)
    unknown_m: List[float] = field(default_factory=list)
    unknown_fragments: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    is_dwell: bool = False
    dwell_seconds: float = 0.0
    # 本行声明的语义（规范化），供比较层判 "等价模态重申"
    declared: List[str] = field(default_factory=list)


@dataclass
class Result:
    program: Program
    steps: List[Step]
    motions: List[Motion]
    unknowns: List[Dict[str, object]]
    errors: List[Dict[str, object]]

    def step_of_line(self, index: int) -> Step:
        return self.steps[index]


def _fmt_code(code: float) -> str:
    return str(int(code)) if float(code).is_integer() else str(code)


class _Interp:
    def __init__(self, program: Program, offsets: Optional[Dict[str, Dict[str, float]]] = None,
                 tool_offsets: Optional[Dict[int, float]] = None):
        self.program = program
        self.state = ModalState()
        if offsets:
            for name, xyz in offsets.items():
                code = _wcs_name_to_code(name)
                if code:
                    wid = WCS_CODES[code]
                    self.state.wcs_offsets[wid] = (
                        xyz.get("x", 0.0), xyz.get("y", 0.0), xyz.get("z", 0.0))
        if tool_offsets:
            self.state.tool_offsets = {int(k): float(v) for k, v in tool_offsets.items()}
        self.motion_seq = 0
        # 固定循环模态参数（G81...）
        self.cycle_data: Dict[str, float] = {}
        self.steps: List[Step] = []
        self.motions: List[Motion] = []
        self.unknowns: List[Dict[str, object]] = []
        self.errors: List[Dict[str, object]] = []

    def new_motion(self, kind: str, points, line_index: int,
                   detail: Optional[Dict[str, object]] = None) -> Motion:
        self.motion_seq += 1
        mot = Motion(
            id=self.motion_seq, kind=kind, points=[tuple(p) for p in points],
            line_index=line_index, feed=self.state.feed, detail=detail or {})
        self.motions.append(mot)
        return mot

    def run(self) -> Result:
        for line in self.program.lines:
            self.steps.append(self.exec_line(line))
        return Result(self.program, self.steps, self.motions,
                      self.unknowns, self.errors)

    # -- 单行 -------------------------------------------------------------
    def exec_line(self, line: Line) -> Step:
        before = self.state.clone()
        step = Step(line_index=line.index, before=before,
                    after=self.state.clone())
        g_words = [w.value for w in line.words if w.letter == "G"]
        m_words = [w.value for w in line.words if w.letter == "M"]
        step.g_codes = g_words
        step.m_codes = m_words

        # 分组：同组取最后一个（ISO 语义），重复出现记入 declared
        g_by_group: Dict[str, List[float]] = {}
        g_nonmodal: List[float] = []
        for code in g_words:
            grp = group_of(code)
            if grp == "nonmodal" or code in (4, 9, 61, 61.1, 64):
                g_nonmodal.append(code)
                continue
            if grp:
                g_by_group.setdefault(grp, []).append(code)
            else:
                if code in (53, 92, 92.1, 92.2, 10):
                    g_nonmodal.append(code)
                else:
                    step.unknown_g.append(code)

        for code in m_words:
            if code not in KNOWN_M:
                step.unknown_m.append(code)

        # 词法无法归类的厂商片段（宏表达式等）
        for frag in line.vendor_fragments:
            step.unknown_fragments.append(frag)

        try:
            self._apply_modal_groups(g_by_group, line, step)
            self._apply_nonmodal(g_nonmodal, line, step)
            self._apply_m_codes(m_words, step)
            self._apply_addresses_and_motion(g_by_group, g_nonmodal, line, step)
        except _InterpError as exc:
            step.errors.append(str(exc))
            self.errors.append({"line": line.index, "error": str(exc),
                                "text": line.raw})

        step.after = self.state.clone()

        # 记录未知项（保留原文，阻断安全结论）
        for code in step.unknown_g:
            self.unknowns.append({"line": line.index, "kind": "G",
                                  "code": f"G{_fmt_code(code)}",
                                  "text": line.raw})
        for code in step.unknown_m:
            self.unknowns.append({"line": line.index, "kind": "M",
                                  "code": f"M{_fmt_code(code)}",
                                  "text": line.raw})
        for frag in step.unknown_fragments:
            self.unknowns.append({"line": line.index, "kind": "fragment",
                                  "code": frag, "text": line.raw})
        return step

    # -- 模态 G 组 --------------------------------------------------------
    def _apply_modal_groups(self, g_by_group, line: Line, step: Step) -> None:
        declared: List[str] = []
        if "units" in g_by_group:
            code = g_by_group["units"][-1]
            new_units = "inch" if code == 20 else "mm"
            if new_units != self.state.units:
                declared.append(f"units:{new_units}")
            self.state.units = new_units
        if "distance" in g_by_group:
            code = g_by_group["distance"][-1]
            self.state.distance = "incremental" if code == 91 else "absolute"
            declared.append(f"distance:{self.state.distance}")
        if "plane" in g_by_group:
            self.state.plane = int(g_by_group["plane"][-1])
            declared.append(f"plane:G{self.state.plane}")
        if "feed_mode" in g_by_group:
            code = g_by_group["feed_mode"][-1]
            self.state.feed_mode = f"G{int(code)}"
            declared.append(f"feed_mode:{self.state.feed_mode}")
        if "wcs" in g_by_group:
            code = g_by_group["wcs"][-1]
            self.state.wcs = WCS_CODES[code]
            declared.append(f"wcs:G{_fmt_code(code)}")
        if "cutter_comp" in g_by_group:
            code = g_by_group["cutter_comp"][-1]
            self.state.cutter_comp = f"G{int(code)}"
            declared.append(f"cutter:{self.state.cutter_comp}")
        if "tool_length" in g_by_group:
            code = g_by_group["tool_length"][-1]
            if code == 49:
                self.state.tool_length = "G49"
            else:
                self.state.tool_length = f"G{_fmt_code(code)}"
            declared.append(f"tool_length:{self.state.tool_length}")
        if "cycle_retract" in g_by_group:
            self.state.cycle_retract = f"G{int(g_by_group['cycle_retract'][-1])}"
            declared.append(f"retract:{self.state.cycle_retract}")
        if "motion" in g_by_group:
            codes = g_by_group["motion"]
            # G80 与真正运动码可同块（罕见）；最后一个 motion 组成员生效
            code = codes[-1]
            if code == 80:
                self.state.cycle = 80.0
                self.state.motion = 1.0
                self.state.motion_code = "G1"
                self.cycle_data = {}
                declared.append("cycle:G80")
            elif code in CYCLE_CODES:
                self.state.cycle = code
                self.state.motion = 1.0
                self.state.motion_code = f"G{int(code)}"
                declared.append(f"cycle:G{int(code)}")
            elif code in (0, 1, 2, 3, 38.2):
                # 出现运动码意味着离开固定循环模态
                self.state.cycle = 80.0
                self.cycle_data = {}
                self.state.motion = code
                self.state.motion_code = f"G{_fmt_code(code)}"
        step.declared.extend(declared)

    # -- 非模态 G：G4/G9/G53/G61/G64/G92/G10 -----------------------------
    def _apply_nonmodal(self, codes: List[float], line: Line, step: Step) -> None:
        scale = INCH_TO_MM if self.state.units == "inch" else 1.0
        for code in codes:
            if code == 4:
                step.is_dwell = True
                p = line.last("P")
                x = line.last("X")
                if p is not None:
                    step.dwell_seconds = p.value / 1000.0
                elif x is not None:
                    step.dwell_seconds = x.value
                step.declared.append(f"dwell:{step.dwell_seconds}")
            elif code in (9, 61, 61.1, 64):
                step.declared.append(f"exactstop:G{_fmt_code(code)}")
            elif code == 92:
                # 设定坐标系：把当前工件坐标重定义为给定值 -> 计算 g92_shift
                self._do_g92(line, scale)
                step.declared.append("frameshift:G92")
            elif code in (92.1, 92.2):
                self.state.g92_shift = (0.0, 0.0, 0.0)
                step.declared.append("frameshift:clear")
            elif code == 10:
                self._do_g10(line, scale, step)
            elif code == 53:
                step.declared.append("frame:G53")

    def _do_g92(self, line: Line, scale: float) -> None:
        # 工件坐标逻辑值 Pw 与机床坐标 M: M = Pw + offset + g92_shift + length。
        # G92 Xx 表示"当前机床位置现在代表工件坐标 x"，
        # 因此新 shift = M - (x + base_offset + length)。
        length = self._tool_length_mm()
        mpos = self.state.machine_pos()
        new_pos = list(self.state.pos)
        for addr, idx in AXIS_ADDR.items():
            w = line.last(addr)
            if w is not None:
                new_pos[idx] = w.value * scale
        # M = Pw + wcs_offset + g92_shift + length => 求新 shift
        wcs_only = self.state.wcs_offsets[self.state.wcs]
        shifts = list(self.state.g92_shift)
        for idx in range(3):
            extra = length if idx == 2 else 0.0
            shifts[idx] = mpos[idx] - new_pos[idx] - wcs_only[idx] - extra
        self.state.g92_shift = tuple(shifts)  # type: ignore
        self.state.pos = tuple(new_pos)  # type: ignore

    def _do_g10(self, line: Line, scale: float, step: Step) -> None:
        l = line.last("L")
        p = line.last("P")
        if l is None or p is None:
            return
        if int(l.value) == 2 and 0 <= int(p.value) <= 9:
            wid = int(p.value)
            cur = dict(self.state.wcs_offsets)
            vals = list(cur[wid]) if wid > 0 else [0.0, 0.0, 0.0]
            for addr, idx in AXIS_ADDR.items():
                w = line.last(addr)
                if w is not None:
                    vals[idx] = w.value * scale
            if wid == 0:
                for k in cur:
                    cur[k] = tuple(vals)  # type: ignore
            else:
                cur[wid] = tuple(vals)  # type: ignore
            self.state.wcs_offsets = cur
            step.declared.append(f"g10:P{wid}")
        else:
            step.errors.append(f"unsupported G10 L{_fmt_code(l.value)} P{p.value}")

    def _tool_length_mm(self) -> float:
        if self.state.tool_length in ("G43", "G43.1"):
            return self.state.tool_offsets.get(self.state.tool, 0.0)
        return 0.0

    # -- M 码 -------------------------------------------------------------
    def _apply_m_codes(self, codes: List[float], step: Step) -> None:
        for code in codes:
            if code in (0, 1):
                pass  # 程序停/可选停
            elif code in (2, 30):
                step.declared.append("program_end")
            elif code == 3:
                self.state.spindle_on = True
                self.state.spindle_dir = "cw"
                step.declared.append("spindle:cw")
            elif code == 4:
                self.state.spindle_on = True
                self.state.spindle_dir = "ccw"
                step.declared.append("spindle:ccw")
            elif code == 5:
                self.state.spindle_on = False
                self.state.spindle_dir = "off"
                step.declared.append("spindle:off")
            elif code in (8, 7):
                self.state.coolant = True
                step.declared.append("coolant:on")
            elif code == 9:
                self.state.coolant = False
                step.declared.append("coolant:off")
            elif code == 6:
                step.declared.append("tool_change")

    # -- 地址词 + 运动 -----------------------------------------------------
    def _apply_addresses_and_motion(self, g_by_group, g_nonmodal,
                                    line: Line, step: Step) -> None:
        scale = INCH_TO_MM if self.state.units == "inch" else 1.0

        # F/S/T/H/D
        f = line.last("F")
        if f is not None:
            if self.state.feed_mode == "G93":
                self.state.feed = f.value  # 1/min
            else:
                self.state.feed = f.value * (INCH_TO_MM if self.state.units == "inch" else 1.0)
            step.declared.append(f"feed:{self.state.feed:.6g}")
        s = line.last("S")
        if s is not None:
            self.state.spindle_speed = s.value
            step.declared.append(f"speed:{s.value:g}")
        t = line.last("T")
        if t is not None:
            self.state.tool = int(t.value)
            step.declared.append(f"tool:{self.state.tool}")
        h = line.last("H")
        if h is not None and self.state.tool_length in ("G43", "G43.1"):
            self.state.tool = int(h.value)
        d = line.last("D")
        if d is not None and self.state.cutter_comp in ("G41", "G42"):
            step.declared.append(f"comp_d:{int(d.value)}")

        motion_codes = g_by_group.get("motion", [])
        explicit = [c for c in motion_codes if c in MOTION_CODES or c in CYCLE_CODES]
        has_axes = any(line.last(a) is not None for a in AXIS_ADDR)
        has_r = line.last("R") is not None
        has_ijk = any(line.last(a) is not None for a in ("I", "J", "K"))
        g53 = 53.0 in g_nonmodal

        # 固定循环执行：本行激活/重申循环，或仅有轴词且处于循环模态
        if self.state.cycle in CYCLE_CODES and (
                any(c in CYCLE_CODES for c in explicit) or
                (has_axes and not any(c in MOTION_CODES for c in explicit))):
            cycle_code = next((c for c in explicit if c in CYCLE_CODES), self.state.cycle)
            self._run_cycle(cycle_code, line, step, scale)
            return

        if not explicit and not (has_axes or has_r or has_ijk):
            return  # 纯模态/注释行
        if not explicit:
            # 模态运动重申
            code = self.state.motion
        else:
            code = explicit[-1]

        target = self._resolve_target(line, scale, g53_nonmodal=g53)
        start = tuple(self.state.pos)  # type: ignore
        if code == 0:
            mot = self.new_motion("rapid", line_points(start, target), line.index,
                                  {"g": "G0"})
            step.motions.append(mot)
        elif code == 1:
            mot = self.new_motion("feed", line_points(start, target), line.index,
                                  {"g": "G1", "feed": self.state.feed})
            step.motions.append(mot)
        elif code in (2, 3):
            center_addr, radius = self._arc_params(line, scale)
            try:
                pts = arc_points(start, target, self.state.plane,
                                 clockwise=(code == 2),
                                 center_addr=center_addr, radius=radius)
            except ValueError as exc:
                raise _InterpError(str(exc))
            kind = "arc_cw" if code == 2 else "arc_ccw"
            mot = self.new_motion(kind, pts, line.index,
                                  {"g": f"G{int(code)}", "plane": self.state.plane})
            step.motions.append(mot)
        elif code == 38.2:
            mot = self.new_motion("probe_feed", line_points(start, target),
                                  line.index, {"g": "G38.2"})
            step.motions.append(mot)
        else:
            return
        if step.is_dwell:
            self._emit_dwell(step)
        # G53 仅对本块生效：不改变持久 WCS（WCS 组未出现），目标点已按机床坐标处理
        self.state.pos = tuple(target)  # type: ignore

    def _resolve_target(self, line: Line, scale: float,
                        g53_nonmodal: bool = False) -> Tuple[float, float, float]:
        target = list(self.state.pos)
        if g53_nonmodal:
            # G53：机床绝对坐标，换算为工件坐标存储
            mpos = self.state.machine_pos()
            base = self.state.offset()
            length = self._tool_length_mm()
            for addr, idx in AXIS_ADDR.items():
                w = line.last(addr)
                if w is not None:
                    machine = w.value * scale
                    target[idx] = machine - base[idx] - (length if idx == 2 else 0.0)
            return tuple(target)  # type: ignore
        for addr, idx in AXIS_ADDR.items():
            w = line.last(addr)
            if w is not None:
                val = w.value * scale
                if self.state.distance == "incremental":
                    target[idx] += val
                else:
                    target[idx] = val
        return tuple(target)  # type: ignore

    def _arc_params(self, line: Line, scale: float):
        center_addr = None
        radius = None
        ijk = {}
        for addr in ("I", "J", "K"):
            w = line.last(addr)
            if w is not None:
                ijk[addr.lower()] = w.value * scale
        if ijk:
            center_addr = ijk
        r = line.last("R")
        if r is not None:
            radius = r.value * scale
        return center_addr, radius

    # -- 固定循环 ---------------------------------------------------------
    def _run_cycle(self, code: float, line: Line, step: Step, scale: float) -> None:
        # 更新循环模态数据：R/Z/F/Q/P，XY 为孔位
        for addr, key in (("R", "r"), ("Z", "z"), ("Q", "q"), ("P", "p")):
            w = line.last(addr)
            if w is not None:
                val = w.value * (scale if addr != "P" else 1.0)
                if addr == "P":
                    val = w.value / 1000.0
                self.cycle_data[key] = val
        feed_word = line.last("F")
        if feed_word is not None:
            self.state.feed = feed_word.value * (
                INCH_TO_MM if self.state.units == "inch" else 1.0)

        start = tuple(self.state.pos)  # type: ignore
        target = self._resolve_target(line, scale)
        x, y, z_target = target
        if "z" not in self.cycle_data:
            raise _InterpError(f"cycle G{int(code)} missing Z")
        if "r" not in self.cycle_data:
            raise _InterpError(f"cycle G{int(code)} missing R")
        r_plane = self.cycle_data["r"]
        z_bottom = self.cycle_data["z"]
        initial_z = start[2]
        # G91 时 R/Z 是相对初值的增量
        if self.state.distance == "incremental":
            z_word = line.last("Z")
            r_word = line.last("R")
            if z_word is not None:
                z_bottom = initial_z + z_word.value * scale
                self.cycle_data["z"] = z_bottom
            if r_word is not None:
                r_plane = initial_z + r_word.value * scale
                self.cycle_data["r"] = r_plane

        def add(kind, pts, detail=None):
            mot = self.new_motion(kind, pts, line.index, detail)
            step.motions.append(mot)
            return mot

        # 1) XY 定位 + Z 到 R 平面（快速）
        if abs(x - start[0]) > 1e-12 or abs(y - start[1]) > 1e-12:
            level = (x, y, initial_z)
            add("rapid", line_points(start, level), {"cycle_phase": "position_xy"})
        else:
            level = start
        rap_to_r = (x, y, r_plane)
        if abs(r_plane - level[2]) > 1e-12:
            add("rapid", line_points(level, rap_to_r), {"cycle_phase": "to_r"})

        # 2) 工作进给钻孔（G82 底部停；G83 分段啄钻；其余直进）
        if code == 83:
            q = self.cycle_data.get("q", 0.0)
            if q <= 0:
                raise _InterpError("G83 requires positive Q")
            cur = r_plane
            pecks: List[Tuple[float, float, float]] = []
            while cur > z_bottom + 1e-12:
                nxt = max(z_bottom, cur - q)
                add("plunge", line_points((x, y, cur), (x, y, nxt)),
                    {"cycle_phase": "peck_down"})
                cur = nxt
                if cur > z_bottom + 1e-12:
                    add("retract", line_points((x, y, cur), (x, y, r_plane)),
                        {"cycle_phase": "peck_retract"})
        else:
            add("plunge", line_points((x, y, r_plane), (x, y, z_bottom)),
                {"cycle_phase": "drill", "feed": self.state.feed})
        if code in (82, 89):
            dwell = self.cycle_data.get("p", 0.0)
            if dwell > 0:
                step.is_dwell = True
                step.dwell_seconds = dwell
                self._emit_dwell(step)
        # 镗/攻丝循环 G84/G85/G86/G87/G88/G89 的进给回退到 R
        if code in (84, 85, 86, 87, 88, 89):
            retract_z = initial_z if self.state.cycle_retract == "G98" else r_plane
            add("feed", line_points((x, y, z_bottom), (x, y, retract_z)),
                {"cycle_phase": "feed_out"})
        else:
            # G81/G82/G83：快速回退
            retract_z = initial_z if self.state.cycle_retract == "G98" else r_plane
            add("retract", line_points((x, y, z_bottom), (x, y, retract_z)),
                {"cycle_phase": "retract"})
        self.state.pos = (x, y,
                          initial_z if self.state.cycle_retract == "G98" else r_plane)

    def _emit_dwell(self, step: Step) -> None:
        if not step.motions:
            return
        last = step.motions[-1]
        pos = tuple(last.points[-1])  # type: ignore
        mot = self.new_motion("dwell", [pos, pos], step.line_index,
                              {"seconds": step.dwell_seconds})
        step.motions.append(mot)


KNOWN_M = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 30, 48, 49, 98, 99}


class _InterpError(Exception):
    pass


def _wcs_name_to_code(name: str) -> Optional[float]:
    name = name.upper().strip()
    table = {f"G{int(c) if float(c).is_integer() else c}": c for c in WCS_CODES}
    return table.get(name)


def interpret(program: Program,
              offsets: Optional[Dict[str, Dict[str, float]]] = None,
              tool_offsets: Optional[Dict[int, float]] = None) -> Result:
    return _Interp(program, offsets=offsets, tool_offsets=tool_offsets).run()


def interpret_text(text: bytes,
                   offsets: Optional[Dict[str, Dict[str, float]]] = None,
                   tool_offsets: Optional[Dict[int, float]] = None) -> Result:
    return interpret(parse_program(text), offsets=offsets, tool_offsets=tool_offsets)
