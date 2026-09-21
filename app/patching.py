"""从原始文本应用明确补丁，未修改行严格保持字节一致。

补丁操作（均为显式操作，不做模糊匹配）：
  {"op": "replace", "line": i, "text": "..."}
  {"op": "insert",  "after": i, "text": "..."}   # i 为 -1 表示插到开头
  {"op": "delete",  "line": i}
text 为不含换行的新行内容；换行符沿用被改行原有的行终止符，
插入行使用文件中最常见的行终止符。
"""
from dataclasses import dataclass
from typing import Dict, List, Optional

from .lexer import tokenize


@dataclass
class PatchError(Exception):
    message: str


def default_newline(text: bytes) -> str:
    crlf = text.count(b"\r\n")
    lf = text.count(b"\n") - crlf
    return "\r\n" if crlf > lf else "\n"


def _line_spans(text: bytes):
    lines = tokenize(text)
    return [(ln.start, ln.end, ln.newline) for ln in lines]


def apply_patch(text: bytes, ops: List[dict]) -> bytes:
    if isinstance(text, str):
        text = text.encode("latin-1")
    spans = _line_spans(text)
    n = len(spans)
    nl_default = default_newline(text)

    # 规范化与校验
    norm: List[dict] = []
    seen_targets = set()
    for op in ops:
        kind = op.get("op")
        if kind == "replace":
            i = int(op["line"])
            if not 0 <= i < n:
                raise PatchError("replace 目标行 %d 越界（共 %d 行）" % (i, n))
            if i in seen_targets:
                raise PatchError("第 %d 行存在多个补丁操作" % i)
            seen_targets.add(i)
            norm.append({"op": kind, "line": i,
                         "text": str(op.get("text", ""))})
        elif kind == "delete":
            i = int(op["line"])
            if not 0 <= i < n:
                raise PatchError("delete 目标行 %d 越界（共 %d 行）" % (i, n))
            if i in seen_targets:
                raise PatchError("第 %d 行存在多个补丁操作" % i)
            seen_targets.add(i)
            norm.append({"op": kind, "line": i})
        elif kind == "insert":
            after = int(op["after"])
            if not -1 <= after < n:
                raise PatchError("insert 锚点 %d 越界（共 %d 行）" % (after, n))
            norm.append({"op": kind, "after": after,
                         "text": str(op.get("text", ""))})
        else:
            raise PatchError("未知补丁操作 %r" % kind)

    deletes = {o["line"] for o in norm if o["op"] == "delete"}
    replaces = {o["line"]: o for o in norm if o["op"] == "replace"}
    inserts: Dict[int, List[str]] = {}
    for o in norm:
        if o["op"] == "insert":
            inserts.setdefault(o["after"], []).append(o["text"])

    out = bytearray()
    # 开头插入
    for t in inserts.get(-1, []):
        out.extend(t.encode("latin-1"))
        out.extend(nl_default.encode("latin-1"))

    for i, (start, end, newline) in enumerate(spans):
        if i in deletes:
            continue
        if i in replaces:
            out.extend(replaces[i]["text"].encode("latin-1"))
            out.extend(newline.encode("latin-1") if newline else b"")
        else:
            # 未改行：原样拷贝（严格字节一致，含任何空白/注释/行终止符）
            out.extend(text[start:end])
            out.extend(newline.encode("latin-1"))
        for t in inserts.get(i, []):
            out.extend(t.encode("latin-1"))
            # 插到最后一行之后且文件无尾换行时，仍给新行换行
            out.extend((newline or nl_default).encode("latin-1"))

    # 若最后一行原本无换行，但在其后面有插入，保持新行完整
    return bytes(out)


def build_patch(alignment: List[dict], b_lines: List,
                text_a: bytes) -> List[dict]:
    """根据对齐结果，从修改版文本生成相对原版的显式补丁。

    b_lines: 修改版解释器 LineResult 列表（提供 raw）。
    """
    ops: List[dict] = []
    a_to_b = {item["a"]: item["b"] for item in alignment
              if item["a"] is not None and item["b"] is not None}
    deletes = [item["a"] for item in alignment if item["kind"] == "delete"]
    changed_pairs = [(item["a"], item["b"]) for item in alignment
                     if item["kind"] in ("changed", "insert", "format", "restate")
                     and item["a"] is not None and item["b"] is not None]

    # 替换（含格式/等价重申，保证文本与修改版一致）
    for ia, ib in changed_pairs:
        ops.append({"op": "replace", "line": ia,
                    "text": b_lines[ib].raw})
    for ia in deletes:
        ops.append({"op": "delete", "line": ia})

    # 插入：按原版锚点聚合（LCS 中该插入区域前一个 a 行）
    insert_items = [item for item in alignment if item["kind"] == "insert"]
    # 区域贪心配对已经把部分插入配成 changed，这里处理纯新增
    anchor_map: Dict[int, List[str]] = {}
    last_a = -1
    for item in alignment:
        if item["a"] is not None:
            last_a = item["a"]
        if item["kind"] == "insert" and item["b"] is not None:
            anchor_map.setdefault(last_a, []).append(b_lines[item["b"]].raw)
    for after, texts in anchor_map.items():
        for t in texts:
            ops.append({"op": "insert", "after": after, "text": t})
    ops.sort(key=lambda o: (o.get("line", o.get("after", -1)),
                            0 if o["op"] == "insert" else 1))
    return ops
