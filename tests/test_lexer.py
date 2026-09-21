from gcode_review.lexer import parse_program


def test_byte_positions_preserved():
    data = b"G01 X1.0\nG2 x3\r\nG90\r\n"
    p = parse_program(data)
    assert [l.start for l in p.lines] == [0, 9, 16]
    assert [l.end for l in p.lines] == [8, 14, 19]
    assert [l.ending for l in p.lines] == [b"\n", b"\r\n", b"\r\n"]
    # 重组必须逐字节等于原文
    rebuilt = b"".join(l.raw_bytes + l.ending for l in p.lines)
    assert rebuilt == data


def test_word_and_comment_lexing():
    p = parse_program(b"g00 X+1. y-.5 (note) ; tail\n")
    line = p.lines[0]
    assert [(w.letter, w.raw) for w in line.words] == [
        ("G", "00"), ("X", "+1."), ("Y", "-.5")]
    assert line.comments == ["note", " tail"]


def test_canonical_form_detects_format_only():
    a = parse_program(b"G00 X01.0 Y2\n").lines[0]
    b = parse_program(b"g0 x1 y2 (c)\n").lines[0]
    assert a.canonical_text() == b.canonical_text()


def test_vendor_fragment_kept_raw():
    # 宏表达式等非标准片段保留原文，不丢弃
    p = parse_program(b"G1 X[#100+1]\n")
    assert p.lines[0].vendor_fragments == ["[#100+1]"]
