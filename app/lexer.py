"""G-code 词法分析：保留每行原文与字节位置。

文件按 latin-1 解码（1 字节 1 字符），因此字符偏移即字节偏移，
后续“从原始文本应用补丁”可以严格保持未改行字节一致。
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import re

_WORD_RE = re.compile(r"([A-Za-z])\s*([+-]?[0-9]*\.?[0-9]*)")
_TOKEN_RE = re.compile(r"""
    \s+                                |  # 空白
    ;[^\n]*                            |  # 行注释
    \([^)\n]*\)                        |  # 括号注释
    ([A-Za-z]\s*[+-]?[0-9]*\.?[0-9]*)  |  # 词
    .                                     # 其他（错误字符）
""", re.VERBOSE)


@dataclass
class Word:
    letter: str
    value_text: str
    number: Optional[float]
    byte_offset: int


@dataclass
class Line:
    index: int
    raw: str                 # 不含行终止符的原文
    start: int               # 行首字节偏移
    end: int                 # 行内容末字节偏移（不含换行）
    newline: str             # 行终止符（含末行可能为 ""）
    words: List[Word] = field(default_factory=list)
    comments: List[Tuple[int, str]] = field(default_factory=list)
    has_macro: bool = False
    errors: List[str] = field(default_factory=list)

    def word_map(self) -> Dict[str, Word]:
        out: Dict[str, Word] = {}
        for w in self.words:
            out[w.letter] = w
        return out


def tokenize(text: bytes) -> List[Line]:
    if isinstance(text, str):
        text = text.encode("latin-1", errors="replace")
    src = text.decode("latin-1")

    lines: List[Line] = []
    pos = 0
    for index, chunk in enumerate(src.split("\n")):
        raw = chunk[:-1] if chunk.endswith("\r") else chunk
        nl = "\r\n" if chunk.endswith("\r") else ("\n" if pos + len(chunk) < len(src) else "")
        line = Line(index=index, raw=raw, start=pos, end=pos + len(raw), newline=nl)
        _scan(line, raw, pos)
        lines.append(line)
        pos += len(chunk) + 1  # 含 \n
    if src.endswith("\n") and lines and lines[-1].raw == "":
        lines.pop()
    return lines


def _scan(line: Line, raw: str, base: int) -> None:
    for m in _TOKEN_RE.finditer(raw):
        tok = m.group(0)
        off = base + m.start()
        if tok[0] in " \t\r" or tok.startswith(";") or tok.startswith("("):
            if tok.startswith(";") or tok.startswith("("):
                line.comments.append((off, tok))
            continue
        wm = _WORD_RE.fullmatch(tok)
        if wm and wm.group(2) != "":
            letter = wm.group(1).upper()
            text_val = wm.group(2)
            try:
                num = float(text_val)
            except ValueError:
                num = None
                line.errors.append("非法数字 %s" % tok)
            line.words.append(Word(letter, text_val, num, off))
        else:
            line.errors.append("无法识别字符 %r" % tok)
    if "#" in raw:
        line.has_macro = True
