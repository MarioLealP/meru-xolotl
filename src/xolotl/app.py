"""xolotl: Meru's merger - checks and merges Shikigami's trust PRs"""

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

app = FastAPI(title="xolotl")


@app.get("/heartbeat", response_class=PlainTextResponse)
def heartbeat() -> str:
    """Meru's liveness and readiness probes (nuwa-app's heartbeat.path) - add a /ready once there are dependencies."""
    return "ok"
