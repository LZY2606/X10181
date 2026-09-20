from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from gcode_review.compare import compare_programs
from gcode_review.interpreter import SafetyConfig, interpret
from gcode_review.parser import parse_program
from gcode_review.patches import patch_payload, unified_diff
from gcode_review.safety import analyze_comparison, findings_to_dicts
from gcode_review.serialize import serialize_side
from gcode_review.storage import Store

BASE_DIR = Path(__file__).parent
app = FastAPI(title="刀轨审阅台", version="1.0.0")
store = Store(BASE_DIR / "review.db")


class ReviewRequest(BaseModel):
    original: str
    modified: str
    machine_model: str = "generic"
    stock_min: Optional[tuple[float, float, float]] = None
    stock_max: Optional[tuple[float, float, float]] = None
    safe_z: float = 5.0


class VendorConfirmationRequest(BaseModel):
    instruction: str = Field(min_length=1)
    machine_model: str = Field(min_length=1)
    semantics_version: str = Field(min_length=1)
    effect: str
    confirmed_by: str = Field(min_length=1)
    note: str = ""


def _review(original_text: str, modified_text: str, request: ReviewRequest) -> dict:
    config = SafetyConfig(safe_z=request.safe_z)
    if request.stock_min:
        config.stock_min = request.stock_min
    if request.stock_max:
        config.stock_max = request.stock_max
    vendor_map = store.vendor_map(request.machine_model)
    original_blocks = interpret(parse_program(original_text), config, vendor_map)
    modified_blocks = interpret(parse_program(modified_text), config, vendor_map)
    comparison = compare_programs(original_blocks, modified_blocks)
    findings = analyze_comparison(original_blocks, modified_blocks, config)
    return {
        "title": "刀轨审阅台",
        "machine_model": request.machine_model,
        "original": serialize_side(original_blocks, "original"),
        "modified": serialize_side(modified_blocks, "modified"),
        "comparison": comparison,
        "safety": findings_to_dicts(findings),
        "vendor_confirmed": vendor_map,
    }


@app.get("/", response_class=FileResponse)
def index() -> Path:
    return BASE_DIR / "static" / "index.html"


@app.post("/api/review")
def review(request: ReviewRequest) -> dict:
    result = _review(request.original, request.modified, request)
    for side, content in (("original", request.original), ("modified", request.modified)):
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        store.save_program(side, f"{side}.gcode", content, digest)
    return result


@app.post("/api/vendor-confirmations", status_code=201)
def confirm_vendor(request: VendorConfirmationRequest) -> dict:
    allowed = {"none", "spindle_on_cw", "spindle_on_ccw", "spindle_off", "coolant_on", "coolant_off"}
    if request.effect not in allowed:
        raise HTTPException(status_code=400, detail=f"effect 必须是 {sorted(allowed)}")
    instruction = request.instruction.strip().upper()
    identification = store.confirm_vendor(
        instruction, request.machine_model.strip(), request.semantics_version.strip(),
        request.effect, request.confirmed_by.strip(), request.note.strip(),
    )
    return {"id": identification, "instruction": instruction}


@app.get("/api/vendor-confirmations")
def list_vendor_confirmations() -> dict:
    return {"items": store.list_confirmations()}


@app.post("/api/export/diff")
def export_diff(request: ReviewRequest) -> PlainTextResponse:
    return PlainTextResponse(unified_diff(request.original, request.modified),
                             media_type="text/x-diff")


@app.post("/api/export/patch")
def export_patch(request: ReviewRequest) -> dict:
    _review(request.original, request.modified, request)
    return patch_payload(request.original, request.modified)


app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
