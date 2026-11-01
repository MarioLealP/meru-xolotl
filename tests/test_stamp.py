import pytest

from xolotl.stamp import Unstampable, stamp

APP = """apiVersion: meru.clockmost.com/v1
kind: App
metadata:
  name: golem  # never changes
spec:
  description: x
"""


def test_approvedBy_goes_last_in_metadata_and_nothing_else_moves():
    assert stamp(APP, "akadmin") == APP.replace("never changes\n", "never changes\n  approvedBy: akadmin\n")


def test_an_earlier_approvedBy_is_replaced():
    once = stamp(APP, "someone")
    assert stamp(once, "akadmin") == stamp(APP, "akadmin")


def test_metadata_at_the_end_without_a_final_newline():
    text = "kind: App\nmetadata:\n  name: golem"
    assert stamp(text, "akadmin") == "kind: App\nmetadata:\n  name: golem\n  approvedBy: akadmin\n"


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
    with pytest.raises(Unstampable, match=why):
        stamp(text, "akadmin")


def test_a_username_yaml_would_read_as_something_else_is_refused():
    with pytest.raises(Unstampable, match="more than that one field"):
        stamp(APP, "true")
