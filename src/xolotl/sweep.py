"""One pass over the trust repos' open PRs (D87): fetch what the check needs, decide, act - merge, or say why not.
Each PR is said something once per head commit (a hidden marker in the comment), so a pass every few minutes
doesn't repeat itself, across restarts too."""

import logging

import httpx2

from xolotl import app_request
from xolotl.check import Decision, Proposal, Verdict, decide, parse_title, paths
from xolotl.gitea import Gitea, PullRequest
from xolotl.models import Value

log = logging.getLogger(__name__)


class Outcome(Value):
    merged: list[str]
    refused: list[str]  # mismatches: Cú Chulainn was asked
    waiting: list[str]


class Evidence(Value):
    repo: str
    pr: int
    head: str
    base: str


class QuarantineReport(Value):
    """What Cú Chulainn is told (D88): who asks, whom to quarantine, why, and where to look."""

    caller: str
    target: str
    reason: str
    evidence: Evidence


def marker(pr: PullRequest) -> str:
    return f"<!-- xolotl {pr.head} -->"


class Sweeper:
    def __init__(
        self,
        gitea: Gitea,
        http: httpx2.AsyncClient,
        repos: tuple[str, ...],
        requests_repo: str,
        shikigami: str,
        shikigami_ping_url: str,
        cuchulainn_url: str,
    ) -> None:
        self._gitea, self._http, self._repos = gitea, http, repos
        self._requests_repo, self._shikigami = requests_repo, shikigami
        self._shikigami_ping_url, self._cuchulainn_url = shikigami_ping_url, cuchulainn_url
        self._unlisted: set[str] = set()  # repos already warned about, until they list again

    async def sweep(self) -> Outcome:
        merged: list[str] = []
        refused: list[str] = []
        waiting: list[str] = []
        for repo in (*self._repos, *await self._app_repos()):
            try:
                prs = await self._gitea.open_prs(repo)
            except httpx2.HTTPStatusError as e:  # e.g. an app repo Xolotl isn't on: the rest still gets its pass
                if repo not in self._unlisted:  # once, not every sweep (golem, retired before Xolotl existed)
                    log.warning("Meru/%s: can't list its PRs: %s", repo, e)
                    self._unlisted.add(repo)
                continue
            self._unlisted.discard(repo)
            for pr in prs:
                if pr.author != self._shikigami:
                    continue  # a person's PR: a person merges it
                where = f"Meru/{repo}#{pr.number}"
                decision, request_at = await self._decide(repo, pr)
                log.info("%s %r: %s - %s", where, pr.title, decision.verdict, decision.reason)
                if decision.verdict == Verdict.MERGE:
                    if await self._merge(repo, pr, decision, request_at):
                        merged.append(where)
                    else:
                        waiting.append(where)
                elif decision.verdict == Verdict.MISMATCH:
                    await self._mismatch(repo, pr, decision)
                    refused.append(where)
                else:
                    await self._say_once(repo, pr, f"Not merged yet: {decision.reason}.")
                    waiting.append(where)
        if merged:
            await self._ping_shikigami()
        return Outcome(merged=merged, refused=refused, waiting=waiting)

    async def _app_repos(self) -> list[str]:
        """The repos of every app with a request on main: where Shikigami's marker PR (D99) and its stop and park PRs
        (cm-konk) open. Only with the bot - the requests repo is private."""
        if not self._gitea.can_write:
            return []
        at = await self._gitea.branch_head(self._requests_repo, private=True)
        repos = []
        for name in await self._gitea.files_in(self._requests_repo, "apps", at, private=True):
            text = await self._gitea.file(self._requests_repo, f"apps/{name}", at, private=True)
            try:
                request = app_request.parse(text or "")
            except ValueError:
                continue  # not a request Xolotl can read: nothing to retire from it
            if request.name not in self._repos:
                repos.append(request.name)
        return sorted(repos)

    async def _decide(self, repo: str, pr: PullRequest) -> tuple[Decision, str | None]:
        t = parse_title(pr.title)
        changed = await self._gitea.changed_files(repo, pr.number)
        wanted = paths(t.app, t.what) if t else ()
        base = {path: await self._gitea.file(repo, path, pr.base) for path in wanted}
        head = {path: await self._gitea.file(repo, path, pr.head) for path in wanted}
        request, request_at = None, None
        if t is not None and self._gitea.can_write:
            request_at = await self._gitea.branch_head(self._requests_repo, private=True)
            text = await self._gitea.file(self._requests_repo, f"apps/{t.app}.yaml", request_at, private=True)
            if text is not None:
                try:
                    request = app_request.parse(text)
                except ValueError as e:
                    log.warning("Meru/%s apps/%s.yaml: %s", self._requests_repo, t.app, e)
        proposal = Proposal(
            repo=repo, number=pr.number, title=pr.title, author=pr.author, changed=changed, base=base, head=head
        )
        return decide(proposal, request, self._shikigami), request_at

    async def _merge(self, repo: str, pr: PullRequest, decision: Decision, request_at: str | None) -> bool:
        if not self._gitea.can_write:
            log.info("Meru/%s#%d would merge - no Gitea token yet", repo, pr.number)
            return False
        if not pr.mergeable:
            await self._say_once(repo, pr, "Matches the rebuild, but conflicts with main - needs re-proposing.")
            return False
        message = (
            f"Checked by Xolotl (D87): {decision.reason} - rebuilt from {pr.base[:12]}, "
            f"backed by Meru/{self._requests_repo} at {(request_at or '')[:12]}."
        )
        merged = await self._gitea.merge(repo, pr.number, pr.head, f"{pr.title} (#{pr.number})", message)
        if not merged:
            await self._say_once(repo, pr, "Gitea refused the merge (conflict, or the PR changed after the check).")
        return merged

    async def _mismatch(self, repo: str, pr: PullRequest, decision: Decision) -> None:
        await self._say_once(
            repo,
            pr,
            f"Not merged: **doesn't match** what the rules rebuild from {pr.base[:12]}: {decision.reason}. "
            "Reported to Cú Chulainn (D87, D88) - a person looks at this one.",
        )
        report = QuarantineReport(
            caller="xolotl",
            target=self._shikigami,
            reason=f"trust PR Meru/{repo}#{pr.number} doesn't match its rebuild: {decision.reason}",
            evidence=Evidence(repo=f"Meru/{repo}", pr=pr.number, head=pr.head, base=pr.base),
        )
        try:
            (await self._http.post(self._cuchulainn_url, json=report.model_dump())).raise_for_status()
        except httpx2.HTTPError as e:  # not built yet (D88): logged, asked again next pass
            log.error("Cú Chulainn not told about Meru/%s#%d: %s", repo, pr.number, e)

    async def _say_once(self, repo: str, pr: PullRequest, text: str) -> None:
        """A comment, once per head commit - only with the bot (no token: the log has it)."""
        if not self._gitea.can_write:
            return
        if any(marker(pr) in body for body in await self._gitea.comments(repo, pr.number)):
            return
        await self._gitea.comment(repo, pr.number, f"{text}\n\n{marker(pr)}")

    async def _ping_shikigami(self) -> None:
        try:
            (await self._http.post(self._shikigami_ping_url, json={})).raise_for_status()
        except httpx2.HTTPError as e:  # its nightly pass catches up
            log.warning("Shikigami not pinged: %s", e)
