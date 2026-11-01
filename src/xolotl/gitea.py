"""The few Gitea calls Xolotl makes. Reads of the public trust repos go without credentials; the token is only for
what needs the bot: merging, commenting, and reading the private requests repo."""

from urllib.parse import quote

import httpx2
from pydantic import TypeAdapter

from xolotl.models import Incoming, Value


class NoToken(Exception):
    """The call needs Xolotl's bot, which doesn't exist yet."""


class PullRequest(Value):
    number: int
    title: str
    author: str
    base: str  # the merge base: the commit the PR was made from
    head: str
    mergeable: bool


# Gitea's answers, only the fields Xolotl reads - a missing or mistyped one fails the call (the sweep logs it and
# tries again next pass) instead of becoming a KeyError deep in the check.
class _User(Incoming):
    login: str


class _Ref(Incoming):
    sha: str


class _Pull(Incoming):
    number: int
    title: str
    user: _User
    merge_base: str
    head: _Ref
    mergeable: bool | None = None  # Gitea leaves it out while it's still working it out

    def as_pull_request(self) -> PullRequest:
        return PullRequest(
            number=self.number,
            title=self.title,
            author=self.user.login,
            base=self.merge_base,
            head=self.head.sha,
            mergeable=bool(self.mergeable),
        )


class _ChangedFile(Incoming):
    filename: str
    previous_filename: str | None = None


class _Commit(Incoming):
    id: str


class _Branch(Incoming):
    commit: _Commit


class _Comment(Incoming):
    body: str


class _Entry(Incoming):
    name: str
    type: str  # file | dir | symlink | submodule


_PULLS = TypeAdapter(list[_Pull])
_CHANGED = TypeAdapter(list[_ChangedFile])
_COMMENTS = TypeAdapter(list[_Comment])
_ENTRIES = TypeAdapter(list[_Entry])


class Gitea:
    def __init__(self, http: httpx2.AsyncClient, url: str, org: str, token: str | None) -> None:
        self._http, self._api, self._org, self._token = http, f"{url}/api/v1", org, token

    @property
    def can_write(self) -> bool:
        return self._token is not None

    def _auth(self) -> dict[str, str]:
        if self._token is None:
            raise NoToken("no Gitea token for Xolotl's bot yet")
        return {"Authorization": f"token {self._token}"}

    async def open_prs(self, repo: str) -> list[PullRequest]:
        response = await self._http.get(
            f"{self._api}/repos/{self._org}/{repo}/pulls", params={"state": "open", "limit": 50}
        )
        response.raise_for_status()
        return [pr.as_pull_request() for pr in _PULLS.validate_json(response.content)]

    async def changed_files(self, repo: str, number: int) -> frozenset[str]:
        """Every path the PR touches - both names of a rename."""
        response = await self._http.get(
            f"{self._api}/repos/{self._org}/{repo}/pulls/{number}/files", params={"limit": 100}
        )
        response.raise_for_status()
        names = set()
        for changed in _CHANGED.validate_json(response.content):
            names.add(changed.filename)
            if changed.previous_filename:
                names.add(changed.previous_filename)
        return frozenset(names)

    async def file(self, repo: str, path: str, ref: str, private: bool = False) -> str | None:
        """A file's text at a commit or branch, or None if it isn't there."""
        response = await self._http.get(
            f"{self._api}/repos/{self._org}/{repo}/raw/{quote(path)}",
            params={"ref": ref},
            headers=self._auth() if private else {},
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.text

    async def files_in(self, repo: str, folder: str, ref: str, private: bool = False) -> list[str]:
        """The file names directly in a folder at a commit ([] if the folder isn't there)."""
        response = await self._http.get(
            f"{self._api}/repos/{self._org}/{repo}/contents/{quote(folder)}",
            params={"ref": ref},
            headers=self._auth() if private else {},
        )
        if response.status_code == 404:
            return []
        response.raise_for_status()
        return [entry.name for entry in _ENTRIES.validate_json(response.content) if entry.type == "file"]

    async def branch_head(self, repo: str, branch: str = "main", private: bool = False) -> str:
        response = await self._http.get(
            f"{self._api}/repos/{self._org}/{repo}/branches/{branch}", headers=self._auth() if private else {}
        )
        response.raise_for_status()
        return _Branch.model_validate_json(response.content).commit.id

    async def merge(self, repo: str, number: int, head: str, title: str, message: str) -> bool:
        """Merge exactly the checked commit: Gitea refuses if the PR moved on (head_commit_id). False if Gitea said
        it can't merge (conflict, moved head, not mergeable)."""
        response = await self._http.post(
            f"{self._api}/repos/{self._org}/{repo}/pulls/{number}/merge",
            headers=self._auth(),
            json={"do": "merge", "head_commit_id": head, "merge_title_field": title, "merge_message_field": message},
        )
        if response.status_code in (405, 409):
            return False
        response.raise_for_status()
        return True

    async def comments(self, repo: str, number: int) -> list[str]:
        response = await self._http.get(f"{self._api}/repos/{self._org}/{repo}/issues/{number}/comments")
        response.raise_for_status()
        return [comment.body for comment in _COMMENTS.validate_json(response.content)]

    async def comment(self, repo: str, number: int, body: str) -> None:
        response = await self._http.post(
            f"{self._api}/repos/{self._org}/{repo}/issues/{number}/comments", headers=self._auth(), json={"body": body}
        )
        response.raise_for_status()
