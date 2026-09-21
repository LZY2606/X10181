"""模态机床状态：单位、绝对/增量、工作坐标系、补偿、进给、主轴、固定循环。

所有内部长度坐标统一为毫米；inch 输入在解释器里换算。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Tuple

# 支持的工作坐标系 G 码 -> 内部编号
WCS_CODES = {54: 1, 55: 2, 56: 3, 57: 4, 58: 5, 59: 6, 59.1: 7, 59.2: 8, 59.3: 9}

# 模态 G 码分组（以 Fanuc/ISO 常见分组为准）
GROUPS: Dict[float, str] = {}
for _c in (0, 1, 2, 3, 38.2, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89):
    GROUPS[_c] = "motion"
for _c in (17, 18, 19):
    GROUPS[_c] = "plane"
for _c in (90, 91):
    GROUPS[_c] = "distance"
for _c in (93, 94):
    GROUPS[_c] = "feed_mode"
for _c in (20, 21):
    GROUPS[_c] = "units"
for _c in (40, 41, 42):
    GROUPS[_c] = "cutter_comp"
for _c in (43, 43.1, 49):
    GROUPS[_c] = "tool_length"
for _c in (4, 9, 61, 61.1, 64):
    GROUPS[_c] = "nonmodal"
for _c in (54, 55, 56, 57, 58, 59, 59.1, 59.2, 59.3):
    GROUPS[_c] = "wcs"
for _c in (53, 92, 92.1, 92.2):
    GROUPS[_c] = "frame_nonmodal"
for _c in (98, 99):
    GROUPS[_c] = "cycle_retract"

CYCLE_CODES = {81, 82, 83, 84, 85, 86, 87, 88, 89}
MOTION_CODES = {0, 1, 2, 3, 38.2}


@dataclass
class ModalState:
    """解释到某行之前/之后的完整模态快照。"""
    units: str = "mm"                  # "mm" | "inch"
    distance: str = "absolute"         # G90 | G91
    plane: int = 17                    # G17/G18/G19
    wcs: int = 1                       # 工作坐标系编号 1..9
    cutter_comp: str = "G40"           # G40/G41/G42
    tool_length: str = "G49"           # G49/G43/G43.1
    cycle: float = 80.0                # 80 表示无固定循环
    cycle_retract: str = "G98"         # G98 回初始平面 / G99 回 R
    feed_mode: str = "G94"
    motion: float = 0.0                # 当前模态运动码
    motion_code: str = "G0"
    feed: float = 0.0                  # mm/min
    spindle_speed: float = 0.0
    spindle_on: bool = False
    spindle_dir: str = "off"           # off/cw/ccw
    coolant: bool = False
    tool: int = 0
    pos: Tuple[float, float, float] = (0.0, 0.0, 0.0)  # 工件坐标 mm
    # 每个工作坐标系的偏置（mm），G10 L2 可改；默认全零
    wcs_offsets: Dict[int, Tuple[float, float, float]] = field(
        default_factory=lambda: {i: (0.0, 0.0, 0.0) for i in range(1, 10)})
    tool_offsets: Dict[int, float] = field(default_factory=dict)  # H 号 -> mm
    # G92 附加工件移位
    g92_shift: Tuple[float, float, float] = (0.0, 0.0, 0.0)

    def clone(self) -> "ModalState":
        return ModalState(
            units=self.units,
            distance=self.distance,
            plane=self.plane,
            wcs=self.wcs,
            cutter_comp=self.cutter_comp,
            tool_length=self.tool_length,
            cycle=self.cycle,
            cycle_retract=self.cycle_retract,
            feed_mode=self.feed_mode,
            motion=self.motion,
            motion_code=self.motion_code,
            feed=self.feed,
            spindle_speed=self.spindle_speed,
            spindle_on=self.spindle_on,
            spindle_dir=self.spindle_dir,
            coolant=self.coolant,
            tool=self.tool,
            pos=self.pos,
            wcs_offsets={k: v for k, v in self.wcs_offsets.items()},
            tool_offsets=dict(self.tool_offsets),
            g92_shift=self.g92_shift,
        )

    def offset(self) -> Tuple[float, float, float]:
        wx, wy, wz = self.wcs_offsets[self.wcs]
        sx, sy, sz = self.g92_shift
        return (wx + sx, wy + sy, wz + sz)

    def machine_pos(self) -> Tuple[float, float, float]:
        """工件坐标 -> 机床坐标（加 WCS/G92 偏置与刀长补偿）。"""
        ox, oy, oz = self.offset()
        length = 0.0
        if self.tool_length in ("G43", "G43.1") and self.tool in self.tool_offsets:
            length = self.tool_offsets[self.tool]
        return (self.pos[0] + ox, self.pos[1] + oy, self.pos[2] + oz + length)

    def snapshot(self) -> dict:
        return {
            "units": self.units,
            "distance": self.distance,
            "plane": f"G{self.plane}",
            "wcs": f"G{53 + self.wcs}" if self.wcs <= 6
            else f"G59.{self.wcs - 6}",
            "cutter_comp": self.cutter_comp,
            "tool_length": self.tool_length,
            "cycle": f"G{_fmt_g(self.cycle)}",
            "cycle_retract": self.cycle_retract,
            "feed_mode": self.feed_mode,
            "motion": self.motion_code,
            "feed": round(self.feed, 6),
            "spindle_speed": self.spindle_speed,
            "spindle": self.spindle_dir,
            "coolant": self.coolant,
            "tool": self.tool,
            "work_pos": [round(v, 6) for v in self.pos],
            "machine_pos": [round(v, 6) for v in self.machine_pos()],
        }


def _fmt_g(code: float) -> str:
    return str(int(code)) if float(code).is_integer() else str(code)


def group_of(code: float) -> str:
    return GROUPS.get(code, "")


def nearly(a: float, b: float, tol: float = 1e-5) -> bool:
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))
