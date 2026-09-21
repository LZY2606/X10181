"""G-code 词法分析：逐行保留原文、字节起止位置与词元。

设计约束：
- 所有偏移量均为相对程序原文的字节偏移，导出补丁时依赖它保证未改行字节一致；
- 词法层不判断 G/M 码语义（只识别地址字母 + 数值），语义交给 interpreter；
- 注释与括号注释原样保留；无法归类的非空片段记入 vendor 并保留原文。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

_LINE_SPLIT = re.compile(rb"\r\n|\r|\n")
# 地址词：字母 + 可选符号 + 数字（允许小数点）。
_WORD_RE = re.compile(rb"([A-Za-z])([+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+))")
_PAREN_RE = re.compile(rb"[()]")


@dataclass
class Word:
    letter: str          # 大写地址字母，如 "G" "X"
    raw: str             # 数字原文，如 "01"
    value: float         # 解析值
    span: Tuple[int, int]  # 相对该行的字节区间（含字母）

    def canonical_number(self) -> str:
        """数值的规范写法：去前导零、去尾零，用于区分纯格式变化。"""
        text = self.raw.lstrip("+")
        neg = text.startswith("-")
        if neg:
            text = text[1:]
        if "." in text:
            whole, frac = text.split(".", 1)
            text = (whole.lstrip("0") or "0") + "." + frac.rstrip("0")
            if text.endswith("."):
                text = text[:-1]
        else:
            text = text.lstrip("0") or "0"
        if neg and float(text) != 0.0:
            text = "-" + text
        return text

    def canonical(self) -> str:
        return f"{self.letter}{self.canonical_number()}"


@dataclass
class Line:
    index: int                 # 行号（0 起）
    raw: str                   # 原文（不含行终止符），UTF-8 替换解码
    raw_bytes: bytes           # 原始字节（不含行终止符）
    start: int                 # 相对程序的起始字节偏移
    end: int                   # 行末（终止符前）字节偏移
    ending: bytes              # 行终止符原文
    words: List[Word] = field(default_factory=list)
    comments: List[str] = field(default_factory=list)
    vendor_fragments: List[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return self.raw

    def word_values(self, letter: str) -> List[Tuple[float, Word]]:
        return [(w.value, w) for w in self.words if w.letter == letter]

    def last(self, letter: str) -> Optional[Word]:
        found = [w for w in self.words if w.letter == letter]
        return found[-1] if found else None

    def canonical_text(self) -> str:
        """规范化文本：同序词元的规范形式 + 去除注释/空白。

        两行 canonical_text 相同而原文不同，即纯格式变化（空格/前导零/注释）。
        """
        return " ".join(w.canonical() for w in self.words)


@dataclass
class Program:
    text_bytes: bytes
    lines: List[Line]
    ending: bytes  # 文件末尾终止符（若有）

    def raw_text(self) -> str:
        return self.text_bytes.decode("utf-8", errors="replace")


def _parse_line(index: int, raw: bytes, start: int, ending: bytes) -> Line:
    end = start + len(raw)
    line = Line(
        index=index,
        raw=raw.decode("utf-8", errors="replace"),
        raw_bytes=raw,
        start=start,
        end=end,
        ending=ending,
    )
    pos = 0
    n = len(raw)
    while pos < n:
        ch = raw[pos:pos + 1]
        if ch in (b" ", b"\t", b","):
            pos += 1
            continue
        if ch == b";":
            line.comments.append(raw[pos + 1:].decode("utf-8", errors="replace"))
            break
        if ch == b"(":
            close = raw.find(b")", pos + 1)
            stop = n if close < 0 else close
            line.comments.append(raw[pos + 1:stop].decode("utf-8", errors="replace"))
            pos = n if close < 0 else close + 1
            continue
        # 地址词后紧跟 '['（宏表达式参数，如 X[#100+1]）：地址字母跳过，
        # 括号表达式作为厂商片段原样保留（解释器不会把它当坐标运动）
        if (ch.isalpha() and pos + 1 < n and raw[pos + 1:pos + 2] == b"["):
            close = raw.find(b"]", pos + 2)
            stop = n if close < 0 else close + 1
            line.vendor_fragments.append(
                raw[pos + 1:stop].decode("utf-8", errors="replace"))
            pos = stop
            continue
        m = _WORD_RE.match(raw, pos)
        if m:
            letter = m.group(1).decode("ascii").upper()
            number_raw = m.group(2).decode("ascii")
            try:
                value = float(number_raw)
            except ValueError:
                line.vendor_fragments.append(
                    raw[m.start():m.end()].decode("utf-8", errors="replace"))
                pos = m.end()
                continue
            line.words.append(Word(
                letter=letter,
                raw=number_raw,
                value=value,
                span=(m.start(), m.end()),
            ))
            pos = m.end()
            continue
        # 不是空白/注释/合法词元：可能是宏表达式、厂商扩展（#、/ 跳段标记除外）。
        if ch == b"/" and (pos == 0 or raw[pos - 1:pos] in (b" ", b"\t")):
            line.vendor_fragments.append("/")  # 块删除标记，视为需人工确认的开关
            pos += 1
            continue
        nxt = pos + 1
        while nxt < n and raw[nxt:nxt + 1] not in (b" ", b"\t", b",", b";", b"("):
            nxt += 1
        line.vendor_fragments.append(
            raw[pos:nxt].decode("utf-8", errors="replace"))
        pos = nxt
    return line


def parse_program(data: bytes) -> Program:
    """把完整程序字节解析为带字节位置的行序列。"""
    if isinstance(data, str):
        data = data.encode("utf-8")
    lines: List[Line] = []
    pos = 0
    idx = 0
    while True:
        m = _LINE_SPLIT.search(data, pos)
        if m is None:
            lines.append(_parse_line(idx, data[pos:], pos, b""))
            break
        lines.append(_parse_line(idx, data[pos:m.start()], pos, m.group(0)))
        idx += 1
        pos = m.end()
        if pos == len(data):
            break
    return Program(text_bytes=data, lines=lines, ending=data[-1:] if data[-1:] in (b"\n", b"\r") else b"")
