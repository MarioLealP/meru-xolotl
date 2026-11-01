"""Write who approved into a request file (metadata.approvedBy, D98) without touching anything else in it: a line
edit, so the requester's layout and comments stay, then a check that parsing the result gives the same request plus
that one field."""

import re

import yaml


class Unstampable(Exception):
    """The file isn't laid out so that the approver can be written in safely - left for a person."""


_METADATA = re.compile(r"^metadata:[ \t]*$")
_FIELD = re.compile(r"^(?P<indent>[ \t]+)(?P<key>[A-Za-z]+):")


def stamp(text: str, username: str) -> str:
    """The file with `approvedBy: <username>` as metadata's last field (replacing one that's there)."""
    lines = text.splitlines(keepends=True)
    try:
        start = next(i for i, line in enumerate(lines) if _METADATA.match(line))
    except StopIteration as e:
        raise Unstampable("no top-level metadata: block") from e
    end, indent = start + 1, None
    while end < len(lines):
        match = _FIELD.match(lines[end])
        if lines[end].strip() and not match:
            break  # the next top-level key (or something metadata doesn't hold)
        if match:
            if indent is None:
                indent = match["indent"]
            elif match["indent"] != indent:
                raise Unstampable("metadata's fields aren't on one indent")
        end += 1
    if indent is None:
        raise Unstampable("metadata: has no fields")
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1  # keep blank lines after the block where they are
    kept = [line for line in lines[start + 1 : end] if not line.startswith(f"{indent}approvedBy:")]
    if kept and not kept[-1].endswith("\n"):
        kept[-1] += "\n"
    new = [*lines[: start + 1], *kept, f"{indent}approvedBy: {username}\n", *lines[end:]]
    result = "".join(new)
    _check(text, result, username)
    return result


def _check(before: str, after: str, username: str) -> None:
    try:
        old, new = yaml.safe_load(before), yaml.safe_load(after)
    except yaml.YAMLError as e:
        raise Unstampable(f"not YAML: {e}") from e
    if not isinstance(old, dict) or not isinstance(old.get("metadata"), dict):
        raise Unstampable("no metadata mapping")
    want = {**old, "metadata": {**old["metadata"], "approvedBy": username}}
    if new != want:
        raise Unstampable("writing approvedBy would change more than that one field")
