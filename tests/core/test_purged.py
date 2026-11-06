from datetime import UTC, datetime

import pytest

from xolotl.core.check import Verdict
from xolotl.core.purged import PurgeProposal, decide, title

NOW = datetime(2026, 12, 10, tzinfo=UTC)
REMOVED = """apiVersion: meru.clockmost.com/v1
kind: App
metadata:
  name: golem
  removedBy: mario
  removedAt: 2026-11-01T10:00:00Z
spec:
  description: Test app
  removed: true
"""


def proposal(**changes: object) -> PurgeProposal:
    fields: dict[str, object] = {
        "title": title("golem"),
        "author": "charon",
        "changed": frozenset({"apps/golem.yaml"}),
        "on_main": REMOVED,
        "at_base": REMOVED,
        "at_head": None,
    }
    return PurgeProposal.model_validate({**fields, **changes})


def test_deleting_a_removed_request_after_its_retention_is_merged():
    decision = decide(proposal(), "charon", NOW)
    assert decision.verdict == Verdict.MERGE
    assert "retention over since 2026-12-01" in decision.reason


def test_someone_elses_pr_is_left_alone():
    assert decide(proposal(author="shikigami"), "charon", NOW).verdict == Verdict.NOT_OURS


@pytest.mark.parametrize(
    ("changes", "why"),
    [
        ({"title": "chore(purge): delete golem"}, "a title Charon doesn't write"),
        ({"changed": frozenset({"apps/golem.yaml", "apps/tanuki.yaml"})}, "not only apps/golem.yaml"),
        ({"title": title("tanuki")}, "not only apps/tanuki.yaml"),
        ({"at_head": REMOVED}, "edits apps/golem.yaml instead of deleting it"),
        ({"on_main": REMOVED.replace("removed: true", "removed: false")}, "golem isn't removed"),
        ({"on_main": REMOVED.replace("  removedAt: 2026-11-01T10:00:00Z\n", "")}, "has no removedAt"),
    ],
)
def test_anything_but_deleting_one_removed_request_is_a_mismatch(changes: dict, why: str):
    on_main = changes.get("on_main")
    if on_main is not None:
        changes = {**changes, "at_base": on_main}  # main didn't move: the content itself is what's wrong
    decision = decide(proposal(**changes), "charon", NOW)
    assert decision.verdict == Verdict.MISMATCH
    assert why in decision.reason


def test_before_the_retention_runs_out_it_waits():
    decision = decide(proposal(), "charon", datetime(2026, 11, 20, tzinfo=UTC))
    assert decision.verdict == Verdict.WAIT
    assert decision.reason == "retention runs to 2026-12-01T10:00:00Z"


def test_a_longer_retention_in_the_request_is_honoured():
    longer = REMOVED.replace("  removed: true\n", "  removed: true\n  retention: 60d\n")
    decision = decide(proposal(on_main=longer, at_base=longer), "charon", NOW)
    assert decision.verdict == Verdict.WAIT
    assert decision.reason == "retention runs to 2026-12-31T10:00:00Z"


def test_a_request_that_changed_on_main_since_the_pr_waits():
    decision = decide(proposal(at_base=REMOVED.replace("Test app", "Old text")), "charon", NOW)
    assert decision.verdict == Verdict.WAIT
    assert "changed on main" in decision.reason


def test_a_request_already_gone_from_main_waits():
    assert decide(proposal(on_main=None), "charon", NOW).verdict == Verdict.WAIT
