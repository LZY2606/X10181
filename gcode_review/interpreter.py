from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .models import Block, ParseIssue

AXES = ("X", "Y", "Z", "A", "B", "C", "I", "J", "K", "R", "F", "S", "P", "Q", "H", "D", "T", "L")

G_MODAL_GROUPS = {
    "motion": {"0", "1", "2", "3", "80", "81", "82", "83", "84", "85", "86", "87", "88", "89"},
    "plane": {"17", "18", "19"},
    "distance": {"90", "91"},
    "unit": {"20", "21"},
    "wcs": {str(i) for i in range(54, 60)},
    "cutter": {"40", "41", "42"},
    "length": {"43", "44", "49"},
    "cycle_z": {"98", "99"},
}
SUPPORTED_G = {
    "0", "1", "2", "3", "4", "10", "11", "17", "18", "19", "20", "21",
    "28", "40", "41", "42", "43", "44", "49", "52", "53", "54", "55",
    "56", "57", "58", "59", "80", "81", "82", "83", "84", "85", "86",
    "87", "88", "89", "90", "91", "98", "99",
}
SUPPORTED_M = {"0", "1", "2", "3", "4", "5", "6", "8", "9", "30"}
VENDOR_EFFECTS = {
    "none": None,
    "spindle_on_cw": "3",
    "spindle_on_ccw": "4",
    "spindle_off": "5",
    "coolant_on": "8",
    "coolant_off": "9",
}


@dataclass
class SafetyConfig:
    stock_min: tuple[float, float, float] = (-10.0, -10.0, -10.0)
    stock_max: tuple[float, float, float] = (110.0, 60.0, 20.0)
    safe_z: float = 5.0


def default_state() -> dict[str, Any]:
    return {
        "unit": "G21",
        "unit_scale": 1.0,
        "distance": "G90",
        "motion": "G1",
        "plane": "G17",
        "wcs": "G54",
        "retract": "G98",
        "cutter_comp": "G40",
        "tool_length": "G49",
        "spindle": "M5",
        "coolant": False,
        "feed": None,
        "spindle_speed": None,
        "tool": None,
        "offset_h": None,
        "offset_d": None,
        "position": {"X": 0.0, "Y": 0.0, "Z": 0.0, "A": 0.0, "B": 0.0, "C": 0.0},
        "wcs_offsets": {f"G{i}": {"X": 0.0, "Y": 0.0, "Z": 0.0} for i in range(54, 60)},
        "local_offset": {"X": 0.0, "Y": 0.0, "Z": 0.0},
        "cycle": None,
        "cycle_params": None,
    }


def code_number(word: Any) -> str:
    number = int(word.value)
    if not math.isclose(number, word.value, abs_tol=1e-9):
        return f"~{word.value:g}"
    return str(number)


def as_state(state: dict[str, Any]) -> dict[str, Any]:
    serializable = deepcopy(state)
    serializable.pop("cycle_params", None)
    return serializable


class InterpretationError(ValueError):
    pass


class Interpreter:
    def __init__(self, config: SafetyConfig | None = None, vendor_confirmed: dict[str, str] | None = None):
        self.config = config or SafetyConfig()
        self.vendor_confirmed = vendor_confirmed or {}

    def run(self, blocks: list[Block]) -> list[Block]:
        state = default_state()
        for block in blocks:
            block.pre_state = as_state(state)
            if block.skipped:
                block.state = as_state(state)
                block.semantic_events.append(("skip",))
                continue
            if block.parse_issues:
                block.unknown.append(block.raw)
                block.state = as_state(state)
                continue
            self._interpret_block(block, state)
            block.state = as_state(state)
        return blocks

    def _interpret_block(self, block: Block, state: dict[str, Any]) -> None:
        values = self._read_words(block, state)
        if block.has_error or (not values["G"] and not values["M"] and not values["addresses"]):
            return
        self._apply_setting_codes(block, values, state)
        if block.unknown:
            return
        self._apply_motion(block, values, state)

    def _read_words(self, block: Block, state: dict[str, Any]) -> dict[str, Any]:
        addresses: dict[str, float] = {}
        g_codes: list[str] = []
        m_codes: list[str] = []
        for word in block.words:
            if word.address == "N":
                continue
            if word.address == "G":
                code = code_number(word)
                if not code.isdigit() or code not in SUPPORTED_G:
                    key = f"G{code.lstrip('~')}"
                    if key in self.vendor_confirmed:
                        effect = VENDOR_EFFECTS.get(self.vendor_confirmed[key])
                        if effect and effect in SUPPORTED_G:
                            g_codes.append(effect)
                    else:
                        block.unknown.append(key)
                else:
                    g_codes.append(code)
            elif word.address == "M":
                code = code_number(word)
                key = f"M{code}"
                if not code.isdigit() or code not in SUPPORTED_M:
                    if key in self.vendor_confirmed:
                        effect = VENDOR_EFFECTS.get(self.vendor_confirmed[key])
                        if effect:
                            m_codes.append(effect)
                    else:
                        block.unknown.append(key)
                else:
                    m_codes.append(code)
            elif word.address in AXES:
                addresses[word.address] = word.value
            else:
                block.unknown.append(f"{word.address}{word.raw_value}")
        self._detect_duplicate_modal_groups(block, g_codes, m_codes)
        return {"addresses": addresses, "G": g_codes, "M": m_codes,
                "raw": {word.address: word for word in block.words}}

    def _detect_duplicate_modal_groups(self, block: Block, g_codes: list[str], m_codes: list[str]) -> None:
        for group, members in G_MODAL_GROUPS.items():
            found = [code for code in g_codes if code in members]
            if len(set(found)) > 1:
                block.parse_issues.append(ParseIssue(
                    f"同一行出现互斥的 {group} 指令: {found}", block.start, block.end
                ))
        if len(set(m_codes) & {"0", "1"}) > 1 or len(set(m_codes) & {"3", "4", "5"}) > 1:
            block.parse_issues.append(ParseIssue(
                "同一行出现互斥的 M 指令", block.start, block.end
            ))

    def _event(self, block: Block, state: dict[str, Any], group: str, code: str) -> None:
        if state.get(group) != code:
            block.semantic_events.append((group, code))
        state[group] = code

    def _apply_setting_codes(self, block: Block, values: dict[str, Any], state: dict[str, Any]) -> None:
        addresses = values["addresses"]
        for code in values["G"]:
            if code in {"20", "21"}:
                scale = 25.4 if code == "20" else 1.0
                state["unit_scale"] = scale
                self._event(block, state, "unit", f"G{code}")
            elif code in {"90", "91"}:
                self._event(block, state, "distance", f"G{code}")
            elif code in {"17", "18", "19"}:
                self._event(block, state, "plane", f"G{code}")
            elif code in {str(i) for i in range(54, 60)}:
                self._event(block, state, "wcs", f"G{code}")
            elif code in {"98", "99"}:
                self._event(block, state, "retract", f"G{code}")
            elif code in {"40", "41", "42"}:
                self._event(block, state, "cutter_comp", f"G{code}")
                if code == "40":
                    state["offset_d"] = None
                elif "D" in addresses:
                    state["offset_d"] = int(addresses["D"])
            elif code in {"43", "44", "49"}:
                self._event(block, state, "tool_length", f"G{code}")
                if code == "49":
                    state["offset_h"] = None
                elif "H" in addresses:
                    state["offset_h"] = int(addresses["H"])
            elif code == "4":
                if "P" not in addresses:
                    block.parse_issues.append(ParseIssue("G04 缺少 P 暂停时间", block.start, block.end))
                block.semantic_events.append(("dwell", round(addresses.get("P", 0.0) * state["unit_scale"], 9)))
            elif code == "10":
                self._apply_g10(block, values, state)
            elif code == "11":
                continue
            elif code == "52":
                axes = {axis: addresses[axis] for axis in ("X", "Y", "Z") if axis in addresses}
                if not axes:
                    state["local_offset"] = {"X": 0.0, "Y": 0.0, "Z": 0.0}
                    block.semantic_events.append(("local_offset", "clear"))
                else:
                    target = {"X": 0.0, "Y": 0.0, "Z": 0.0, **axes}
                    state["local_offset"] = {axis: target[axis] * state["unit_scale"] for axis in ("X", "Y", "Z")}
                    block.semantic_events.append(("local_offset", *tuple(round(state["local_offset"][a], 9) for a in "XYZ")))
            elif code in {"80", "81", "82", "83", "84", "85", "86", "87", "88", "89"}:
                if code == "80":
                    state["cycle"] = None
                    state["cycle_params"] = None
                    self._event(block, state, "motion", "G80")
                else:
                    state["cycle"] = f"G{code}"
                    state["motion"] = f"G{code}"
                    block.semantic_events.append(("cycle", f"G{code}"))
                    state["cycle_params"] = {
                        **(state["cycle_params"] or {}),
                        **self._cycle_params(addresses, state),
                    }

        for code in values["M"]:
            if code in {"0", "1"}:
                state["spindle"] = f"M{code}"
                block.semantic_events.append(("program_pause", f"M{code}"))
            elif code in {"3", "4", "5"}:
                state["spindle"] = f"M{code}"
                block.semantic_events.append(("spindle", f"M{code}"))
            elif code == "6":
                state["tool"] = int(addresses["T"]) if "T" in addresses else state.get("tool")
                block.semantic_events.append(("tool_change", state.get("tool")))
            elif code in {"8", "9"}:
                state["coolant"] = code == "8"
                block.semantic_events.append(("coolant", code == "8"))
            elif code == "30":
                state["cycle"] = None
                state["cycle_params"] = None
                block.semantic_events.append(("program_end",))

        if "F" in addresses:
            feed = addresses["F"] * state["unit_scale"]
            state["feed"] = feed
            block.semantic_events.append(("feed", round(feed, 9)))
        if "S" in addresses:
            speed = addresses["S"]
            state["spindle_speed"] = speed
            block.semantic_events.append(("spindle_speed", speed))
        if "T" in addresses and "6" not in values["M"]:
            state["tool"] = int(addresses["T"])
            block.semantic_events.append(("tool_select", state["tool"]))
        if "H" in addresses and state["tool_length"] in {"G43", "G44"}:
            state["offset_h"] = int(addresses["H"])
        if "D" in addresses and state["cutter_comp"] in {"G41", "G42"}:
            state["offset_d"] = int(addresses["D"])

    def _apply_g10(self, block: Block, values: dict[str, Any], state: dict[str, Any]) -> None:
        addresses = values["addresses"]
        if int(addresses.get("L", -1)) != 2 or "P" not in addresses:
            block.unknown.append("G10")
            return
        p = int(addresses["P"])
        if not 1 <= p <= 6:
            block.parse_issues.append(ParseIssue("G10 L2 的 P 必须在 1 到 6 之间", block.start, block.end))
            return
        wcs = f"G{53 + p}"
        current = state["wcs_offsets"][wcs]
        updated = {
            axis: (addresses[axis] * state["unit_scale"] if axis in addresses else current[axis])
            for axis in ("X", "Y", "Z")
        }
        state["wcs_offsets"][wcs] = updated
        block.semantic_events.append(("wcs_offset", wcs, *tuple(round(updated[a], 9) for a in "XYZ")))

    def _cycle_params(self, addresses: dict[str, float], state: dict[str, Any]) -> dict[str, float | None]:
        previous = state["cycle_params"] or {}

        def named(axis: str) -> float | None:
            if axis not in addresses:
                return previous.get(axis)
            value = addresses[axis] * state["unit_scale"]
            if state["distance"] == "G91":
                value += state["position"][axis]
            return value
        return {
            "R": named("R"),
            "Z": named("Z"),
            "F": addresses.get("F") * state["unit_scale"] if "F" in addresses else state["feed"],
            "P": addresses.get("P", previous.get("P")),
            "Q": addresses.get("Q") * state["unit_scale"] if "Q" in addresses else previous.get("Q"),
        }

    def _apply_motion(self, block: Block, values: dict[str, Any], state: dict[str, Any]) -> None:
        codes = values["G"]
        addresses = values["addresses"]
        explicit = {code for code in codes if code in {"0", "1", "2", "3", "53", "28"}}
        cycle_codes = {code for code in codes if 81 <= int(code) <= 89}
        if "28" in explicit:
            self._g28_motion(block, values, state)
            return
        if explicit:
            ordered_motion_codes = [
                code_number(word) for word in block.words
                if word.address == "G" and code_number(word) in {"0", "1", "2", "3"}
            ]
            code = ordered_motion_codes[-1] if ordered_motion_codes else sorted(explicit)[-1]
            if code in {"0", "1"}:
                self._linear_motion(
                    block, values, state,
                    rapid=code == "0" or ("53" in codes and any(code_number(word) == "53" for word in block.words if word.address == "G")),
                    raw="53" in codes,
                )
            else:
                self._arc_motion(block, values, state, clockwise=code == "2")
            if code in {"0", "1", "2", "3"}:
                state["motion"] = f"G{code}"
            return
        if cycle_codes or state["cycle"]:
            if any(axis in addresses for axis in ("X", "Y", "Z", "R")):
                self._fixed_cycle(block, values, state)
            return
        if state["motion"] in {"G0", "G1"} and any(axis in addresses for axis in ("X", "Y", "Z", "A", "B", "C")):
            self._linear_motion(block, values, state, rapid=state["motion"] == "G0")

    def _offset(self, state: dict[str, Any]) -> dict[str, float]:
        wcs = state["wcs_offsets"][state["wcs"]]
        local = state["local_offset"]
        return {axis: wcs[axis] + local[axis] for axis in ("X", "Y", "Z")}

    def _target(self, addresses: dict[str, float], state: dict[str, Any], raw: bool = False) -> dict[str, float]:
        target = dict(state["position"])
        for axis in ("X", "Y", "Z", "A", "B", "C"):
            if axis in addresses:
                value = addresses[axis] if raw else addresses[axis] * state["unit_scale"]
                if not raw and state["distance"] == "G91" and axis in ("X", "Y", "Z"):
                    value += state["position"][axis]
                target[axis] = value
        if not raw:
            shift = self._offset(state)
            for axis in ("X", "Y", "Z"):
                target[axis] += shift[axis]
        return target

    def _motion_base(self, block: Block, state: dict[str, Any], kind: str, start: dict[str, float], end: dict[str, float]) -> dict[str, Any]:
        index = len(block.motions)
        return {
            "id": f"{block.line_number}:{index}",
            "kind": kind,
            "line_number": block.line_number,
            "start": {axis: round(start[axis], 9) for axis in ("X", "Y", "Z")},
            "end": {axis: round(end[axis], 9) for axis in ("X", "Y", "Z")},
            "points": [],
            "unit": state["unit"],
            "plane": state["plane"],
            "wcs": state["wcs"],
            "distance": state["distance"],
            "feed": state["feed"],
            "cutter_comp": state["cutter_comp"],
            "tool_length": state["tool_length"],
            "cycle": state["cycle"],
        }

    def _add_motion(self, block: Block, motion: dict[str, Any], state: dict[str, Any]) -> None:
        for point in motion["points"]:
            state["position"].update({axis: point[axis] for axis in ("X", "Y", "Z")})
        if not motion["points"]:
            state["position"].update(motion["end"])
        motion["end"] = {axis: round(state["position"][axis], 9) for axis in ("X", "Y", "Z")}
        block.motions.append(motion)

    def _linear_motion(self, block: Block, values: dict[str, Any], state: dict[str, Any], rapid: bool, raw: bool = False) -> None:
        start = dict(state["position"])
        end = self._target(values["addresses"], state, raw=raw)
        motion = self._motion_base(block, state, "rapid" if rapid else "feed_linear", start, end)
        motion["points"] = [
            {axis: round(start[axis], 9) for axis in ("X", "Y", "Z")},
            {axis: round(end[axis], 9) for axis in ("X", "Y", "Z")},
        ]
        self._add_motion(block, motion, state)

    def _g28_motion(self, block: Block, values: dict[str, Any], state: dict[str, Any]) -> None:
        if any(axis in values["addresses"] for axis in ("X", "Y", "Z")):
            self._linear_motion(block, values, state, rapid=True)
        start = dict(state["position"])
        end = {axis: 0.0 for axis in ("X", "Y", "Z")}
        motion = self._motion_base(block, state, "rapid", start, end)
        motion["points"] = [
            {axis: round(start[axis], 9) for axis in ("X", "Y", "Z")},
            {axis: 0.0 for axis in ("X", "Y", "Z")},
        ]
        self._add_motion(block, motion, state)

    def _arc_motion(self, block: Block, values: dict[str, Any], state: dict[str, Any], clockwise: bool) -> None:
        addresses = values["addresses"]
        plane_config = {
            "G17": (("X", "Y"), ("I", "J"), "Z", -1.0),
            "G18": (("Z", "X"), ("K", "I"), "Y", 1.0),
            "G19": (("Y", "Z"), ("J", "K"), "X", 1.0),
        }[state["plane"]]
        axes, center_axes, helix_axis, plane_sign = plane_config
        start_program = self._program_position(state)
        end_program = {axis: start_program[axis] for axis in ("X", "Y", "Z")}
        for axis in ("X", "Y", "Z"):
            if axis in addresses:
                value = addresses[axis] * state["unit_scale"]
                if state["distance"] == "G91":
                    value += start_program[axis]
                end_program[axis] = value
        start = dict(state["position"])
        end = self._program_to_machine(end_program, state)
        u0, v0 = start_program[axes[0]], start_program[axes[1]]
        u1, v1 = end_program[axes[0]], end_program[axes[1]]
        if center_axes[0] in addresses or center_axes[1] in addresses:
            cu = u0 + addresses.get(center_axes[0], 0.0) * state["unit_scale"]
            cv = v0 + addresses.get(center_axes[1], 0.0) * state["unit_scale"]
        elif "R" in addresses:
            chord = math.hypot(u1 - u0, v1 - v0)
            signed_radius = addresses["R"] * state["unit_scale"]
            radius = abs(signed_radius)
            if chord <= 1e-12 or radius + 1e-9 < chord / 2:
                block.parse_issues.append(ParseIssue("圆弧半径无法通过端点", block.start, block.end))
                return
            height = math.sqrt(max(0.0, radius * radius - chord * chord / 4))
            mid_u, mid_v = (u0 + u1) / 2, (v0 + v1) / 2
            perpendicular_u, perpendicular_v = (v0 - v1) / chord, (u1 - u0) / chord
            sign = (-1 if clockwise else 1) * (-1 if signed_radius < 0 else 1)
            cu = mid_u + perpendicular_u * height * sign
            cv = mid_v + perpendicular_v * height * sign
        else:
            block.parse_issues.append(ParseIssue("圆弧缺少 I/J/K 或 R", block.start, block.end))
            return
        radius = math.hypot(u0 - cu, v0 - cv)
        if radius <= 1e-9:
            block.parse_issues.append(ParseIssue("圆弧半径为零", block.start, block.end))
            return
        full_circle = math.isclose(u0, u1, abs_tol=1e-8) and math.isclose(v0, v1, abs_tol=1e-8)
        angle = 0.0 if full_circle else math.atan2(v1 - cv, u1 - cu) - math.atan2(v0 - cv, u0 - cu)
        direction = (-1 if clockwise else 1) * plane_sign
        major_arc = "R" in addresses and addresses["R"] * state["unit_scale"] < 0
        if full_circle:
            sweep = direction * math.tau
        else:
            normalized = angle % math.tau
            if direction == -1:
                normalized = math.tau - normalized
            sweep = direction * (normalized + math.tau if major_arc else normalized)
        start_angle = math.atan2(v0 - cv, u0 - cu)
        steps = 32
        points = []
        h0 = start_program[helix_axis]
        h1 = end_program[helix_axis]
        for step in range(steps + 1):
            fraction = step / steps
            theta = start_angle + sweep * fraction
            program_point = dict(start_program)
            program_point[axes[0]] = cu + radius * math.cos(theta)
            program_point[axes[1]] = cv + radius * math.sin(theta)
            program_point[helix_axis] = h0 + (h1 - h0) * fraction
            points.append(self._program_to_machine(program_point, state))
        motion = self._motion_base(block, state, "feed_arc", start, end)
        motion["points"] = [{axis: round(point[axis], 9) for axis in ("X", "Y", "Z")} for point in points]
        motion["arc"] = {
            "plane": state["plane"],
            "clockwise": clockwise,
            "center_program": {"u": round(cu, 9), "v": round(cv, 9)},
            "radius": round(radius, 9),
        }
        self._add_motion(block, motion, state)

    def _program_position(self, state: dict[str, Any]) -> dict[str, float]:
        shift = self._offset(state)
        return {axis: state["position"][axis] - shift[axis] for axis in ("X", "Y", "Z")}

    def _program_to_machine(self, point: dict[str, float], state: dict[str, Any]) -> dict[str, float]:
        shift = self._offset(state)
        return {axis: point[axis] + shift[axis] for axis in ("X", "Y", "Z")}

    def _fixed_cycle(self, block: Block, values: dict[str, Any], state: dict[str, Any]) -> None:
        addresses = values["addresses"]
        cycle = state["cycle"]
        params = state["cycle_params"]
        if params is None:
            block.parse_issues.append(ParseIssue(f"{cycle} 缺少 R/Z 参数", block.start, block.end))
            return
        xy_target = dict(state["position"])
        for axis in ("X", "Y"):
            if axis in addresses:
                value = addresses[axis] * state["unit_scale"]
                if state["distance"] == "G91":
                    value += state["position"][axis]
                xy_target[axis] = value
        initial_z = state["position"]["Z"]
        r_plane = params["R"] if params["R"] is not None else initial_z
        z_target = params["Z"] if params["Z"] is not None else initial_z
        retract_z = initial_z if state["retract"] == "G98" else r_plane
        if "R" in addresses and state["distance"] == "G91":
            retract_z = initial_z if state["retract"] == "G98" else r_plane

        def emit(kind: str, start: dict[str, float], end: dict[str, float], tag: str, feed: float | None = None) -> None:
            motion = self._motion_base(block, state, kind, start, end)
            motion["cycle_phase"] = tag
            motion["points"] = [
                {axis: round(start[axis], 9) for axis in ("X", "Y", "Z")},
                {axis: round(end[axis], 9) for axis in ("X", "Y", "Z")},
            ]
            if feed is not None:
                motion["feed"] = feed
            self._add_motion(block, motion, state)

        xy_end = {**dict(state["position"]), "X": xy_target["X"], "Y": xy_target["Y"]}
        emit("rapid", dict(state["position"]), xy_end, "cycle_positioning")
        if abs(state["position"]["Z"] - r_plane) > 1e-12:
            emit("rapid", dict(state["position"]), {**dict(state["position"]), "Z": r_plane}, "cycle_rapid_r")
        feed = params["F"]
        if cycle in {"G83"} and params.get("Q"):
            current = state["position"]["Z"]
            depth_step = params["Q"]
            while current - depth_step > z_target:
                current -= depth_step
                emit("feed_linear", dict(state["position"]), {**dict(state["position"]), "Z": current}, "cycle_peck", feed)
                emit("rapid", dict(state["position"]), {**dict(state["position"]), "Z": r_plane}, "cycle_peck_retract")
                emit("rapid", dict(state["position"]), {**dict(state["position"]), "Z": current}, "cycle_peck_reentry")
        emit("feed_linear", dict(state["position"]), {**dict(state["position"]), "Z": z_target}, "cycle_cut", feed)
        if cycle == "G82" and params.get("P") is not None:
            block.semantic_events.append(("dwell", float(params["P"])))
        if cycle == "G84":
            block.semantic_events.append(("spindle", "M4"))
        if cycle in {"G85", "G89"}:
            emit("feed_linear", dict(state["position"]), {**dict(state["position"]), "Z": r_plane}, "cycle_feed_retract", feed)
        else:
            emit("rapid", dict(state["position"]), {**dict(state["position"]), "Z": retract_z}, "cycle_retract")
        if cycle == "G84":
            block.semantic_events.append(("spindle", "M3"))


def interpret(blocks: list[Block], config: SafetyConfig | None = None, vendor_confirmed: dict[str, str] | None = None) -> list[Block]:
    return Interpreter(config, vendor_confirmed).run(blocks)
