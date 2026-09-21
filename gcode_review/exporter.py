"""导出：所有结果 = 原始字节 + 明确补丁；未改行逐字节一致。

补丁直接由 difflib 的非 equal 区块生成（而不是逐行配对），
因此“删除若干 A 行 + 插入若干 B 行”交错时锚点仍然唯一正确：
  对每个 replace/delete/insert hunk：
    span = A 上从首行到末行（含行终止符）的完整字节区间（insert 为零长度锚点）
    expected 必须等于该区间当前原始字节
    replacement = 该 hunk 涉及的 B 行（含行终止符）拼接
补丁从后向前应用；任何 expected 不匹配或区间重叠都拒绝导出。
"""
from __future__ import annotations

import base64
import difflib
from dataclasses import dataclass
from typing import List

from .compare import Comparison
from .lexer import Program


@dataclass
class Patch:
    start: int
    length: int
    expected: bytes
    replacement: bytes
    reason: str
    a_lines: object = None
    b_lines: object = None

    def to_dict(self) -> dict:
        return {
            "start": self.start,
            "length": self.length,
            "expected_b64": base64.b64encode(self.expected).decode(),
            "replacement_b64": base64.b64encode(self.replacement).decode(),
            "reason": self.reason,
            "a_lines": self.a_lines,
            "b_lines": self.b_lines,
        }


def _line_blob(prog: Program, i: int) -> bytes:
    ln = prog.lines[i]
    return ln.raw_bytes + ln.ending


def build_patches(cmp: Comparison) -> List[Patch]:
    a: Program = cmp.a.program
    b: Program = cmp.b.program
    a_texts = [ln.raw for ln in a.lines]
    b_texts = [ln.raw for ln in b.lines]
    sm = difflib.SequenceMatcher(a=a_texts, b=b_texts, autojunk=False)
    patches: List[Patch] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if i2 > i1:
            start_line = a.lines[i1]
            end_line = a.lines[i2 - 1]
            start = start_line.start
            length = end_line.end + len(end_line.ending) - start
            expected = a.text_bytes[start:start + length]
            a_range = [i1, i2 - 1]
        else:
            # 纯插入：锚点放在上一条 A 行终止符之后（文件首则 0）
            start = 0 if i1 == 0 else (a.lines[i1 - 1].end
                                       + len(a.lines[i1 - 1].ending))
            length = 0
            expected = b""
            a_range = None
        replacement = b"".join(_line_blob(b, j) for j in range(j1, j2))
        patches.append(Patch(
            start=start, length=length, expected=expected,
            replacement=replacement, reason=tag,
            a_lines=a_range, b_lines=[j1, j2 - 1] if j2 > j1 else None))
    patches.sort(key=lambda p: (p.start, -p.length))
    return patches


def apply_patches(original: bytes, patches: List[Patch]) -> bytes:
    out = bytearray()
    cursor = 0
    for p in sorted(patches, key=lambda x: (x.start, -x.length)):
        actual = original[p.start:p.start + p.length]
        if actual != p.expected:
            raise ValueError(
                f"patch anchor mismatch at {p.start}+{p.length}: "
                f"expected {p.expected!r}, found {actual!r}")
        if p.start < cursor:
            raise ValueError(f"overlapping patches at {p.start}")
        out += original[cursor:p.start]
        out += p.replacement
        cursor = p.start + p.length
    out += original[cursor:]
    return bytes(out)


def unchanged_regions_byte_identical(original: bytes, exported: bytes,
                                     cmp: Comparison) -> bool:
    """所有 equal 行原文（含终止符）必须原样出现在导出文本中。"""
    a: Program = cmp.a.program
    for e in cmp.entries:
        if e.op != "equal" or e.a_line is None:
            continue
        blob = _line_blob(a, e.a_line)
        if blob and blob not in exported:
            return False
    return True
