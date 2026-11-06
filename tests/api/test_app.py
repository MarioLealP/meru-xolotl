import httpx2
from fastapi import FastAPI
from pydantic import SecretStr

from xolotl.api.app import app, create_app
from xolotl.config import Settings

NOWHERE = "http://127.0.0.1:9"  # nothing listens: every outside call fails at once
ASK = {"pr": 7, "head": "a" * 40, "decision": "approve", "code": "one-time"}


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


async def test_approve_answers_503_without_a_gitea_token():
    served = create_app(Settings(gitea_url=NOWHERE, gitea_token=SecretStr(""), approvals_client_secret=SecretStr("s")))
    async with served.router.lifespan_context(served), client(served) as http:
        response = await http.post("/approve", json=ASK)
    assert response.status_code == 503


async def test_approve_is_wired_up_with_a_token_and_a_client_secret():
    settings = Settings(
        gitea_url=NOWHERE,
        gitea_token=SecretStr("tok"),
        approvals_client_secret=SecretStr("s"),
        approvals_issuer=f"{NOWHERE}/application/o/xolotl-approvals/",
    )
    served = create_app(settings)
    async with served.router.lifespan_context(served), client(served) as http:
        response = await http.post("/approve", json=ASK)
    assert response.status_code == 502  # got as far as asking Authentik
