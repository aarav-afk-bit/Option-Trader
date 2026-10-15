from fastapi.testclient import TestClient

from option_trader.app import create_app


def test_health():
    client = TestClient(create_app())
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
