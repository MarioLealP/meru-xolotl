"""Shikigami's marker PR in a new app's own repo (D99). The base is the real nuwa-python template's deploy files; the PR
contents were written by Shikigami's own marker.py - so this checks Xolotl's rules against Shikigami's, not itself."""

from pathlib import Path

import pytest

from xolotl.app_request import AppRequest, parse
from xolotl.check import Proposal, Verdict, decide, parse_title, paths
from xolotl.retired import ENABLED_LINE

F = Path(__file__).parent / "fixtures" / "marker-tanuki"
BASE = {p: (F / "base" / p).read_text() for p in ("deploy/values.yaml", "deploy/meru.yaml.in")}
PR = {p: (F / "pr" / p).read_text() for p in ("deploy/values.yaml", "deploy/meru.yaml")}
TANUKI = AppRequest(
    name="tanuki",
    description="Test app",
    removed=False,
    owner="mario",
    secrets=("discord-token", "session-key"),
)


def files(on: dict[str, str]) -> dict[str, str | None]:
    return {path: on.get(path) for path in paths("tanuki", "marker")}


def marker_pr(head: dict[str, str] | None = None, **change) -> Proposal:
    pr = {
        "repo": "tanuki",
        "number": 1,
        "title": "chore(deploy): tanuki is a Meru app",
        "author": "shikigami",
        "changed": frozenset({"deploy/values.yaml", "deploy/meru.yaml", "deploy/meru.yaml.in"}),
        "base": files(BASE),
        "head": files(head if head is not None else PR),
    }
    return Proposal.model_validate(pr | change)


def test_the_marker_title_parses_and_belongs_in_the_apps_own_repo():
    t = parse_title("chore(deploy): tanuki is a Meru app")
    assert t is not None and (t.direction, t.app, t.what) == ("onboard", "tanuki", "marker")
    assert decide(marker_pr(repo="meru"), TANUKI, "shikigami").verdict == Verdict.MISMATCH


def test_shikigamis_marker_pr_is_merged():
    decision = decide(marker_pr(), TANUKI, "shikigami")
    assert decision.verdict == Verdict.MERGE, decision.reason


def test_a_marker_without_secrets_only_fills_the_owner():
    request = TANUKI.model_copy(update={"secrets": ()})
    head = {
        **PR,
        "deploy/values.yaml": BASE["deploy/values.yaml"].replace('owner: "" # from the request', "owner: mario"),
    }
    assert decide(marker_pr(head), request, "shikigami").verdict == Verdict.MERGE


@pytest.mark.parametrize(
    ("values", "why"),
    [
        # an env var the request didn't ask for
        (PR["deploy/values.yaml"] + "  EXTRA:\n    secret: env\n    key: extra\n", "differs"),
        # one of the request's names fed from another Secret in the namespace
        (
            PR["deploy/values.yaml"].replace("secret: env\n    key: session-key", "secret: db-app\n    key: password"),
            "differs",
        ),
    ],
)
def test_wiring_the_request_didnt_ask_for_is_a_mismatch(values: str, why: str):
    decision = decide(marker_pr({**PR, "deploy/values.yaml": values}), TANUKI, "shikigami")
    assert decision.verdict == Verdict.MISMATCH and why in decision.reason


def test_a_restores_marker_pr_is_merged():
    # D106: what the retirement left (the stop's line, the secrets block of the first onboarding, the marker parked)
    wired = PR["deploy/values.yaml"]
    owner_end = wired.index("owner: mario") + len("owner: mario\n")
    stopped = wired[:owner_end] + ENABLED_LINE + "\n" + wired[owner_end:]
    base = {**BASE, "deploy/values.yaml": stopped}
    decision = decide(marker_pr(base=files(base)), TANUKI, "shikigami")
    assert decision.verdict == Verdict.MERGE, decision.reason
    still_stopped = {**PR, "deploy/values.yaml": stopped}  # a PR that leaves the app stopped isn't the rebuild
    assert decide(marker_pr(still_stopped, base=files(base)), TANUKI, "shikigami").verdict == Verdict.MISMATCH


def test_another_owner_is_a_mismatch():
    head = {**PR, "deploy/meru.yaml": PR["deploy/meru.yaml"].replace("owner: mario", "owner: someone")}
    assert decide(marker_pr(head), TANUKI, "shikigami").verdict == Verdict.MISMATCH


def test_a_repo_that_already_deploys_waits():
    base = {**BASE, "deploy/meru.yaml": PR["deploy/meru.yaml"]}
    decision = decide(marker_pr(base=files(base)), TANUKI, "shikigami")
    assert decision.verdict == Verdict.WAIT and "already has deploy/meru.yaml" in decision.reason


def test_a_removed_app_gets_no_marker():
    removed = TANUKI.model_copy(update={"removed": True})
    assert decide(marker_pr(), removed, "shikigami").verdict == Verdict.WAIT


def test_the_request_gives_owner_and_secret_names():
    text = (
        "apiVersion: meru.clockmost.com/v1\nkind: App\nmetadata:\n  name: tanuki\n"
        "spec:\n  owner: mario\n  description: Test app\n  secrets:\n"
        "    - name: discord-token\n    - name: session-key\n      generate: true\n"
    )
    assert parse(text) == TANUKI
