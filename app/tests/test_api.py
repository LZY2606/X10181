"""端到端：导入→比较→确认→导出，及首页标题。"""
from fastapi.testclient import TestClient

from app.server import app

client = TestClient(app)


def test_index_title():
    r = client.get("/")
    assert r.status_code == 200
    assert "刀轨审阅台" in r.text


def test_full_flow_compare_confirm_export():
    a = b"G21 G90\nM200\nG1 X10 F100\nM30\n"
    b = b"G21 G90\nM200\nG1 X12 F100\nM30\n"
    pa = client.post("/api/programs",
                     data={"name": "a", "machine": "FANUC-0i"},
                     files={"file": ("a.gcode", a)}).json()["id"]
    pb = client.post("/api/programs",
                     data={"name": "b", "machine": "FANUC-0i"},
                     files={"file": ("b.gcode", b)}).json()["id"]

    r = client.post("/api/compare", json={
        "program_a": pa, "program_b": pb,
        "version": "rev1", "machine": "FANUC-0i",
        "stock": {"xmin": -5, "xmax": 50, "ymin": -5, "ymax": 50,
                  "zmin": -100, "z_clear": 1}})
    assert r.status_code == 200
    body = r.json()
    assert body["comparison"]["verdict"] == "blocked"
    assert any("M200" in x for x in
               body["comparison"]["block_reasons"]["a"])

    # 人工确认（带版本与机型）后不再阻止
    r = client.post("/api/confirmations", json={
        "code": "M200", "effect": "no_effect",
        "note": "探头应答信号，不影响运动",
        "version": "rev1", "machine": "FANUC-0i"})
    assert r.status_code == 200

    r = client.post("/api/compare", json={
        "program_a": pa, "program_b": pb,
        "version": "rev1", "machine": "FANUC-0i"})
    body = r.json()
    assert body["comparison"]["verdict"] == "different"
    assert body["comparison"]["counts"]["changed"] == 1

    # 确认必须带版本与机型
    r = client.post("/api/confirmations", json={
        "code": "M201", "effect": "no_effect", "note": "",
        "version": "", "machine": "FANUC-0i"})
    assert r.status_code == 400

    # 导出补丁包
    r = client.post("/api/export", json={
        "program_a": pa, "program_b": pb,
        "version": "rev1", "machine": "FANUC-0i"})
    assert r.status_code == 200
    import io, zipfile
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert zf.read("exported.gcode") == b
    assert zf.read("original.gcode") == a


def test_compare_text_endpoint():
    r = client.post("/api/compare", json={
        "text_a": "G21 G90\nG1 X1\nM30\n".replace("g1", "G1"),
        "text_b": "G21 G90\nG1 X1\nM30\n"})
    assert r.status_code == 200
    assert r.json()["comparison"]["verdict"] == "equivalent"
