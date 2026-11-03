"""Write Xolotl's own fields into a request file without touching anything else in it: who approved it
(metadata.approvedBy, D98) and, on a removal, who removed it and when (removedBy/removedAt, D105 - Charon's retention
counts from removedAt). A line edit, so the requester's layout and comments stay, then a check that parsing the result
gives the same request with exactly those fields changed."""

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import yaml


class Unstampable(Exception):
    """The file isn't laid out so that the fields can be written in safely - left for a person."""


_METADATA = re.compile(r"^metadata:[ \t]*$")
_FIELD = re.compile(r"^(?P<indent>[ \t]+)(?P<key>[A-Za-z]+):")
REMOVAL = ("removedBy", "removedAt")


def removal(proposed: str, main: str | None, username: str, now: datetime) -> dict[str, str | None]:
    """removedBy/removedAt for the approved file (None = take it out). Only Xolotl writes them, so what the PR says is
    ignored: a removal keeps main's record if main already has the app removed with a date (an edit never restarts
    the retention), else it's stamped now with the approver; a request that isn't removed (a restore, D106) has
    neither."""
    if not _removed(_load(proposed)):
        return dict.fromkeys(REMOVAL)
    before = _load(main) if main is not None else {}
    found = before.get("metadata")
    meta: dict[str, Any] = found if isinstance(found, dict) else {}
    if _removed(before) and meta.get("removedAt") is not None:
        return {"removedBy": str(meta.get("removedBy") or username), "removedAt": _timestamp(meta["removedAt"])}
    return {"removedBy": username, "removedAt": _timestamp(now)}


def stamp(text: str, fields: Mapping[str, str | None]) -> str:
    """The file with each field as metadata's last lines, in the given order (replacing ones that are there); a field
    set to None is taken out."""
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
    ours = tuple(f"{indent}{key}:" for key in fields)
    kept = [line for line in lines[start + 1 : end] if not line.startswith(ours)]
    if kept and not kept[-1].endswith("\n"):
        kept[-1] += "\n"
    added = [f"{indent}{key}: {value}\n" for key, value in fields.items() if value is not None]
    result = "".join([*lines[: start + 1], *kept, *added, *lines[end:]])
    _check(text, result, fields)
    return result


def _load(text: str) -> dict[str, Any]:
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise Unstampable(f"not YAML: {e}") from e
    return doc if isinstance(doc, dict) else {}


def _removed(doc: dict[str, Any]) -> bool:
    spec = doc.get("spec")
    return isinstance(spec, dict) and spec.get("removed") is True


def _timestamp(value: object) -> str:
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=UTC)
        return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    raise Unstampable(f"removedAt on main isn't a timestamp: {value!r}")


def _check(before: str, after: str, fields: Mapping[str, str | None]) -> None:
    try:
        old, new = yaml.safe_load(before), yaml.safe_load(after)
    except yaml.YAMLError as e:
        raise Unstampable(f"not YAML: {e}") from e
    if not isinstance(old, dict) or not isinstance(old.get("metadata"), dict):
        raise Unstampable("no metadata mapping")
    meta = {k: v for k, v in old["metadata"].items() if k not in fields}
    for key, value in fields.items():
        if value is not None:
            # names stay strings (a username YAML reads as something else is refused); removedAt reads as a datetime
            meta[key] = (
                datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC) if key == "removedAt" else value
            )
    if new != {**old, "metadata": meta}:
        raise Unstampable(f"writing {', '.join(fields)} would change more than those fields")
