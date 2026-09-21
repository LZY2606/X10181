"""SQLite 存储：导入程序、比较任务与人工确认记录。

人工确认必须带版本与适用机型；同一代码在不同版本/机型下分别生效。
"""
import json
import os
import sqlite3
import time
from typing import Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS programs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    machine TEXT NOT NULL DEFAULT '',
    content BLOB NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS comparisons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    program_a INTEGER NOT NULL,
    program_b INTEGER NOT NULL,
    machine TEXT NOT NULL DEFAULT '',
    stock_json TEXT NOT NULL,
    report_json TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS confirmations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    effect TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    version TEXT NOT NULL,
    machine TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE(code, version, machine)
);
"""


class Database:
    def __init__(self, path: str):
        self.path = path
        first = not os.path.exists(path)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        if first:
            self.conn.executescript(SCHEMA)
            self.conn.commit()
        else:
            self.conn.executescript(SCHEMA)

    def insert_program(self, name: str, content: bytes,
                       machine: str = "") -> int:
        cur = self.conn.execute(
            "INSERT INTO programs(name, machine, content, created_at) "
            "VALUES (?,?,?,?)",
            (name, machine, content, time.time()))
        self.conn.commit()
        return cur.lastrowid

    def get_program(self, pid: int) -> Optional[Dict]:
        row = self.conn.execute(
            "SELECT * FROM programs WHERE id=?", (pid,)).fetchone()
        return dict(row) if row else None

    def save_comparison(self, pa: int, pb: int, machine: str,
                        stock: dict, report: dict) -> int:
        cur = self.conn.execute(
            "INSERT INTO comparisons(program_a, program_b, machine, "
            "stock_json, report_json, created_at) VALUES (?,?,?,?,?,?)",
            (pa, pb, machine, json.dumps(stock, ensure_ascii=False),
             json.dumps(report, ensure_ascii=False), time.time()))
        self.conn.commit()
        return cur.lastrowid

    def add_confirmation(self, code: str, effect: str, note: str,
                         version: str, machine: str) -> int:
        if effect not in ("no_effect", "modal_only", "motion_unverified"):
            raise ValueError("effect 必须是 no_effect/modal_only/motion_unverified")
        if not version.strip():
            raise ValueError("人工确认必须提供版本")
        if not machine.strip():
            raise ValueError("人工确认必须提供适用机型")
        self.conn.execute(
            "INSERT INTO confirmations(code, effect, note, version, machine,"
            " created_at) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(code, version, machine) DO UPDATE SET "
            "effect=excluded.effect, note=excluded.note, "
            "created_at=excluded.created_at",
            (code, effect, note, version, machine, time.time()))
        self.conn.commit()
        return self.get_confirmation(code, version, machine)["id"]

    def get_confirmation(self, code: str, version: str,
                         machine: str) -> Optional[Dict]:
        row = self.conn.execute(
            "SELECT * FROM confirmations WHERE code=? AND version=? "
            "AND machine=?", (code, version, machine)).fetchone()
        return dict(row) if row else None

    def list_confirmations(self) -> List[Dict]:
        rows = self.conn.execute(
            "SELECT * FROM confirmations ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    def resolver(self, version: str, machine: str):
        """构造 interpreter 用的确认查询回调。"""
        def resolve(code: str):
            return self.get_confirmation(code, version, machine)
        return resolve

    def close(self):
        self.conn.close()
