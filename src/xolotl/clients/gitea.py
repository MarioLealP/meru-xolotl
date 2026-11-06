"""The few Gitea calls Xolotl makes.

Reads of the public trust repos go without credentials; the token is only for what needs the bot: merging, commenting,
and reading the private requests repo.
"""

import base64
from urllib.parse import quote

import httpx2
from pydantic import SecretStr, TypeAdapter

from xolotl.models.base import Incoming, Service, Value


class NoTokenError(Exception):
    """The call needs Xolotl's bot, which doesn't exist yet."""


class PullRequest(Value):
    """One open PR as the sweep reads it."""

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


class OpenPull(Value):
    """One PR as an approval reads it (D98)."""

    number: int
    state: str  # open | closed
    merged: bool
    author: str
    title: str
    body: str
    base_ref: str  # the branch it's for
    head: str


class _BaseRef(Incoming):
    ref: str


class _PullDetail(Incoming):
    number: int
    state: str
    merged: bool = False
    title: str
    body: str | None = None
    user: _User
    base: _BaseRef
    head: _Ref


class _FileEntry(Incoming):
    sha: str
    content: str
    encoding: str


class _CommitAnswer(Incoming):
    commit: _Ref


class StoredFile(Value):
    """A file on a branch, with what an update has to name."""

    text: str
    sha: str  # the blob - an update names it, so Gitea refuses if the file changed meanwhile


class ConflictError(Exception):
    """Gitea refused the write because main moved under it - try again from the top."""


_PULLS = TypeAdapter(list[_Pull])
_CHANGED = TypeAdapter(list[_ChangedFile])
_COMMENTS = TypeAdapter(list[_Comment])
_ENTRIES = TypeAdapter(list[_Entry])


class Gitea(Service):
    """Gitea's API for one org."""

    http: httpx2.AsyncClient
    url: str
    org: str
    token: SecretStr | None

    @property
    def _api(self) -> str:
        return f"{self.url}/api/v1"

    @property
    def can_write(self) -> bool:
        """Whether Xolotl has its bot's token: without it nothing is merged, commented or read from private repos."""
        return self.token is not None

    def _auth(self) -> dict[str, str]:
        if self.token is None:
            raise NoTokenError("no Gitea token for Xolotl's bot yet")
        return {"Authorization": f"token {self.token.get_secret_value()}"}

    async def open_prs(self, repo: str, private: bool = False) -> list[PullRequest]:
        """The repo's open PRs, at most 50."""
        response = await self.http.get(
            f"{self._api}/repos/{self.org}/{repo}/pulls",
            params={"state": "open", "limit": 50},
            headers=self._auth() if private else {},
        )
        response.raise_for_status()
        return [pr.as_pull_request() for pr in _PULLS.validate_json(response.content)]

    async def changed_files(self, repo: str, number: int, private: bool = False) -> frozenset[str]:
        """Every path the PR touches - both names of a rename."""
        response = await self.http.get(
            f"{self._api}/repos/{self.org}/{repo}/pulls/{number}/files",
            params={"limit": 100},
            headers=self._auth() if private else {},
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
        response = await self.http.get(
            f"{self._api}/repos/{self.org}/{repo}/raw/{quote(path)}",
            params={"ref": ref},
            headers=self._auth() if private else {},
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.text

    async def files_in(self, repo: str, folder: str, ref: str, private: bool = False) -> list[str]:
        """The file names directly in a folder at a commit ([] if the folder isn't there)."""
        response = await self.http.get(
            f"{self._api}/repos/{self.org}/{repo}/contents/{quote(folder)}",
            params={"ref": ref},
            headers=self._auth() if private else {},
        )
        if response.status_code == 404:
            return []
        response.raise_for_status()
        return [entry.name for entry in _ENTRIES.validate_json(response.content) if entry.type == "file"]

    async def branch_head(self, repo: str, branch: str = "main", private: bool = False) -> str:
        """The commit a branch is at."""
        response = await self.http.get(
            f"{self._api}/repos/{self.org}/{repo}/branches/{branch}", headers=self._auth() if private else {}
        )
        response.raise_for_status()
        return _Branch.model_validate_json(response.content).commit.id

    async def merge(self, repo: str, number: int, head: str, title: str, message: str) -> bool:
        """Merge exactly the checked commit: Gitea refuses if the PR moved on (head_commit_id).

        False if Gitea said it can't merge (conflict, moved head, not mergeable).
        """
        response = await self.http.post(
            f"{self._api}/repos/{self.org}/{repo}/pulls/{number}/merge",
            headers=self._auth(),
            json={"do": "merge", "head_commit_id": head, "merge_title_field": title, "merge_message_field": message},
        )
        if response.status_code in (405, 409):
            return False
        response.raise_for_status()
        return True

    async def comments(self, repo: str, number: int, private: bool = False) -> list[str]:
        """The bodies of a PR's comments."""
        response = await self.http.get(
            f"{self._api}/repos/{self.org}/{repo}/issues/{number}/comments", headers=self._auth() if private else {}
        )
        response.raise_for_status()
        return [comment.body for comment in _COMMENTS.validate_json(response.content)]

    async def comment(self, repo: str, number: int, body: str) -> None:
        """Comment on a PR as the bot."""
        response = await self.http.post(
            f"{self._api}/repos/{self.org}/{repo}/issues/{number}/comments", headers=self._auth(), json={"body": body}
        )
        response.raise_for_status()

    async def pull(self, repo: str, number: int) -> OpenPull | None:
        """One PR in full, or None if it isn't there."""
        response = await self.http.get(f"{self._api}/repos/{self.org}/{repo}/pulls/{number}", headers=self._auth())
        if response.status_code == 404:
            return None
        response.raise_for_status()
        p = _PullDetail.model_validate_json(response.content)
        return OpenPull(
            number=p.number,
            state=p.state,
            merged=p.merged,
            author=p.user.login,
            title=p.title,
            body=p.body or "",
            base_ref=p.base.ref,
            head=p.head.sha,
        )

    async def stored(self, repo: str, path: str, ref: str = "main") -> StoredFile | None:
        """A private file's text and blob sha at a branch, or None if it isn't there."""
        response = await self.http.get(
            f"{self._api}/repos/{self.org}/{repo}/contents/{quote(path)}", params={"ref": ref}, headers=self._auth()
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        entry = _FileEntry.model_validate_json(response.content)
        if entry.encoding != "base64":
            raise ValueError(f"{path}: Gitea sent {entry.encoding}, not base64")
        return StoredFile(text=base64.b64decode(entry.content).decode(), sha=entry.sha)

    async def write(self, repo: str, path: str, text: str, message: str, replaces: str | None) -> str:
        """Commit one file to main: create it (replaces=None) or update the blob `replaces`.

        Returns the new commit's sha; ConflictError if the file appeared or changed since it was read.
        """
        body: dict[str, str] = {
            "branch": "main",
            "content": base64.b64encode(text.encode()).decode(),
            "message": message,
        }
        if replaces is None:
            response = await self.http.post(
                f"{self._api}/repos/{self.org}/{repo}/contents/{quote(path)}", headers=self._auth(), json=body
            )
        else:
            response = await self.http.put(
                f"{self._api}/repos/{self.org}/{repo}/contents/{quote(path)}",
                headers=self._auth(),
                json=body | {"sha": replaces},
            )
        if response.status_code in (409, 422):
            raise ConflictError(f"{path} changed on main meanwhile ({response.status_code})")
        response.raise_for_status()
        return _CommitAnswer.model_validate_json(response.content).commit.sha

    async def close(self, repo: str, number: int) -> None:
        """Close a PR without merging it."""
        response = await self.http.patch(
            f"{self._api}/repos/{self.org}/{repo}/pulls/{number}", headers=self._auth(), json={"state": "closed"}
        )
        response.raise_for_status()
