"""xolotl: Meru's merger - checks and merges Shikigami's trust PRs (D87).

A sweep runs when Shikigami pings (after it opens trust PRs) and every sweep_seconds as the backstop; one at a time -
pings during a sweep queue one more.
"""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress

import httpx2
import sentry_sdk
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

from xolotl.clients.gitea import Gitea
from xolotl.config import Settings
from xolotl.sweep.sweep import Sweeper

log = logging.getLogger("xolotl")


async def keep_sweeping(sweeper: Sweeper, wake: asyncio.Event, every: int) -> None:
    """Sweep whenever woken, or every `every` seconds, until cancelled."""
    while True:
        with suppress(TimeoutError):
            await asyncio.wait_for(wake.wait(), timeout=every)
        wake.clear()  # a ping from now on asks for one more pass
        try:
            outcome = await sweeper.sweep()
            log.info("sweep: merged %s, refused %s, waiting %s", outcome.merged, outcome.refused, outcome.waiting)
        except Exception:  # Gitea down, a bad answer, a PR's file that won't parse: the loop outlives any one pass
            log.exception("sweep failed")


# Crash reports to GlitchTip (D125): SENTRY_DSN comes from nuwa-app's glitchtip.dsn; unset, the SDK stays off.
# No local variables or request bodies in a report - either can hold a secret.
sentry_sdk.init(include_local_variables=False, max_request_body_size="never")


def create_app(settings: Settings | None = None) -> FastAPI:
    """The app; its lifespan builds the Gitea client and the sweeper from the settings."""
    wake = asyncio.Event()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        s = settings or Settings()
        token = s.gitea_token or None  # an empty token counts as none
        if token is None:
            log.warning("no Gitea token yet: checking and logging only - nothing is merged")
        async with httpx2.AsyncClient(timeout=s.http_timeout) as http:
            gitea = Gitea(http=http, url=s.gitea_url, org=s.org, token=token)
            sweeper = Sweeper(
                gitea=gitea,
                http=http,
                repos=s.trust_repos,
                requests_repo=s.requests_repo,
                shikigami=s.shikigami_user,
                shikigami_ping_url=s.shikigami_ping_url,
                cuchulainn_url=s.cuchulainn_url,
            )
            wake.set()  # a first pass right away
            task = asyncio.create_task(keep_sweeping(sweeper, wake, s.sweep_seconds))
            try:
                yield
            finally:
                task.cancel()

    app = FastAPI(title="xolotl", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.get("/heartbeat", response_class=PlainTextResponse, status_code=200)
    async def heartbeat() -> str:
        """Meru's liveness and readiness probes (nuwa-app's heartbeat.path)."""
        return "ok"

    @app.post("/ping", status_code=202)
    async def ping() -> dict[str, str]:
        """Shikigami opened trust PRs: sweep now. The body means nothing - anyone may ask for a pass; it only checks."""
        wake.set()
        return {"status": "queued"}

    return app


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
app = create_app()
