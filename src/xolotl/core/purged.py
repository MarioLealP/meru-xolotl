"""The verdict on one of Charon's PRs in Meru/requests (cm-mr2a): delete a purged app's request file.

The last step of a retirement (D53): Charon has purged everything the app left and proposes deleting
`apps/<app>.yaml`; once it's gone, Anubis gives the `retired` verdict. Xolotl refuses every other deletion
(approve.py), so this path is as narrow as it can be. Pure: the sweep fetches the PR and the file, this decides.

- not Charon's PR -> left alone
- a title Charon doesn't write, any change but deleting exactly `apps/<app>.yaml`, an app that isn't removed or has
  no removedAt -> mismatch: never merged, Cú Chulainn is told
- the retention hasn't run out by Xolotl's clock, main moved since the PR was made -> wait (timing, not tampering)
"""

import re
from datetime import datetime

from xolotl.core.check import APP, Decision, Verdict
from xolotl.models import app_request
from xolotl.models.base import Value

TITLE = re.compile(rf"^chore\(purge\): delete {APP} request$")


def title(app: str) -> str:
    """The title Charon writes - the shared contract with charon's own copy."""
    return f"chore(purge): delete {app} request"


class PurgeProposal(Value):
    """One of Charon's PRs as the check sees it: the app's file on main, at the PR's base and at its head."""

    title: str
    author: str
    changed: frozenset[str]
    on_main: str | None
    at_base: str | None
    at_head: str | None


def decide(pr: PurgeProposal, charon: str, now: datetime) -> Decision:
    """Merge only the deletion of a removed app's request whose retention has run out."""
    if pr.author != charon:
        return Decision(verdict=Verdict.NOT_OURS, reason=f"opened by {pr.author}, not {charon}")
    shape = _shape(pr)
    if isinstance(shape, Decision):
        return shape
    if pr.on_main is None:
        return Decision(verdict=Verdict.WAIT, reason=f"apps/{shape}.yaml isn't on main")
    if pr.on_main != pr.at_base:
        return Decision(verdict=Verdict.WAIT, reason=f"apps/{shape}.yaml changed on main since the PR was made")
    return _retention(shape, pr.on_main, now)


def _shape(pr: PurgeProposal) -> str | Decision:
    """The app, if the PR only deletes its request file under the title Charon writes; else the mismatch."""
    if (match := TITLE.match(pr.title)) is None:
        return Decision(verdict=Verdict.MISMATCH, reason=f"a title Charon doesn't write: {pr.title!r}")
    app = match.group(1)
    path = f"apps/{app}.yaml"
    if pr.changed != {path}:
        return Decision(verdict=Verdict.MISMATCH, reason=f"changes {sorted(pr.changed)}, not only {path}")
    if pr.at_head is not None:
        return Decision(verdict=Verdict.MISMATCH, reason=f"edits {path} instead of deleting it")
    return app


def _retention(app: str, on_main: str, now: datetime) -> Decision:
    """Merge once the removed request's retention has run out by Xolotl's clock."""
    try:
        request = app_request.parse(on_main)
    except ValueError as e:
        return Decision(verdict=Verdict.MISMATCH, reason=f"apps/{app}.yaml on main is unreadable: {e}")
    if not request.removed:
        return Decision(verdict=Verdict.MISMATCH, reason=f"{app} isn't removed")
    if request.removed_at is None:
        return Decision(verdict=Verdict.MISMATCH, reason=f"{app} is removed but has no removedAt")
    until = request.removed_at + request.retention
    if now < until:
        return Decision(verdict=Verdict.WAIT, reason=f"retention runs to {until:%Y-%m-%dT%H:%M:%SZ}")
    return Decision(verdict=Verdict.MERGE, reason=f"{app} removed, retention over since {until:%Y-%m-%d}")
