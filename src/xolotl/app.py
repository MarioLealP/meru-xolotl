"""xolotl: Meru's merger - checks and merges Shikigami's trust PRs (D87).

A sweep runs when Shikigami pings (after it opens trust PRs) and every sweep_seconds as the backstop; one at a time -
pings during a sweep queue one more. /approve takes Mario's approvals of request PRs from Janus (D98).
"""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress

import httpx2
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse

from xolotl.approval import Keys, OidcClient
from xolotl.approve import Approvals, Ask, Done, FailedError
from xolotl.config import Settings
from xolotl.gitea import Gitea
from xolotl.sweep import Sweeper

log = logging.getLogger("xolotl")
APPROVE_ERRORS: dict[int | str, dict[str, str]] = {
    403: {"description": "not an approval"},
    404: {"description": "no such PR"},
    409: {"description": "the PR isn't in that state, or main moved"},
    422: {"description": "Shikigami's problems with the file, or a malformed ask"},
    502: {"description": "Gitea, Authentik or Shikigami didn't answer"},
    503: {"description": "no Gitea token or approvals client secret yet"},
}


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


def create_app(settings: Settings | None = None) -> FastAPI:
    """The app; its lifespan builds the Gitea client, the sweeper and the approvals from the settings."""
    wake = asyncio.Event()
    state: dict[str, Approvals] = {}

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
            if token and s.approvals_client_secret:
                client = OidcClient(
                    issuer=s.approvals_issuer,
                    client_id=s.approvals_client_id,
                    client_secret=s.approvals_client_secret,
                    redirect_uri=s.approvals_redirect_uri,
                    group=s.approvers_group,
                )
                state["approvals"] = Approvals(
                    gitea=gitea,
                    http=http,
                    client=client,
                    keys=Keys(http=http, url=client.jwks_url),
                    org=s.org,
                    repo=s.requests_repo,
                    shikigami_validate_url=s.shikigami_validate_url,
                )
            else:
                log.warning("no Gitea token or approvals client secret: /approve answers 503")
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

    @app.post("/approve", status_code=200, responses=APPROVE_ERRORS)
    async def approve(ask: Ask) -> Done:
        """An approval or denial of a request PR, from Janus (D98).

        Anyone may call it: the one-time code only turns into an approval if Authentik signed Mario's yes to exactly
        this PR, commit and decision.
        """
        approvals = state.get("approvals")
        if approvals is None:
            raise HTTPException(503, "Xolotl can't approve yet (no Gitea token or approvals client secret)")
        try:
            return await approvals.handle(ask)
        except FailedError as e:
            raise HTTPException(e.status, str(e)) from e

    return app


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
app = create_app()
