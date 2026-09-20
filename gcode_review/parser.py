from __future__ import annotations

import re

from .models import Block, ParseIssue, Word

COMMENT_RE = re.compile(r"\((?:[^()]|\([^()]*\))*\)|;.*")
WORD_RE = re.compile(
    r"(?P<addr>[A-Za-z])\s*(?P<value>[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
)


def _split_keep_endings(text: str) -> list[str]:
    lines: list[str] = []
    start = 0
    for match in re.finditer(r"\r\n|[\r\n]", text):
        lines.append(text[start : match.end()])
        start = match.end()
    if start < len(text) or lines:
        lines.append(text[start:])
    if not lines:
        return []
    if lines[-1] == "" and text.endswith(("\r", "\n")):
        return lines[:-1]
    return lines


def parse_program(text: str) -> list[Block]:
    blocks: list[Block] = []
    offset = 0
    byte_offset = 0
    for index, line in enumerate(_split_keep_endings(text), start=1):
        line_end_match = re.search(r"(?:\r\n|[\r\n])$", line)
        content_end = offset + (line_end_match.start() if line_end_match else len(line))
        block = Block(
            line_number=index,
            text=line,
            raw=line[: (line_end_match.start() if line_end_match else len(line))],
            start=byte_offset,
            end=byte_offset + len(line[: (line_end_match.start() if line_end_match else len(line))].encode("utf-8")),
            line_end=byte_offset + len(line.encode("utf-8")),
        )

        content = line[: (line_end_match.start() if line_end_match else len(line))]
        stripped = content.lstrip()
        block.skipped = stripped.startswith("/")
        active_start = 1 if block.skipped else 0
        code_part = stripped[active_start:]
        line_prefix = content[: len(content) - len(stripped)] + ("/" if block.skipped else "")
        prefix_bytes = len(line_prefix.encode("utf-8"))

        for comment in COMMENT_RE.finditer(code_part):
            value = comment.group(0)
            block.comments.append(value[1:-1] if value.startswith("(") else value[1:])

        without_comments = COMMENT_RE.sub(lambda match: " " * len(match.group(0)), code_part)
        scanned = 0
        for match in WORD_RE.finditer(without_comments):
            gap = without_comments[scanned : match.start()].strip()
            if gap:
                block.parse_issues.append(
                    ParseIssue(
                        f"无法识别的词法内容: {gap!r}",
                        byte_offset + prefix_bytes + len(code_part[:scanned].encode("utf-8")),
                        byte_offset + prefix_bytes + len(code_part[:match.start()].encode("utf-8")),
                    )
                )
            try:
                numeric_value = float(match.group("value"))
            except ValueError:
                block.parse_issues.append(
                    ParseIssue(
                        "数值格式无效",
                        byte_offset + prefix_bytes + len(code_part[:match.start()].encode("utf-8")),
                        byte_offset + prefix_bytes + len(code_part[:match.end()].encode("utf-8")),
                    )
                )
                continue
            block.words.append(
                Word(
                    address=match.group("addr").upper(),
                    value=numeric_value,
                    raw_value=match.group("value"),
                    start=byte_offset + prefix_bytes + len(code_part[:match.start()].encode("utf-8")),
                    end=byte_offset + prefix_bytes + len(code_part[:match.end()].encode("utf-8")),
                )
            )
            scanned = match.end()
        tail = without_comments[scanned:].strip()
        if tail:
            block.parse_issues.append(
                ParseIssue(
                    f"无法识别的词法内容: {tail!r}",
                    byte_offset + prefix_bytes + len(code_part[:scanned].encode("utf-8")),
                    byte_offset + prefix_bytes + len(code_part.encode("utf-8")),
                )
            )

        blocks.append(block)
        offset += len(line)
        byte_offset += len(line.encode("utf-8"))
    return blocks
