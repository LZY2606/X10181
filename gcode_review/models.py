from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class Word:
    address: str
    value: float
    raw_value: str
    start: int
    end: int

    def canonical(self) -> tuple[str, float]:
        if self.address in {"G", "M", "P", "L", "H", "D", "T"}:
            return (self.address, int(self.value))
        return (self.address, self.value)


@dataclass(frozen=True)
class ParseIssue:
    message: str
    start: int
    end: int


@dataclass
class Block:
    line_number: int
    text: str
    raw: str
    start: int
    end: int
    line_end: int
    words: list[Word] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)
    skipped: bool = False
    unknown: list[str] = field(default_factory=list)
    parse_issues: list[ParseIssue] = field(default_factory=list)
    pre_state: dict[str, Any] | None = None
    state: dict[str, Any] | None = None
    motions: list[dict[str, Any]] = field(default_factory=list)
    semantic_events: list[tuple[str, ...]] = field(default_factory=list)

    @property
    def has_error(self) -> bool:
        return bool(self.unknown or self.parse_issues)


IssueSeverity = Literal["danger", "warning", "info"]


@dataclass(frozen=True)
class SafetyFinding:
    code: str
    severity: IssueSeverity
    message: str
    side: str
    line_number: int | None = None
    motion_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "side": self.side,
            "line_number": self.line_number,
            "motion_id": self.motion_id,
        }
