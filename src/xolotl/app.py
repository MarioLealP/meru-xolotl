"""xolotl: Meru's merger - checks and merges Shikigami's trust PRs (D87). A sweep runs when Shikigami pings (after it
opens trust PRs) and every sweep_seconds as the backstop; one at a time - pings during a sweep queue one more."""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress

import httpx2
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

from xolotl.config import Settings
from xolotl.gitea import Gitea
from xolotl.sweep import Sweeper

log = logging.getLogger("xolotl")


async def keep_sweeping(sweeper: Sweeper, wake: asyncio.Event, every: int) -> None:
    while True:
        with suppress(TimeoutError):
            await asyncio.wait_for(wake.wait(), timeout=every)
        wake.clear()  # a ping from now on asks for one more pass
        try:
            outcome = await sweeper.sweep()
            log.info("sweep: merged %s, refused %s, waiting %s", outcome.merged, outcome.refused, outcome.waiting)
        except Exception:  # Gitea down, a bad answer: next pass tries again
            log.exception("sweep failed")


def create_app(settings: Settings | None = None) -> FastAPI:
    wake = asyncio.Event()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        s = settings or Settings()
        token = s.gitea_token.get_secret_value() if s.gitea_token else None
        if token is None:
            log.warning("no Gitea token yet: checking and logging only - nothing is merged")
        async with httpx2.AsyncClient(timeout=s.http_timeout) as http:
            gitea = Gitea(http, s.gitea_url, s.org, token)
            sweeper = Sweeper(
                gitea, http, s.trust_repos, s.requests_repo, s.shikigami_user, s.shikigami_ping_url, s.cuchulainn_url
            )
            wake.set()  # a first pass right away
            task = asyncio.create_task(keep_sweeping(sweeper, wake, s.sweep_seconds))
            yield
            task.cancel()

    app = FastAPI(title="xolotl", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.get("/heartbeat", response_class=PlainTextResponse)
    def heartbeat() -> str:
        """Meru's liveness and readiness probes (nuwa-app's heartbeat.path)."""
        return "ok"

    @app.post("/ping", status_code=202)
    def ping() -> dict[str, str]:
        """Shikigami opened trust PRs: sweep now. The body means nothing - anyone may ask for a pass; it only checks."""
        wake.set()
        return {"status": "queued"}

    return app


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
app = create_app()
