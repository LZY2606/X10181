"""FastAPI 服务：导入、比较、确认、导出与静态审阅页面。"""
import io
import json
import os
import zipfile
from typing import Dict, List, Optional

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from .compare import compare_models
from .db import Database
from .interpreter import interpret
from .patching import apply_patch, build_patch
from .safety import Stock, safety_report

def _db_path() -> str:
    return os.environ.get(
        "GRSB_DB", os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                "grsbench.db"))


app = FastAPI(title="刀轨审阅台")
_db = None


def get_db() -> Database:
    """惰性创建，避免导入 app 包时产生文件副作用（测试可注入 GRSB_DB）。"""
    global _db
    if _db is None:
        _db = Database(_db_path())
    return _db


def _stock_from(stock: Optional[dict]) -> Stock:
    stock = stock or {}
    return Stock(**{k: float(v) for k, v in stock.items()
                    if k in Stock.__dataclass_fields__})


class CompareRequest(BaseModel):
    program_a: Optional[int] = None
    program_b: Optional[int] = None
    text_a: Optional[str] = None
    text_b: Optional[str] = None
    version: str = "v1"
    machine: str = "generic"
    stock: Optional[Dict[str, float]] = None
    save: bool = False


class ConfirmRequest(BaseModel):
    code: str
    effect: str
    note: str = ""
    version: str
    machine: str


class ExportRequest(BaseModel):
    program_a: int
    program_b: int
    version: str = "v1"
    machine: str = "generic"
    stock: Optional[Dict[str, float]] = None
    ops: Optional[List[dict]] = None


def _bytes(v: str) -> bytes:
    return v.encode("latin-1", errors="replace")


@app.get("/api/health")
def health():
    return {"status": "ok", "title": "刀轨审阅台"}


@app.post("/api/programs")
async def upload_program(name: str = Form(...),
                         machine: str = Form(""),
                         file: UploadFile = File(...)):
    content = await file.read()
    pid = get_db().insert_program(name, content, machine)
    return {"id": pid, "name": name, "bytes": len(content)}


@app.get("/api/programs/{pid}")
def get_program(pid: int, version: str = "v1", machine: str = "generic"):
    row = get_db().get_program(pid)
    if not row:
        raise HTTPException(404, "程序不存在")
    model = interpret(row["content"], get_db().resolver(version, machine))
    return {"id": pid, "name": row["name"], "machine": row["machine"],
            "model": model.to_dict(),
            "safety": safety_report(model, _stock_from(None))}


@app.post("/api/compare")
def compare(req: CompareRequest):
    if req.program_a is not None and req.program_b is not None:
        ra = get_db().get_program(req.program_a)
        rb = get_db().get_program(req.program_b)
        if not ra or not rb:
            raise HTTPException(404, "程序不存在")
        text_a, text_b = ra["content"], rb["content"]
        names = (ra["name"], rb["name"])
    else:
        if req.text_a is None or req.text_b is None:
            raise HTTPException(400, "需要提供两个程序 ID 或文本")
        text_a, text_b = _bytes(req.text_a), _bytes(req.text_b)
        names = ("原版", "修改版")

    resolver = get_db().resolver(req.version, req.machine)
    model_a = interpret(text_a, resolver)
    model_b = interpret(text_b, resolver)
    cmp_report = compare_models(model_a, model_b)
    stock = _stock_from(req.stock)
    safe_a = safety_report(model_a, stock)
    safe_b = safety_report(model_b, stock)

    cmp_id = None
    if req.save and req.program_a is not None and req.program_b is not None:
        cmp_id = get_db().save_comparison(req.program_a, req.program_b,
                                    req.machine, req.stock or {}, cmp_report)

    return {
        "id": cmp_id,
        "names": names,
        "version": req.version,
        "machine": req.machine,
        "comparison": cmp_report,
        "model_a": model_a.to_dict(),
        "model_b": model_b.to_dict(),
        "safety_a": safe_a,
        "safety_b": safe_b,
        "stock": stock.__dict__,
    }


@app.get("/api/confirmations")
def list_confirmations():
    return {"confirmations": get_db().list_confirmations()}


@app.post("/api/confirmations")
def add_confirmation(req: ConfirmRequest):
    try:
        cid = get_db().add_confirmation(req.code, req.effect, req.note,
                                  req.version, req.machine)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"id": cid}


@app.post("/api/export")
def export_patch(req: ExportRequest):
    ra = get_db().get_program(req.program_a)
    rb = get_db().get_program(req.program_b)
    if not ra or not rb:
        raise HTTPException(404, "程序不存在")
    text_a, text_b = ra["content"], rb["content"]

    resolver = get_db().resolver(req.version, req.machine)
    ma = interpret(text_a, resolver)
    mb = interpret(text_b, resolver)
    cmp_report = compare_models(ma, mb)

    if req.ops is not None:
        ops = req.ops
    else:
        ops = build_patch(cmp_report["alignment"], mb.lines, text_a)

    try:
        patched = apply_patch(text_a, ops)
    except Exception as exc:
        raise HTTPException(400, "补丁无法应用：%s" % exc)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("original.gcode", text_a)
        zf.writestr("modified.gcode", text_b)
        zf.writestr("exported.gcode", patched)
        zf.writestr("patch.json",
                    json.dumps(ops, ensure_ascii=False, indent=2))
        zf.writestr("report.json",
                    json.dumps({"comparison": cmp_report,
                                "safety_a": safety_report(ma, _stock_from(req.stock)),
                                "safety_b": safety_report(mb, _stock_from(req.stock))},
                               ensure_ascii=False, indent=2))
    return Response(
        content=buf.getvalue(), media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="review_export.zip"'})


@app.get("/")
def index():
    path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    return Response(open(path, "rb").read(), media_type="text/html")
