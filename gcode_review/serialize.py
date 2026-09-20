from __future__ import annotations

from typing import Any

from .compare import state_delta
from .models import Block


def serialize_block(block: Block, side: str) -> dict[str, Any]:
    return {
        "side": side,
        "line_number": block.line_number,
        "text": block.text,
        "raw": block.raw,
        "start": block.start,
        "end": block.end,
        "line_end": block.line_end,
        "words": [
            {"address": word.address, "value": word.value, "raw": word.raw_value,
             "start": word.start, "end": word.end}
            for word in block.words
        ],
        "comments": block.comments,
        "skipped": block.skipped,
        "unknown": block.unknown,
        "issues": [issue.message for issue in block.parse_issues],
        "motions": block.motions,
        "events": [list(event) for event in block.semantic_events],
        "state_before": block.pre_state,
        "state_after": block.state,
        "state_delta": state_delta(block.pre_state, block.state),
    }


def serialize_side(blocks: list[Block], side: str) -> dict[str, Any]:
    return {
        "side": side,
        "lines": [serialize_block(block, side) for block in blocks],
        "motions": [motion for block in blocks for motion in block.motions],
    }
