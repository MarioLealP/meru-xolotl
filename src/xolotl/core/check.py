"""The verdict on one of Shikigami's PRs (D87).

That is a trust PR in Meru/meru|kyverno|zot, or in an app's own repo its marker (D99) or its stop or park (cm-konk).
Rebuild the files it should contain from the PR's own base commit and the app's request, and merge only an exact match.
Pure: the sweep fetches the PR, its files and the request, this decides.

- not Shikigami's PR -> left alone (a person's change is a person's merge)
- a title Shikigami doesn't write for this repo, a changed file that shouldn't change, a file that differs from the
  rebuilt one, a key that isn't a P-256 public key -> mismatch: never merged, Cú Chulainn is told
- no request backing it (missing, or onboarding a removed app / retiring a live one), a base Xolotl can't rebuild
  on -> wait: not merged, said why, nobody is quarantined (it can be timing or a person's edit, not tampering)
"""

import re
from collections.abc import Mapping
from enum import StrEnum

from xolotl.core import expected, marker, retired
from xolotl.models.app_request import AppRequest
from xolotl.models.base import Value

APP = r"([a-z0-9](?:[-a-z0-9]*[a-z0-9])?)"
# Shikigami's PR titles (Conventional Commits). Trust, in Meru/meru|kyverno|zot: chore(trust): onboard <app> - fence.
# Retire, in the app's own repo (cm-konk): chore(retire): stop <app>, then chore(retire): park <app>.
TITLE = re.compile(rf"^chore\(trust\): (onboard|retire) {APP} - (fence|signing key|push rule)$")
RETIRE_TITLE = re.compile(rf"^chore\(retire\): (stop|park) {APP}$")
# The marker, in the app's own repo (D99): chore(deploy): <app> is a Meru app.
MARKER_TITLE = re.compile(rf"^chore\(deploy\): {APP} is a Meru app$")
REPO = {"fence": "meru", "signing key": "kyverno", "push rule": "zot"}


def repo_of(app: str, what: str) -> str:
    """The repo a PR of this kind belongs in: a trust repo, or the app's own for its stop and park."""
    return REPO.get(what, app)


def paths(app: str, what: str) -> tuple[str, ...]:
    """The files a PR of this kind may touch or the check reads - the only ones the sweep fetches."""
    return {
        "fence": ("kustomization.yaml", f"projects/{app}.yaml"),
        "signing key": ("kustomization.yaml", "keys/meru-signing-keys.yaml", f"exceptions/{app}-build.yaml"),
        "push rule": ("config.json",),
        "stop": ("deploy/values.yaml", "deploy/meru.yaml"),
        "park": ("deploy/values.yaml", "deploy/meru.yaml", "deploy/meru.yaml.in"),  # values: read, never changed
        "marker": ("deploy/values.yaml", "deploy/meru.yaml", "deploy/meru.yaml.in"),
    }[what]


class Title(Value):
    """A PR title taken apart."""

    direction: str  # onboard | retire
    app: str
    what: str  # fence | signing key | push rule | stop | park | marker


def parse_title(title: str) -> Title | None:
    """The title's parts, or None if Shikigami doesn't write titles like it."""
    if match := TITLE.match(title):
        direction, app, what = match.groups()
        return Title(direction=direction, app=app, what=what)
    if match := MARKER_TITLE.match(title):
        return Title(direction="onboard", app=match.group(1), what="marker")
    if match := RETIRE_TITLE.match(title):
        what, app = match.groups()
        return Title(direction="retire", app=app, what=what)
    return None


class Proposal(Value):
    """A PR as the check sees it. `base` / `head`: paths(...) at the PR's base and head commits (None = no file)."""

    repo: str
    number: int
    title: str
    author: str
    changed: frozenset[str]
    base: dict[str, str | None]
    head: dict[str, str | None]


class Verdict(StrEnum):
    """What the sweep does with a PR."""

    MERGE = "merge"
    MISMATCH = "mismatch"
    WAIT = "wait"
    NOT_OURS = "not ours"


class Decision(Value):
    """A verdict and the reason given for it."""

    verdict: Verdict
    reason: str


class _MismatchError(Exception):
    pass


def _required(files: Mapping[str, str | None], path: str) -> str:
    text = files.get(path)
    if text is None:
        raise expected.BadLayoutError(f"no {path} at the PR's base")
    return text


def _new_key(head: Mapping[str, str | None], app: str) -> str:
    """The app's key as the PR adds it, checked for shape - the one value Xolotl can't rebuild."""
    try:
        key = expected.keys_data(head.get("keys/meru-signing-keys.yaml") or "").get(app)
    except expected.BadLayoutError as e:
        raise _MismatchError(f"the PR's signing-keys file: {e}") from e
    if not isinstance(key, str):
        raise _MismatchError(f"the PR adds no key for {app}")
    try:
        return expected.public_key(key)
    except ValueError as e:
        raise _MismatchError(f"{app}'s key: {e}") from e


def _fence(t: Title, base: Mapping[str, str | None], request: AppRequest) -> dict[str, str | None]:
    app = t.app
    kustomization, path = _required(base, "kustomization.yaml"), f"projects/{app}.yaml"
    if t.direction == "onboard":
        return {
            "kustomization.yaml": expected.with_line(kustomization, "projects", path),
            path: expected.fence(app, request.description),
        }
    return {"kustomization.yaml": expected.without_line(kustomization, path), path: None}


def _signing_key(t: Title, base: Mapping[str, str | None], head: Mapping[str, str | None]) -> dict[str, str | None]:
    app = t.app
    kustomization, keys = _required(base, "kustomization.yaml"), _required(base, "keys/meru-signing-keys.yaml")
    path = f"exceptions/{app}-build.yaml"
    if t.direction == "onboard":
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


def _new_dsn(head: Mapping[str, str | None]) -> str | None:
    """The GlitchTip DSN the marker PR adds (D125), checked for shape and host.

    Like the signing key: the one value Xolotl takes from the PR, because GlitchTip draws it.
    """
    dsn = marker.dsn_line(head.get("deploy/values.yaml"))
    if dsn is not None and not marker.DSN.match(dsn):
        raise _MismatchError(f"the PR's GlitchTip DSN isn't one of ours: {dsn!r}")
    return dsn


def _marker(
    app: str, base: Mapping[str, str | None], head: Mapping[str, str | None], request: AppRequest
) -> dict[str, str | None]:
    if base.get("deploy/meru.yaml") is not None:
        raise expected.BadLayoutError(f"{app} already has deploy/meru.yaml on main")
    if not request.owner:
        raise expected.BadLayoutError(f"{app}'s request names no owner")
    template = _required(base, "deploy/meru.yaml.in")
    return marker.marker_files(
        template, base.get("deploy/values.yaml"), request.owner, request.secrets, request.entries, _new_dsn(head)
    )


def _stop(base: Mapping[str, str | None]) -> dict[str, str | None]:
    values, deployed = _required(base, "deploy/values.yaml"), _required(base, "deploy/meru.yaml")
    return {
        "deploy/values.yaml": retired.stopped_values(values),
        "deploy/meru.yaml": retired.stopped_marker(deployed),
    }


def _park(app: str, base: Mapping[str, str | None]) -> dict[str, str | None]:
    values, deployed = _required(base, "deploy/values.yaml"), _required(base, "deploy/meru.yaml")
    if not retired.is_stopped(values, deployed):  # parked before the stop, Argo would drop an app that still runs
        raise expected.BadLayoutError(f"{app} isn't stopped on main yet - the park waits for the stop")
    return {"deploy/meru.yaml.in": retired.parked(deployed), "deploy/meru.yaml": None}


def _push_rule(t: Title, base: Mapping[str, str | None]) -> dict[str, str | None]:
    config = _required(base, "config.json")
    onboard = t.direction == "onboard"
    return {
        "config.json": expected.with_push_rule(config, t.app) if onboard else expected.without_push_rule(config, t.app)
    }


def rebuild(
    t: Title, base: Mapping[str, str | None], head: Mapping[str, str | None], request: AppRequest
) -> dict[str, str | None]:
    """Each path and what it must hold after the PR (None = deleted), from the base alone (+ the PR's key or DSN)."""
    if t.what == "fence":
        return _fence(t, base, request)
    if t.what == "signing key":
        return _signing_key(t, base, head)
    if t.what == "marker":
        return _marker(t.app, base, head, request)
    if t.what == "stop":
        return _stop(base)
    if t.what == "park":
        return _park(t.app, base)
    return _push_rule(t, base)


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
    """Judge one PR: MERGE only if it is Shikigami's, backed by the request, and matches the rebuild exactly."""
    if p.author != shikigami:
        return Decision(verdict=Verdict.NOT_OURS, reason=f"opened by {p.author}, not {shikigami}")
    t = parse_title(p.title)
    if t is None or repo_of(t.app, t.what) != p.repo:
        return Decision(
            verdict=Verdict.MISMATCH, reason=f"{p.title!r} isn't a title Shikigami writes for Meru/{p.repo}"
        )
    if (why := _backing(t, request)) is not None:
        return Decision(verdict=Verdict.WAIT, reason=why)
    assert request is not None  # _backing said so
    try:
        want = rebuild(t, p.base, p.head, request)
    except _MismatchError as e:
        return Decision(verdict=Verdict.MISMATCH, reason=str(e))
    except expected.BadLayoutError as e:
        return Decision(verdict=Verdict.WAIT, reason=f"can't rebuild on the PR's base: {e}")
    should_change = {path for path, text in want.items() if text != p.base.get(path)}
    if p.changed != should_change:
        extra, missing = sorted(p.changed - should_change), sorted(should_change - p.changed)
        return Decision(
            verdict=Verdict.MISMATCH, reason=f"changes the wrong files (extra: {extra}, missing: {missing})"
        )
    differ = sorted(path for path in should_change if p.head.get(path) != want[path])
    if differ:
        return Decision(verdict=Verdict.MISMATCH, reason=f"differs from the rebuilt file: {', '.join(differ)}")
    return Decision(verdict=Verdict.MERGE, reason=f"{p.title}: every file matches the rebuild")
