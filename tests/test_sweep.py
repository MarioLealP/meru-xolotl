import json
from pathlib import Path
from urllib.parse import unquote

import httpx2
import pytest

from xolotl.gitea import Gitea
from xolotl.sweep import Sweeper

FIXTURES = Path(__file__).parent / "fixtures"
PR25 = FIXTURES / "prs" / "meru-25"
REQUEST = (FIXTURES / "request-xolotl.yaml").read_text()


class FakeGitea:
    """Meru/meru with one open PR (#25, Xolotl's real fence PR), Meru/requests, and the two services pinged."""

    def __init__(self, author: str = "shikigami", head_fence: str | None = None, mergeable: bool = True) -> None:
        self.meta = json.loads((PR25 / "pr.json").read_text())
        self.files = {
            ("meru", "base"): {p: (PR25 / "base" / p).read_text() for p in ("kustomization.yaml",)},
            ("meru", "head"): {
                "kustomization.yaml": (PR25 / "head" / "kustomization.yaml").read_text(),
                "projects/xolotl.yaml": head_fence or (PR25 / "head" / "projects/xolotl.yaml").read_text(),
            },
            ("requests", "r1"): {"apps/xolotl.yaml": REQUEST},
        }
        self.author, self.mergeable = author, mergeable
        self.comments: list[str] = []
        self.merges: list[dict] = []
        self.pings: list[str] = []
        self.quarantines: list[dict] = []
        self.listed: list[str] = []  # repos whose PRs were asked for
        self.unlistable: set[str] = set()  # repos whose PR list answers 500

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        url, path, method = request.url, unquote(request.url.path), request.method
        if url.host == "shikigami.svc":
            self.pings.append("shikigami")
            return httpx2.Response(202, json={})
        if url.host == "cuchulainn.svc":
            self.quarantines.append(json.loads(request.content))
            return httpx2.Response(202, json={})
        repo = path.removeprefix("/api/v1/repos/Meru/").split("/", 1)[0]
        tail = path.removeprefix(f"/api/v1/repos/Meru/{repo}")
        if tail == "/pulls":
            self.listed.append(repo)
            if repo in self.unlistable:
                return httpx2.Response(500)
            prs = [] if repo != "meru" else [self._pr()]
            return httpx2.Response(200, json=prs)
        if tail == "/pulls/25/files":
            return httpx2.Response(200, json=[{"filename": f} for f in self.meta["changed"]])
        if tail == "/contents/apps":
            if "authorization" not in request.headers:
                return httpx2.Response(404)
            names = [path.removeprefix("apps/") for (r, _), fs in self.files.items() if r == repo for path in fs]
            return httpx2.Response(200, json=[{"name": n, "type": "file"} for n in sorted(set(names))])
        if tail.startswith("/raw/"):
            files = self.files.get((repo, url.params["ref"]), {})
            name = tail.removeprefix("/raw/")
            if repo == "requests" and "authorization" not in request.headers:
                return httpx2.Response(404)
            return httpx2.Response(200, text=files[name]) if name in files else httpx2.Response(404)
        if tail == "/branches/main":
            return httpx2.Response(200, json={"commit": {"id": "r1"}})
        if tail == "/pulls/25/merge" and method == "POST":
            self.merges.append(json.loads(request.content))
            return httpx2.Response(200)
        if tail == "/issues/25/comments" and method == "GET":
            return httpx2.Response(200, json=[{"body": body} for body in self.comments])
        if tail == "/issues/25/comments" and method == "POST":
            self.comments.append(json.loads(request.content)["body"])
            return httpx2.Response(201, json={})
        return httpx2.Response(500, text=f"unexpected {method} {path}")

    def _pr(self) -> dict:
        return {
            "number": 25,
            "title": self.meta["title"],
            "user": {"login": self.author},
            "merge_base": "base",
            "head": {"sha": "head"},
            "mergeable": self.mergeable,
        }


def sweeper(fake: FakeGitea, token: str | None = "tok") -> Sweeper:
    http = httpx2.AsyncClient(transport=httpx2.MockTransport(fake.handler))
    return Sweeper(
        Gitea(http, "https://git.test", "Meru", token),
        http,
        ("meru", "kyverno", "zot"),
        "requests",
        "shikigami",
        "http://shikigami.svc/ping",
        "http://cuchulainn.svc/quarantine",
    )


async def test_a_matching_pr_is_merged_exactly_as_checked_and_shikigami_is_pinged():
    fake = FakeGitea()
    outcome = await sweeper(fake).sweep()
    assert outcome.merged == ["Meru/meru#25"]
    [merge] = fake.merges
    assert merge["head_commit_id"] == "head"  # Gitea refuses if the PR moved after the check
    assert merge["merge_title_field"] == "chore(trust): onboard xolotl - fence (#25)"
    assert "rebuilt from base" in merge["merge_message_field"]
    assert fake.pings == ["shikigami"]
    assert fake.comments == fake.quarantines == []


async def test_a_mismatch_is_refused_commented_once_and_reported():
    fence = (
        (PR25 / "head" / "projects/xolotl.yaml").read_text().replace("namespace: xolotl-build", "namespace: kyverno")
    )
    fake = FakeGitea(head_fence=fence)
    s = sweeper(fake)
    outcome = await s.sweep()
    await s.sweep()  # a second pass says nothing new
    assert outcome.refused == ["Meru/meru#25"]
    assert fake.merges == [] and fake.pings == []
    [comment] = fake.comments
    assert "doesn't match" in comment and "<!-- xolotl head -->" in comment
    assert fake.quarantines[0]["target"] == "shikigami"
    assert fake.quarantines[0]["evidence"] == {"repo": "Meru/meru", "pr": 25, "head": "head", "base": "base"}


async def test_without_a_token_nothing_is_written_and_every_pr_waits():
    fake = FakeGitea()
    outcome = await sweeper(fake, token=None).sweep()
    assert outcome.waiting == ["Meru/meru#25"]  # it can't read the private request without the bot
    assert fake.merges == fake.comments == fake.pings == []


async def test_a_persons_pr_is_never_touched():
    fake = FakeGitea(author="MarioGLealP")
    outcome = await sweeper(fake).sweep()
    assert (outcome.merged, outcome.refused, outcome.waiting) == ([], [], [])
    assert fake.merges == fake.comments == []


async def test_a_matching_pr_that_conflicts_waits_with_a_comment():
    fake = FakeGitea(mergeable=False)
    outcome = await sweeper(fake).sweep()
    assert outcome.waiting == ["Meru/meru#25"]
    assert fake.merges == []
    assert "conflicts with main" in fake.comments[0]


async def test_cuchulainn_down_is_logged_not_fatal(caplog: pytest.LogCaptureFixture):
    fence = (PR25 / "head" / "projects/xolotl.yaml").read_text() + "# extra\n"
    fake = FakeGitea(head_fence=fence)
    s = sweeper(fake)
    s._cuchulainn_url = "http://nowhere.svc/quarantine"
    outcome = await s.sweep()
    assert outcome.refused == ["Meru/meru#25"]
    assert "Cú Chulainn not told" in caplog.text


GOLEM_REMOVED_REQUEST = (
    "apiVersion: meru.clockmost.com/v1\nkind: App\nmetadata:\n  name: golem\n"
    "spec:\n  owner: mario\n  description: Test app\n  removed: true\n"
)


async def test_every_apps_own_repo_is_swept_too():
    fake = FakeGitea()
    fake.files[("requests", "r1")]["apps/golem.yaml"] = GOLEM_REMOVED_REQUEST
    outcome = await sweeper(fake).sweep()
    # each request's repo: its marker PR (D99) while onboarding, its stop and park PRs while retiring (cm-konk)
    assert fake.listed == ["meru", "kyverno", "zot", "golem", "xolotl"]
    assert outcome.merged == ["Meru/meru#25"]  # and the trust repos as before


async def test_a_repo_xolotl_cant_list_is_skipped_not_fatal(caplog: pytest.LogCaptureFixture):
    fake = FakeGitea()
    fake.files[("requests", "r1")]["apps/golem.yaml"] = GOLEM_REMOVED_REQUEST
    fake.unlistable = {"golem"}
    outcome = await sweeper(fake).sweep()
    assert outcome.merged == ["Meru/meru#25"]
    assert "Meru/golem: can't list its PRs" in caplog.text


async def test_an_unlistable_repo_is_warned_about_once_until_it_lists_again(caplog: pytest.LogCaptureFixture):
    fake = FakeGitea()
    fake.files[("requests", "r1")]["apps/golem.yaml"] = GOLEM_REMOVED_REQUEST
    fake.unlistable = {"golem"}
    s = sweeper(fake)
    await s.sweep()
    await s.sweep()
    assert caplog.text.count("Meru/golem: can't list its PRs") == 1
    fake.unlistable = set()
    await s.sweep()  # it lists again: a later failure is news again
    fake.unlistable = {"golem"}
    await s.sweep()
    assert caplog.text.count("Meru/golem: can't list its PRs") == 2
