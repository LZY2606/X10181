import base64

import pytest
from fastapi.testclient import TestClient

import app as app_module


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # 每个测试独立 DB
    db = tmp_path / "t.db"
    new_conn = app_module.database.init_db(str(db))
    monkeypatch.setattr(app_module, "conn", new_conn)
    app_module.seed_demo(new_conn)
    return TestClient(app_module.app)


def b64s(data: bytes) -> str:
    return base64.b64encode(data).decode()


def make_project(client, a, b, name="t"):
    r = client.post("/api/projects", json={
        "name": name,
        "original": {"content_b64": b64s(a)},
        "modified": {"content_b64": b64s(b)},
    })
    return r.json()["id"]


def test_index_title(client):
    html = client.get("/").text
    assert "刀轨审阅台" in html


def test_review_endpoint_shape_and_seed(client):
    # 启动演示项目
    projects = client.get("/api/projects").json()
    assert projects
    pid = projects[0]["id"]
    data = client.get(f"/api/projects/{pid}/review").json()
    assert {"a", "b", "entries", "divergences", "safety"} <= set(data)
    assert data["a"]["motions"], "几何必须由解释器产生运动"
    assert data["a"]["lines"][0]["start"] == 0


def test_export_equals_modified(client):
    a = b"G21 G90\nG1 X10 F100\nM30\n"
    b = b"G21 G90\nG1 X12 F100\nM30\n"
    pid = make_project(client, a, b)
    r = client.get(f"/api/projects/{pid}/export")
    assert r.status_code == 200
    assert r.content == b
    assert int(r.headers["X-Patch-Count"]) >= 1


def test_confirmation_requires_version_and_models(client):
    pid = make_project(client, b"G0 X1\n", b"M77\nG0 X1\n")
    payload = {
        "signature": "M:77.0", "code": "M77", "sample_text": "M77",
        "version": "", "machine_models": [], "semantics": "x"}
    r = client.post(f"/api/projects/{pid}/confirmations", json=payload)
    assert r.status_code == 400
    payload.update(version="Fanuc 31i", machine_models=["VMC850"],
                   semantics="主轴雾冷开")
    r2 = client.post(f"/api/projects/{pid}/confirmations", json=payload)
    assert r2.status_code == 200
    conf = client.get(f"/api/projects/{pid}/confirmations").json()
    assert conf[0]["version"] == "Fanuc 31i"
    assert conf[0]["machine_models"] == ["VMC850"]


def test_unknown_program_not_safe(client):
    pid = make_project(client, b"G21\n", b"G21\nG187 P3\n")
    data = client.get(f"/api/projects/{pid}/review").json()
    assert data["safety"]["verdict"] != "safe"
