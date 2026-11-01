"""Approve or deny a request PR on Meru/requests (D93, D98). Janus passes on what the atlas got back from Authentik;
Xolotl decides alone:

1. redeem the one-time code and check the ID token: Mario, in meru-approvers, for exactly this PR at this head commit
   and this decision (approval.verify)
2. the PR: open, for main, still at that head, changing exactly one request file
3. deny -> say who denied it, close the PR
4. approve -> the file with metadata.approvedBy, checked once more by Shikigami against main as it is now, committed
   to main by Xolotl (only Xolotl writes there, D69), then the PR is closed pointing at that commit

One at a time (a lock): two approvals never race for the same main.
"""

import asyncio
import logging
import re
from typing import Annotated

import httpx2
from pydantic import Field, StringConstraints

from xolotl import approval, stamp
from xolotl.gitea import Conflict, Gitea
from xolotl.models import Incoming, Value

log = logging.getLogger("xolotl.approve")
REQUEST_FILE = re.compile(r"^(apps|databases)/[a-z0-9]([-a-z0-9]*[a-z0-9])?\.yaml$")


class Ask(Value):
    """What Janus sends: the PR, the head commit the button was for, the decision, and Authentik's one-time code."""

    pr: Annotated[int, Field(gt=0)]
    head: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
    decision: approval.Decision
    code: Annotated[str, StringConstraints(min_length=1, max_length=4096)]


class Done(Value):
    pr: int
    decision: approval.Decision
    by: str
    commit: str | None  # the commit on main (approve)


class Failed(Exception):
    """Ends the approval with an HTTP status for Janus: 403 not an approval, 404/409 the PR isn't in that state,
    422 Shikigami's problems, 502 something upstream."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class _Verdict(Incoming):
    commit: str
    problems: list[str]


class Approvals:
    def __init__(
        self,
        gitea: Gitea,
        http: httpx2.AsyncClient,
        client: approval.OidcClient,
        org: str,
        repo: str,
        shikigami_validate_url: str,
    ) -> None:
        self._gitea, self._http, self._client = gitea, http, client
        self._org, self._repo, self._validate_url = org, repo, shikigami_validate_url
        self._keys = approval.Keys(http, client.jwks_url)
        self._lock = asyncio.Lock()

    async def handle(self, ask: Ask) -> Done:
        async with self._lock:
            try:
                return await self._handle(ask)
            except approval.Refused as e:
                log.warning("refused %s of %s/%s#%d: %s", ask.decision, self._org, self._repo, ask.pr, e)
                raise Failed(403, str(e)) from e
            except stamp.Unstampable as e:
                raise Failed(409, f"can't write approvedBy into the file: {e}") from e
            except Conflict as e:
                raise Failed(409, str(e)) from e
            except httpx2.HTTPError as e:
                raise Failed(502, f"Gitea, Authentik or Shikigami didn't answer: {type(e).__name__}") from e

    async def _handle(self, ask: Ask) -> Done:
        expected = approval.nonce(self._org, self._repo, ask.pr, ask.head, ask.decision)
        id_token = await approval.redeem(self._http, self._client, ask.code)
        who = await approval.verify(id_token, self._keys, self._client, expected)

        pull = await self._gitea.pull(self._repo, ask.pr)
        if pull is None:
            raise Failed(404, f"no PR #{ask.pr} on {self._org}/{self._repo}")
        if pull.state != "open":
            raise Failed(409, f"PR #{ask.pr} is already {'merged' if pull.merged else pull.state}")
        if pull.base_ref != "main":
            raise Failed(409, f"PR #{ask.pr} is for {pull.base_ref}, not main")
        if pull.head != ask.head:
            raise Failed(409, f"PR #{ask.pr} moved on since the button was shown - look at it again")
        changed = await self._gitea.changed_files(self._repo, ask.pr, private=True)
        if len(changed) != 1 or not REQUEST_FILE.fullmatch(path := next(iter(changed))):
            raise Failed(409, f"PR #{ask.pr} must change exactly one request file, changes {sorted(changed)}")

        if ask.decision == "deny":
            await self._gitea.comment(self._repo, ask.pr, f"Denied by {who.username} in the atlas.")
            await self._gitea.close(self._repo, ask.pr)
            log.info("%s denied %s/%s#%d (%s)", who.username, self._org, self._repo, ask.pr, path)
            return Done(pr=ask.pr, decision="deny", by=who.username, commit=None)

        proposed = await self._gitea.file(self._repo, path, ask.head, private=True)
        if proposed is None:
            raise Failed(409, f"PR #{ask.pr} deletes {path} - removals are a spec.removed: true change")
        stamped = stamp.stamp(proposed, who.username)
        verdict = await self._validate(path, stamped)
        if verdict.problems:
            raise Failed(422, "Shikigami: " + "; ".join(verdict.problems))
        current = await self._gitea.stored(self._repo, path)
        message = self._message(ask, pull.body, path, who, verdict.commit)
        commit = await self._gitea.write(self._repo, path, stamped, message, current.sha if current else None)
        await self._gitea.comment(self._repo, ask.pr, f"Approved by {who.username} in the atlas - on main in {commit}.")
        await self._gitea.close(self._repo, ask.pr)
        log.info("%s approved %s/%s#%d: %s in %s", who.username, self._org, self._repo, ask.pr, path, commit)
        return Done(pr=ask.pr, decision="approve", by=who.username, commit=commit)

    async def _validate(self, path: str, content: str) -> _Verdict:
        response = await self._http.post(self._validate_url, json={"path": path, "content": content})
        response.raise_for_status()
        return _Verdict.model_validate_json(response.content)

    def _message(self, ask: Ask, body: str, path: str, who: approval.Approver, checked: str) -> str:
        requested = [line for line in body.splitlines() if line.startswith("Requested-By:")][:1]
        return "\n".join(
            [
                f"feat(request): approve {path} (#{ask.pr})",
                "",
                f"Approved in the atlas: {self._org}/{self._repo}#{ask.pr} at {ask.head}.",
                "",
                *requested,
                f"Approved-By: {who.username}",
                f"Approval-Token: {who.token_id}",
                f"Checked-Against: {checked}",
            ]
        )
