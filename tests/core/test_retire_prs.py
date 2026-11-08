"""Shikigami's stop and park PRs in an app's own repo (cm-konk). The base is homunculus's real deploy files; the PR
contents were written by Shikigami's own retire.py - so this checks Xolotl's rules against Shikigami's, not itself."""

from pathlib import Path

import pytest

from xolotl.core.check import Proposal, Verdict, decide, parse_title, paths
from xolotl.models.app_request import AppRequest

F = Path(__file__).parent.parent / "fixtures" / "retire-homunculus"
BASE = {p: (F / "base" / p).read_text() for p in ("deploy/values.yaml", "deploy/meru.yaml")}
STOPPED = {**BASE, **{p: (F / "stop" / p).read_text() for p in ("deploy/values.yaml", "deploy/meru.yaml")}}
PARKED_MARKER = (F / "park" / "deploy/meru.yaml.in").read_text()
REMOVED = AppRequest(name="homunculus", description="Test app", removed=True)
PROD = "deploy/envs/prod/values.yaml"  # D126; the stopped one is Shikigami's retire.stopped_values on the base
PROD_BASE, PROD_STOPPED = (F / "base" / PROD).read_text(), (F / "stop" / PROD).read_text()
REMOVED_ENVS = REMOVED.model_copy(update={"environments": ("uat", "prod")})
LIVE = AppRequest(name="homunculus", description="Test app", removed=False)


def files(on: dict[str, str], what: str) -> dict[str, str | None]:
    return {path: on.get(path) for path in paths("homunculus", what)}


def stop_pr(**change) -> Proposal:
    pr = {
        "repo": "homunculus",
        "number": 2,
        "title": "chore(retire): stop homunculus",
        "author": "shikigami",
        "changed": frozenset({"deploy/values.yaml", "deploy/meru.yaml"}),
        "base": files(BASE, "stop"),
        "head": files(STOPPED, "stop"),
    }
    return Proposal.model_validate(pr | change)


def park_pr(**change) -> Proposal:
    head = {**STOPPED, "deploy/meru.yaml.in": PARKED_MARKER}
    del head["deploy/meru.yaml"]
    pr = {
        "repo": "homunculus",
        "number": 3,
        "title": "chore(retire): park homunculus",
        "author": "shikigami",
        "changed": frozenset({"deploy/meru.yaml", "deploy/meru.yaml.in"}),
        "base": files(STOPPED, "park"),
        "head": files(head, "park"),
    }
    return Proposal.model_validate(pr | change)


def test_retire_titles_parse_and_belong_in_the_apps_own_repo():
    t = parse_title("chore(retire): park homunculus")
    assert t is not None
    assert (t.direction, t.app, t.what) == ("retire", "homunculus", "park")
    assert decide(stop_pr(repo="meru"), REMOVED, "shikigami").verdict == Verdict.MISMATCH  # not in a trust repo


@pytest.mark.parametrize("pr", [stop_pr(), park_pr()], ids=["stop", "park"])
def test_shikigamis_stop_and_park_are_merged_for_a_removed_app(pr: Proposal):
    decision = decide(pr, REMOVED, "shikigami")
    assert decision.verdict == Verdict.MERGE, decision.reason


def test_retiring_an_app_whose_request_is_live_waits():
    decision = decide(stop_pr(), LIVE, "shikigami")
    assert decision.verdict == Verdict.WAIT
    assert "isn't marked removed" in decision.reason


def test_a_park_before_the_stop_is_on_main_waits():
    # parked first, Argo would drop the Application while its pods still run (preserveResourcesOnDeletion)
    decision = decide(park_pr(base=files(BASE, "park")), REMOVED, "shikigami")
    assert decision.verdict == Verdict.WAIT
    assert "isn't stopped on main yet" in decision.reason


def test_a_stop_that_also_changes_the_image_is_a_mismatch():
    head = files(STOPPED, "stop")
    head["deploy/values.yaml"] = (head["deploy/values.yaml"] or "") + "image:\n  tag: evil\n"
    assert decide(stop_pr(head=head), REMOVED, "shikigami").verdict == Verdict.MISMATCH


def test_a_stop_touching_the_code_is_a_mismatch():
    changed = frozenset({"deploy/values.yaml", "deploy/meru.yaml", "src/app.py"})
    decision = decide(stop_pr(changed=changed), REMOVED, "shikigami")
    assert decision.verdict == Verdict.MISMATCH
    assert "src/app.py" in decision.reason


def env_files(on: dict[str, str], what: str) -> dict[str, str | None]:
    return {path: on.get(path) for path in paths("homunculus", what, ("uat", "prod"))}


def test_a_stop_turns_each_environment_off_with_the_app():
    base, head = {**BASE, PROD: PROD_BASE}, {**STOPPED, PROD: PROD_STOPPED}  # no uat values: nothing to stop there
    pr = stop_pr(
        changed=frozenset({"deploy/values.yaml", "deploy/meru.yaml", PROD}),
        base=env_files(base, "stop"),
        head=env_files(head, "stop"),
    )
    assert decide(pr, REMOVED_ENVS, "shikigami").verdict == Verdict.MERGE
    assert decide(pr, REMOVED, "shikigami").verdict == Verdict.MISMATCH  # prod isn't in the request
    left_on = stop_pr(base=env_files(base, "stop"), head=env_files({**head, PROD: PROD_BASE}, "stop"))
    assert decide(left_on, REMOVED_ENVS, "shikigami").verdict == Verdict.MISMATCH  # prod still running


def test_a_park_waits_for_every_environment_to_stop():
    head = {**STOPPED, "deploy/meru.yaml.in": PARKED_MARKER}
    del head["deploy/meru.yaml"]
    running = park_pr(base=env_files({**STOPPED, PROD: PROD_BASE}, "park"), head=env_files(head, "park"))
    decision = decide(running, REMOVED_ENVS, "shikigami")
    assert decision.verdict == Verdict.WAIT
    assert PROD in decision.reason
    stopped = park_pr(base=env_files({**STOPPED, PROD: PROD_STOPPED}, "park"), head=env_files(head, "park"))
    assert decide(stopped, REMOVED_ENVS, "shikigami").verdict == Verdict.MERGE
