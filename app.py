"""FastAPI 入口：离线刀轨审阅台。

启动后访问 http://127.0.0.1:5241 应看到“刀轨审阅台”。
"""
from __future__ import annotations

import base64
import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from gcode_review import db as database
from gcode_review.service import DEFAULT_BOX, build_review, export_modified

DB_PATH = os.environ.get("REVIEW_DB", "review.db")
conn = database.init_db(DB_PATH)

app = FastAPI(title="刀轨审阅台", version="1.0")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


class VersionIn(BaseModel):
    content_b64: str
    filename: str = ""


class CreateProjectIn(BaseModel):
    name: str
    original: Optional[VersionIn] = None
    modified: Optional[VersionIn] = None
    settings: dict = Field(default_factory=dict)


class ConfirmIn(BaseModel):
    signature: str
    code: str
    sample_text: str
    version: str
    machine_models: List[str]
    semantics: str
    confirmed_by: str = ""


class SettingsIn(BaseModel):
    settings: dict


def _b64(v: VersionIn) -> bytes:
    try:
        return base64.b64decode(v.content_b64)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"base64 解码失败: {exc}")


def _require_project(pid: int):
    proj = database.get_project(conn, pid)
    if proj is None:
        raise HTTPException(404, "项目不存在")
    return proj


@app.get("/api/health")
def health():
    return {"ok": True, "title": "刀轨审阅台"}


@app.get("/api/projects")
def projects():
    return database.list_projects(conn)


@app.post("/api/projects")
def create_project(body: CreateProjectIn):
    settings = body.settings or {}
    settings.setdefault("stock_box", DEFAULT_BOX)
    pid = database.create_project(conn, body.name, settings)
    if body.original:
        database.save_version(conn, pid, "a", _b64(body.original), body.original.filename)
    if body.modified:
        database.save_version(conn, pid, "b", _b64(body.modified), body.modified.filename)
    return {"id": pid}


@app.put("/api/projects/{pid}/versions/{side}")
def put_version(pid: int, side: str, body: VersionIn):
    if side not in ("a", "b"):
        raise HTTPException(400, "side 必须是 a 或 b")
    _require_project(pid)
    database.save_version(conn, pid, side, _b64(body), body.filename)
    return {"ok": True}


@app.put("/api/projects/{pid}/settings")
def put_settings(pid: int, body: SettingsIn):
    _require_project(pid)
    database.update_settings(conn, pid, body.settings)
    return {"ok": True}


@app.get("/api/projects/{pid}/review")
def review(pid: int):
    proj = _require_project(pid)
    a = database.get_version(conn, pid, "a")
    b = database.get_version(conn, pid, "b")
    if a is None or b is None:
        raise HTTPException(409, "需要同时导入原版与修改版")
    return build_review(a, b, proj.get("settings") or {})


@app.get("/api/projects/{pid}/confirmations")
def confirmations(pid: int):
    _require_project(pid)
    return database.list_confirmations(conn, pid)


@app.post("/api/projects/{pid}/confirmations")
def add_confirmation(pid: int, body: ConfirmIn):
    _require_project(pid)
    if not body.version.strip():
        raise HTTPException(400, "确认必须包含 G 代码版本/方言")
    if not body.machine_models or not all(m.strip() for m in body.machine_models):
        raise HTTPException(400, "确认必须包含至少一个适用机型")
    if not body.semantics.strip():
        raise HTTPException(400, "请填写人工语义说明")
    try:
        cid = database.add_confirmation(
            conn, pid, body.signature, body.code, body.sample_text,
            body.version, body.machine_models, body.semantics, body.confirmed_by)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"id": cid}


@app.get("/api/projects/{pid}/export")
def export_project(pid: int):
    proj = _require_project(pid)
    a = database.get_version(conn, pid, "a")
    b = database.get_version(conn, pid, "b")
    if a is None or b is None:
        raise HTTPException(409, "需要两个版本")
    result = export_modified(a, b, proj.get("settings") or {})
    return Response(
        content=result["bytes"],
        media_type="text/plain; charset=utf-8",
        headers={"X-Patch-Count": str(len(result["patches"]))})


@app.get("/api/projects/{pid}/patches")
def patches(pid: int):
    proj = _require_project(pid)
    a = database.get_version(conn, pid, "a")
    b = database.get_version(conn, pid, "b")
    if a is None or b is None:
        raise HTTPException(409, "需要两个版本")
    return export_modified(a, b, proj.get("settings") or {})["patches"]


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# 首次启动写入演示程序，便于直接打开页面
def seed_demo(connection=None) -> int:
    """幂等播种；空库时返回新项目 id，否则返回已有首个项目 id。"""
    c = connection or conn
    existing = database.list_projects(c)
    if existing:
        return existing[0]["id"]
    original = (
        "G21 G90 G54 G17\n"
        "G43 H1 Z50.\n"
        "S1200 M3\n"
        "G0 X0 Y0 Z5\n"
        "G1 Z-2 F80\n"
        "G1 X20 Y0 F200\n"
        "G3 X30 Y10 I0 J10\n"
        "G1 Y20\n"
        "G1 X0 Y20 Z5 F500\n"
        "G0 Z50\n"
        "G49 M5\n"
        "M30\n"
    ).encode()
    modified = (
        "G21 G90 G54 G17\n"
        "G43 H1 Z50.\n"
        "S1200 M3\n"
        "G0 X0 Y0 Z5\n"
        "G1 Z-2 F80\n"
        "G1 X20 Y0 F260\n"
        "G3 X30 Y10 R10.\n"
        "G2 X20 Y20 I-10 J0\n"
        "G1 X0 Y20 Z5 F500\n"
        "G0 Z50\n"
        "G49 M5\n"
        "M30\n"
    ).encode()
    demo_box = {"xmin": -30.0, "ymin": -30.0, "zmin": 0.0,
                "xmax": 30.0, "ymax": 30.0, "zmax": 2.0}
    pid = database.create_project(c, "演示：法兰轮廓", {"stock_box": demo_box})
    database.save_version(c, pid, "a", original, "original.nc")
    database.save_version(c, pid, "b", modified, b"", ) if False else database.save_version(c, pid, "b", modified, "modified.nc")
    return pid


seed_demo()
