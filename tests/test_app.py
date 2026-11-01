from fastapi.testclient import TestClient

from xolotl.app import app


def test_heartbeat():
    response = TestClient(app).get("/heartbeat")
    assert response.status_code == 200
    assert response.text == "ok"


def test_a_ping_queues_a_sweep():
    response = TestClient(app).post("/ping", json={"anything": "ignored"})
    assert response.status_code == 202
    assert response.json() == {"status": "queued"}
