import httpx2
from fastapi import FastAPI

from xolotl.api.app import app, create_app
from xolotl.config import Settings

NOWHERE = "http://127.0.0.1:9"  # nothing listens: every outside call fails at once


def client(served: FastAPI = app) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=served), base_url="http://test")


async def test_heartbeat():
    async with client() as http:
        response = await http.get("/heartbeat")
    assert response.status_code == 200
    assert response.text == "ok"


async def test_a_ping_queues_a_sweep():
    async with client() as http:
        response = await http.post("/ping", json={"anything": "ignored"})
    assert response.status_code == 202
    assert response.json() == {"status": "queued"}


async def test_approve_is_gone():
    served = create_app(Settings(gitea_url=NOWHERE))
    async with served.router.lifespan_context(served), client(served) as http:
        response = await http.post("/approve", json={"pr": 7})
    assert response.status_code == 404


def test_settings_need_no_approvals_environment(monkeypatch):
    for name in ("XOLOTL_APPROVALS_CLIENT_SECRET", "XOLOTL_APPROVALS_CLIENT_ID", "XOLOTL_APPROVERS_GROUP"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings()
    assert settings.gitea_token is None
    assert not hasattr(settings, "approvals_client_secret")
