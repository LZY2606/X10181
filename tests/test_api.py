from fastapi.testclient import TestClient

from app import app


client = TestClient(app)


def test_home_title():
    response = client.get("/")
    assert response.status_code == 200
    assert "刀轨审阅台" in response.text


def test_review_and_patch_roundtrip_api():
    payload = {
        "original": "G21 G90\nG1 X1 F100\nM30\n",
        "modified": "G21 G90\nG1 X2 F100\nM30\n",
        "stock_min": [-100, -100, -100],
        "stock_max": [100, 100, 100],
    }
    response = client.post("/api/review", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "刀轨审阅台"
    assert data["comparison"]["safe_no_change"] is False

    patch = client.post("/api/export/patch", json=payload).json()
    assert patch["roundtrip"] is True

    diff = client.post("/api/export/diff", json=payload)
    assert "G1 X1" in diff.text and "G1 X2" in diff.text


def test_vendor_confirmation_requires_version_and_machine():
    response = client.post("/api/vendor-confirmations", json={
        "instruction": "M999",
        "machine_model": "mill-a",
        "semantics_version": "manual/1.0",
        "effect": "spindle_on_cw",
        "confirmed_by": "tester",
    })
    assert response.status_code == 201
    assert response.json()["instruction"] == "M999"

    review = client.post("/api/review", json={
        "original": "M999\n",
        "modified": "M3\n",
        "machine_model": "mill-a",
    })
    assert review.status_code == 200
    assert not review.json()["original"]["lines"][0]["unknown"]
