from gcode_review.compare import compare_programs
from gcode_review.interpreter import SafetyConfig, interpret
from gcode_review.patches import apply_patches, patches_from_text
from gcode_review.parser import parse_program
from gcode_review.safety import analyze_comparison


def run(text, **config_kwargs):
    config = SafetyConfig(**config_kwargs)
    return interpret(parse_program(text), config)


def test_absolute_and_incremental_reach_same_numeric_point():
    absolute = run("G21 G90\nG1 X10 Y20 Z5 F100\n")
    incremental = run("G21 G91\nG1 X10 Y20 Z5 F100\n")
    assert absolute[1].motions[0]["end"] == {"X": 10.0, "Y": 20.0, "Z": 5.0}
    assert incremental[1].motions[0]["end"] == {"X": 10.0, "Y": 20.0, "Z": 5.0}


def test_incremental_change_affects_following_motion():
    original = "G21 G90\nG1 X10 Y0 F100\nG1 X10 Y10\n"
    modified = "G21 G90\nG1 X0 Y0 F100\nG91\nG1 X10 Y10\n"
    result = compare_programs(run(original), run(modified))
    affected = {
        (item["side"], item["line"])
        for diff in result["differences"]
        for item in diff["affected_motions"]
    }
    assert ("modified", 4) in affected
    assert result["safe_no_change"] is False


def test_g17_g18_g19_arcs_are_interpreted_in_correct_planes():
    programs = [
        ("G17\nG2 X2 Y0 I1 J0", "X", "Y", 1.0, -1.0),
        ("G18\nG2 Z2 X0 K1 I0", "Z", "X", 1.0, 1.0),
        ("G19\nG2 Y2 Z0 J1 K0", "Y", "Z", 1.0, 1.0),
    ]
    for text, axis_u, axis_v, expected_u, expected_v in programs:
        blocks = run(text)
        arc = blocks[1].motions[0]
        midpoint = arc["points"][16]
        assert arc["arc"]["plane"] == blocks[1].state["plane"]
        assert midpoint[axis_u] == expected_u
        assert midpoint[axis_v] == expected_v


def test_radius_and_center_arc_forms_are_geometrically_equivalent():
    radius = run("G21 G90 G17\nG1 X0 Y0 F100\nG3 X2 Y0 R1\n")
    center = run("G21 G90 G17\nG1 X0 Y0 F100\nG3 X2 Y0 I1 J0\n")
    result = compare_programs(radius, center)
    assert result["summary"]["equivalent_geometry"] == 1
    assert result["safe_no_change"] is True
    assert radius[2].motions[0]["points"][16]["Y"] == center[2].motions[0]["points"][16]["Y"]


def test_negative_r_selects_major_arc_path():
    minor = run("G21 G90 G17\nG1 X0 Y0\nG2 X2 Y0 R1\n")
    major = run("G21 G90 G17\nG1 X0 Y0\nG2 X2 Y0 R-1\n")
    assert minor[2].motions[0]["points"][16]["Y"] == -1.0
    assert major[2].motions[0]["points"][16]["Y"] == 1.0


def test_work_coordinate_offsets_shift_machine_path_only():
    blocks = run("G21 G90 G54\nG10 L2 P1 X5 Y7 Z2\nG1 X1 Y1 Z1 F100\n")
    motion = blocks[2].motions[0]
    assert motion["end"] == {"X": 6.0, "Y": 8.0, "Z": 3.0}
    assert blocks[2].state["wcs_offsets"]["G54"] == {"X": 5.0, "Y": 7.0, "Z": 2.0}


def test_cutter_and_length_compensation_flags_and_unrestored_findings():
    blocks = run("G21 G90\nG41 D2\nG43 H1\nM30\n")
    findings = analyze_comparison(blocks, blocks)
    codes = {f.code for f in findings}
    assert "cutter_comp_not_restored" in codes
    assert "tool_length_not_restored" in codes


def test_fixed_cycle_and_cancellation():
    config = SafetyConfig((-100, -100, 20), (100, 100, 100), 50)
    blocks = run("G21 G90 G54\nG0 X0 Y0 Z10\nG81 R2 Z-5 F100\nX10 Y10\nG80\nM30\n",
                 stock_min=(-100, -100, 20), stock_max=(100, 100, 100), safe_z=50)
    phases = [motion["cycle_phase"] for block in blocks[2:5] for motion in block.motions]
    assert phases.count("cycle_cut") == 2
    assert blocks[4].state["cycle"] is None
    assert not analyze_comparison(
        blocks, blocks, config
    )


def test_unknown_vendor_instruction_blocks_safe_conclusion_and_preserves_raw():
    original = run("M3 S100\n")
    modified = run("M3 S100\nO999 CUSTOM PROBE\n")
    assert modified[1].raw == "O999 CUSTOM PROBE"
    assert modified[1].unknown
    result = compare_programs(original, modified)
    assert result["safe_no_change"] is False
    assert any(item.kind == "unknown" for item in [type("D", (), {"kind": d["kind"]}) for d in result["differences"]])
    assert any(f.code == "unknown_instruction" for f in analyze_comparison(original, modified))


def test_confirmed_vendor_semantics_can_become_known():
    blocks = interpret(parse_program("M999\n"), vendor_confirmed={"M999": "spindle_on_cw"})
    assert not blocks[0].unknown
    assert blocks[0].state["spindle"] == "M3"


def test_rapid_through_stock_is_numeric_safety_finding():
    blocks = run(
        "G21 G90\nG0 X0 Y0 Z20\nG0 X50 Y25 Z0\n",
        stock_min=(0, 0, -1), stock_max=(100, 50, 10),
    )
    findings = analyze_comparison(blocks, blocks, SafetyConfig((0, 0, -1), (100, 50, 10), 5))
    assert any(f.code == "rapid_through_stock" and f.line_number == 3 for f in findings)


def test_missing_unit_switch_is_reported():
    inch = run("G20 G90\nG1 X1 F10\n")
    mm = run("G21 G90\nG1 X1 F10\n")
    findings = analyze_comparison(inch, mm)
    assert any(f.code == "unit_switch_missing" for f in findings)


def test_format_only_difference_is_safe():
    blocks_a = run("G21 G90 G17\nG1 X10 F100\n")
    blocks_b = run("(comment)\nG21 G90 G17\n  g1 x10 f100\n")
    result = compare_programs(blocks_a, blocks_b)
    assert result["summary"]["format"] >= 1
    assert result["safe_no_change"] is True


def test_patch_roundtrip_keeps_unmodified_bytes_identical():
    original = "G21 G90\nG1 X10 F100\n中文注释\nM30\n"
    modified = "G21 G90\nG1 X11 F100\n中文注释\nM30\n"
    patches = patches_from_text(original, modified)
    assert len(patches) == 1
    assert apply_patches(original, patches) == modified
    rebuilt = bytearray(original.encode())
    for patch in patches:
        rebuilt[patch.start:patch.end] = patch.replacement.encode()
    assert rebuilt == modified.encode()


def test_parser_preserves_line_and_byte_positions():
    text = "中文 G1 X1\r\nG2 X2\nG3"
    blocks = parse_program(text)
    assert [block.text for block in blocks] == ["中文 G1 X1\r\n", "G2 X2\n", "G3"]
    assert blocks[1].start == len("中文 G1 X1\r\n".encode("utf-8"))
    assert blocks[2].end == len(text.encode("utf-8"))
    assert blocks[0].words[0].start == len("中文 ".encode("utf-8"))


def test_inch_unit_conversion_uses_numeric_machine_millimeters():
    blocks = run("G20 G90\nG1 X1 F10\n")
    assert blocks[1].motions[0]["end"]["X"] == 25.4


def test_comments_preserve_lexical_byte_offsets():
    blocks = parse_program("G1 (long comment) X7\n")
    x_word = next(word for word in blocks[0].words if word.address == "X")
    assert blocks[0].raw.encode("utf-8")[x_word.start:x_word.end] == b"X7"
