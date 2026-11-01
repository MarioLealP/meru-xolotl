"""The verdict on one trust PR (D87): rebuild the files it should contain from the PR's own base commit and the app's
request, and merge only an exact match. Pure: the sweep fetches the PR, its files and the request, this decides.

- not Shikigami's PR -> left alone (a person's change is a person's merge)
- a title Shikigami doesn't write for this repo, a changed file that shouldn't change, a file that differs from the
  rebuilt one, a key that isn't a P-256 public key -> mismatch: never merged, Cú Chulainn is told
- no request backing it (missing, or onboarding a removed app / retiring a live one), a base Xolotl can't rebuild
  on -> wait: not merged, said why, nobody is quarantined (it can be timing or a person's edit, not tampering)
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from xolotl import expected
from xolotl.app_request import AppRequest

TITLE = re.compile(r"^(onboard|retire) ([a-z0-9](?:[-a-z0-9]*[a-z0-9])?): (fence|signing key|push rule)$")
REPO = {"fence": "meru", "signing key": "kyverno", "push rule": "zot"}


def paths(app: str, what: str) -> tuple[str, ...]:
    """The files a trust PR of this kind may touch - the only ones the sweep fetches."""
    return {
        "fence": ("kustomization.yaml", f"projects/{app}.yaml"),
        "signing key": ("kustomization.yaml", "keys/meru-signing-keys.yaml", f"exceptions/{app}-build.yaml"),
        "push rule": ("config.json",),
    }[what]


@dataclass(frozen=True)
class Title:
    direction: str  # onboard | retire
    app: str
    what: str  # fence | signing key | push rule


def parse_title(title: str) -> Title | None:
    match = TITLE.match(title)
    return Title(*match.groups()) if match else None


@dataclass(frozen=True)
class Proposal:
    """A PR as the check sees it. `base` / `head`: paths(...) at the PR's base and head commits (None = no file)."""

    repo: str
    number: int
    title: str
    author: str
    changed: frozenset[str]
    base: Mapping[str, str | None]
    head: Mapping[str, str | None]


class Verdict(StrEnum):
    MERGE = "merge"
    MISMATCH = "mismatch"
    WAIT = "wait"
    NOT_OURS = "not ours"


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    reason: str


class _Mismatch(Exception):
    pass


def _required(files: Mapping[str, str | None], path: str) -> str:
    text = files.get(path)
    if text is None:
        raise expected.BadLayout(f"no {path} at the PR's base")
    return text


def _new_key(head: Mapping[str, str | None], app: str) -> str:
    """The app's key as the PR adds it, checked for shape - the one value Xolotl can't rebuild."""
    try:
        key = expected.keys_data(head.get("keys/meru-signing-keys.yaml") or "").get(app)
    except expected.BadLayout as e:
        raise _Mismatch(f"the PR's signing-keys file: {e}") from e
    if not isinstance(key, str):
        raise _Mismatch(f"the PR adds no key for {app}")
    try:
        return expected.public_key(key)
    except ValueError as e:
        raise _Mismatch(f"{app}'s key: {e}") from e


def rebuild(t: Title, base: Mapping[str, str | None], head: Mapping[str, str | None], request: AppRequest) -> dict:
    """path -> what it must hold after the PR (None = deleted), built from the base alone (+ the PR's key)."""
    app, onboard = t.app, t.direction == "onboard"
    if t.what == "fence":
        kustomization, path = _required(base, "kustomization.yaml"), f"projects/{app}.yaml"
        if onboard:
            return {
                "kustomization.yaml": expected.with_line(kustomization, "projects", path),
                path: expected.fence(app, request.description),
            }
        return {"kustomization.yaml": expected.without_line(kustomization, path), path: None}
    if t.what == "signing key":
        kustomization, keys = _required(base, "kustomization.yaml"), _required(base, "keys/meru-signing-keys.yaml")
        path = f"exceptions/{app}-build.yaml"
        if onboard:
            return {
                "kustomization.yaml": expected.with_line(kustomization, "exceptions", path),
                "keys/meru-signing-keys.yaml": expected.with_key(keys, app, _new_key(head, app)),
                path: expected.build_exception(app),
            }
        return {
            "kustomization.yaml": expected.without_line(kustomization, path),
            "keys/meru-signing-keys.yaml": expected.without_key(keys, app),
            path: None,
        }
    config = _required(base, "config.json")
    return {"config.json": expected.with_push_rule(config, app) if onboard else expected.without_push_rule(config, app)}


def _backing(t: Title, request: AppRequest | None) -> str | None:
    """Why the request doesn't back this PR, or None if it does."""
    if request is None:
        return f"no request apps/{t.app}.yaml on Meru/requests main"
    if request.name != t.app:
        return f"apps/{t.app}.yaml names {request.name}"
    if t.direction == "onboard" and request.removed:
        return f"{t.app}'s request is marked removed - not onboarding it"
    if t.direction == "retire" and not request.removed:
        return f"{t.app}'s request isn't marked removed - not retiring it"
    return None


def decide(p: Proposal, request: AppRequest | None, shikigami: str) -> Decision:
    if p.author != shikigami:
        return Decision(Verdict.NOT_OURS, f"opened by {p.author}, not {shikigami}")
    t = parse_title(p.title)
    if t is None or REPO[t.what] != p.repo:
        return Decision(Verdict.MISMATCH, f"{p.title!r} isn't a title Shikigami writes for Meru/{p.repo}")
    if (why := _backing(t, request)) is not None:
        return Decision(Verdict.WAIT, why)
    assert request is not None  # _backing said so
    try:
        want = rebuild(t, p.base, p.head, request)
    except _Mismatch as e:
        return Decision(Verdict.MISMATCH, str(e))
    except expected.BadLayout as e:
        return Decision(Verdict.WAIT, f"can't rebuild on the PR's base: {e}")
    should_change = {path for path, text in want.items() if text != p.base.get(path)}
    if p.changed != should_change:
        extra, missing = sorted(p.changed - should_change), sorted(should_change - p.changed)
        return Decision(Verdict.MISMATCH, f"changes the wrong files (extra: {extra}, missing: {missing})")
    differ = sorted(path for path in should_change if p.head.get(path) != want[path])
    if differ:
        return Decision(Verdict.MISMATCH, f"differs from the rebuilt file: {', '.join(differ)}")
    return Decision(Verdict.MERGE, f"{p.title}: every file matches the rebuild")
