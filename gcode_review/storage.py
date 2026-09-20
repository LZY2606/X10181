from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class Store:
    def __init__(self, path: str | Path = "review.db"):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS programs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                side TEXT NOT NULL,
                name TEXT NOT NULL,
                content TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS vendor_confirmations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instruction TEXT NOT NULL,
                machine_model TEXT NOT NULL,
                semantics_version TEXT NOT NULL,
                effect TEXT NOT NULL CHECK(effect IN ('none', 'spindle_on_cw', 'spindle_on_ccw', 'spindle_off', 'coolant_on', 'coolant_off')),
                confirmed_by TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE(instruction, machine_model, semantics_version)
            );
            """
        )
        self.conn.commit()

    def save_program(self, side: str, name: str, content: str, digest: str) -> int:
        cursor = self.conn.execute(
            "INSERT INTO programs(side, name, content, sha256, created_at) VALUES (?, ?, ?, ?, ?)",
            (side, name, content, digest, datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def confirm_vendor(
        self, instruction: str, machine_model: str, semantics_version: str,
        effect: str, confirmed_by: str, note: str,
    ) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO vendor_confirmations(
                instruction, machine_model, semantics_version, effect, confirmed_by, note, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(instruction, machine_model, semantics_version) DO UPDATE SET
                effect=excluded.effect, confirmed_by=excluded.confirmed_by,
                note=excluded.note, created_at=excluded.created_at
            """,
            (instruction, machine_model, semantics_version, effect, confirmed_by, note,
             datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def vendor_map(self, machine_model: str) -> dict[str, str]:
        rows = self.conn.execute(
            "SELECT instruction, effect FROM vendor_confirmations WHERE machine_model = ?",
            (machine_model,),
        ).fetchall()
        return {row["instruction"]: row["effect"] for row in rows}

    def list_confirmations(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM vendor_confirmations ORDER BY created_at DESC"
        ).fetchall()
        return [dict(row) for row in rows]
