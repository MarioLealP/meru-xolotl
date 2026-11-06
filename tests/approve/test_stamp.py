from datetime import UTC, datetime

import pytest

from xolotl.approve.stamp import UnstampableError, removal, stamp

A = {"approvedBy": "akadmin"}
NOW = datetime(2026, 11, 3, 9, 30, 5, tzinfo=UTC)

APP = """apiVersion: meru.clockmost.com/v1
kind: App
metadata:
  name: golem  # never changes
spec:
  description: x
"""


def test_approved_by_goes_last_in_metadata_and_nothing_else_moves():
    assert stamp(APP, A) == APP.replace("never changes\n", "never changes\n  approvedBy: akadmin\n")


def test_an_earlier_approved_by_is_replaced():
    once = stamp(APP, {"approvedBy": "someone"})
    assert stamp(once, A) == stamp(APP, A)


def test_metadata_at_the_end_without_a_final_newline():
    text = "kind: App\nmetadata:\n  name: golem"
    assert stamp(text, A) == "kind: App\nmetadata:\n  name: golem\n  approvedBy: akadmin\n"


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("kind: App\n", "no top-level metadata"),
        ("metadata: {name: golem}\n", "no top-level metadata"),  # flow style: left for a person
        ("metadata:\nspec: {}\n", "has no fields"),
        ("metadata:\n  name: golem\n    labels: x\n", "one indent"),
    ],
)
def test_layouts_it_cant_edit_safely_are_refused(text: str, why: str):
    with pytest.raises(UnstampableError, match=why):
        stamp(text, A)


def test_a_username_yaml_would_read_as_something_else_is_refused():
    with pytest.raises(UnstampableError, match="more than those fields"):
        stamp(APP, {"approvedBy": "true"})


REMOVED = APP.replace("spec:\n", "spec:\n  removed: true\n")
STAMPED = {"removedBy": "akadmin", "removedAt": "2026-11-03T09:30:05Z"}


def test_a_removal_is_stamped_with_who_and_when():
    fields = removal(REMOVED, APP, "akadmin", NOW)
    assert fields == STAMPED
    text = stamp(REMOVED, {**A, **fields})
    assert "  approvedBy: akadmin\n  removedBy: akadmin\n  removedAt: 2026-11-03T09:30:05Z\nspec:" in text


def test_an_edit_of_a_removed_request_keeps_mains_date():
    main = stamp(REMOVED, {**A, **STAMPED})
    later = datetime(2026, 11, 20, tzinfo=UTC)
    assert removal(REMOVED, main, "someone", later) == STAMPED  # the retention never restarts
    forged = REMOVED.replace("  name: golem", "  name: golem\n  removedAt: 2030-01-01T00:00:00Z")
    assert removal(forged, main, "someone", later) == STAMPED  # what the PR says doesn't count


def test_a_removed_request_without_a_date_on_main_is_stamped_now():
    assert removal(REMOVED, REMOVED, "akadmin", NOW) == STAMPED  # the apps removed before D105


def test_a_restore_takes_both_out():
    main = stamp(REMOVED, {**A, **STAMPED})
    restored = main.replace("  removed: true\n", "")
    fields = removal(restored, main, "akadmin", NOW)
    assert fields == {"removedBy": None, "removedAt": None}
    text = stamp(restored, {**A, **fields})
    assert "removed" not in text
    assert "  approvedBy: akadmin\nspec:" in text
