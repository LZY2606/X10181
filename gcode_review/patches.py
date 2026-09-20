from __future__ import annotations

import difflib
from dataclasses import dataclass


@dataclass(frozen=True)
class TextPatch:
    start: int
    end: int
    replacement: str
    original_line: int
    modified_line: int


def patches_from_text(original: str, modified: str) -> list[TextPatch]:
    original_lines = original.splitlines(keepends=True)
    modified_lines = modified.splitlines(keepends=True)
    patches: list[TextPatch] = []
    original_offset = [0]
    for line in original_lines:
        original_offset.append(original_offset[-1] + len(line.encode("utf-8")))
    matcher = difflib.SequenceMatcher(None, original_lines, modified_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        start = original_offset[i1] if i1 < len(original_offset) else len(original)
        end = original_offset[i2] if i2 < len(original_offset) else len(original)
        patches.append(TextPatch(start, end, "".join(modified_lines[j1:j2]), i1 + 1, j1 + 1))
    return patches

def apply_patches(original: str, patches: list[TextPatch]) -> str:
    result = bytearray(original.encode("utf-8"))
    for patch in sorted(patches, key=lambda item: item.start, reverse=True):
        result[patch.start:patch.end] = patch.replacement.encode("utf-8")
    return result.decode("utf-8")


def unified_diff(original: str, modified: str) -> str:
    return "".join(difflib.unified_diff(
        original.splitlines(keepends=True),
        modified.splitlines(keepends=True),
        fromfile="original.gcode",
        tofile="modified.gcode",
    ))


def patch_payload(original: str, modified: str) -> dict[str, object]:
    patches = patches_from_text(original, modified)
    return {
        "format": "byte-offset-patch/1",
        "patches": [
            {"start": patch.start, "end": patch.end, "replacement": patch.replacement,
             "original_line": patch.original_line, "modified_line": patch.modified_line}
            for patch in patches
        ],
        "roundtrip": apply_patches(original, patches) == modified,
    }
