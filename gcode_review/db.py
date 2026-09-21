"""SQLite 持久化：项目、原始/修改版本字节、厂商指令人工确认。

确认必须带 version（G 代码版本/方言）与 machine_models（适用机型），
并保留确认人、说明与时间戳。
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Dict, Iterator, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    created_at REAL NOT NULL,
    settings_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS program_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    side TEXT NOT NULL CHECK(side IN ('a','b')),
    filename TEXT NOT NULL DEFAULT '',
    content BLOB NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE(project_id, side)
);
CREATE TABLE IF NOT EXISTS vendor_confirmations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    signature TEXT NOT NULL,
    code TEXT NOT NULL,
    sample_text TEXT NOT NULL,
    version TEXT NOT NULL,
    machine_models TEXT NOT NULL,
    semantics TEXT NOT NULL,
    confirmed_by TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    UNIQUE(project_id, signature)
);
"""


def init_db(path: str = "review.db") -> sqlite3.Connection:
    fresh = not os.path.exists(path)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn


@contextmanager
def tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def create_project(conn, name: str, settings: Optional[dict] = None) -> int:
    with tx(conn):
        cur = conn.execute(
            "INSERT INTO projects(name, created_at, settings_json) VALUES(?,?,?)",
            (name, time.time(), json.dumps(settings or {}, ensure_ascii=False)))
        return int(cur.lastrowid)


def save_version(conn, project_id: int, side: str, content: bytes,
                 filename: str = "") -> None:
    with tx(conn):
        conn.execute(
            """INSERT INTO program_versions(project_id, side, filename, content, created_at)
               VALUES(?,?,?,?,?)
               ON CONFLICT(project_id, side) DO UPDATE SET
                 filename=excluded.filename, content=excluded.content,
                 created_at=excluded.created_at""",
            (project_id, side, filename, bytes(content), time.time()))


def get_project(conn, project_id: int) -> Optional[dict]:
    row = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["settings"] = json.loads(d.pop("settings_json"))
    return d


def update_settings(conn, project_id: int, settings: dict) -> None:
    with tx(conn):
        conn.execute("UPDATE projects SET settings_json=? WHERE id=?",
                     (json.dumps(settings, ensure_ascii=False), project_id))


def list_projects(conn) -> List[dict]:
    rows = conn.execute("SELECT id,name,created_at FROM projects ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def get_version(conn, project_id: int, side: str) -> Optional[bytes]:
    row = conn.execute(
        "SELECT content FROM program_versions WHERE project_id=? AND side=?",
        (project_id, side)).fetchone()
    return None if row is None else bytes(row["content"])


def add_confirmation(conn, project_id: int, signature: str, code: str,
                     sample_text: str, version: str, machine_models: List[str],
                     semantics: str, confirmed_by: str) -> int:
    if not version.strip():
        raise ValueError("确认必须包含 G 代码版本/方言")
    if not machine_models or not all(m.strip() for m in machine_models):
        raise ValueError("确认必须包含至少一个适用机型")
    with tx(conn):
        cur = conn.execute(
            """INSERT INTO vendor_confirmations
               (project_id, signature, code, sample_text, version, machine_models,
                semantics, confirmed_by, created_at)
               VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(project_id, signature) DO UPDATE SET
                 version=excluded.version,
                 machine_models=excluded.machine_models,
                 semantics=excluded.semantics,
                 confirmed_by=excluded.confirmed_by,
                 created_at=excluded.created_at""",
            (project_id, signature, code, sample_text, version,
             json.dumps(machine_models, ensure_ascii=False), semantics,
             confirmed_by, time.time()))
        return int(cur.lastrowid)


def list_confirmations(conn, project_id: int) -> List[dict]:
    rows = conn.execute(
        "SELECT * FROM vendor_confirmations WHERE project_id=? ORDER BY id",
        (project_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["machine_models"] = json.loads(d.pop("machine_models"))
        out.append(d)
    return out
