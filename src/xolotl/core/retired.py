"""What an app's own deploy files must look like when it's retired (D85, cm-konk): the stop, then the parked marker.

Xolotl's own version of the rules, written apart from Shikigami's (shikigami/retire.py) like expected.py - the two
comment lines below are the shared contract, byte for byte. Pure text work, line by line, keeping every comment.
"""

import re

from xolotl.core.expected import BadLayoutError

ENABLED_LINE = "enabled: false # retired (D85): no workload until Charon purges the namespace"
BUILD_LINE = "build: false # retired (D85): no build namespace or runner"

_ENABLED = re.compile(r"enabled: (true|false)\b")
_BUILD = re.compile(r"build: (true|false)\b")
_OWNER = re.compile(r"owner: ")


def _replace_first(text: str, starts: re.Pattern[str], new: str) -> str | None:
    """`text` with the first top-level line matching `starts` replaced by `new` (its line ending kept), or None."""
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if starts.match(line):  # the whole line goes, a CR with it: only the newline stays (as in Shikigami)
            lines[i] = new + ("\n" if line.endswith("\n") else "")
            return "".join(lines)
    return None


def stopped_values(values: str) -> str:
    """deploy/values.yaml with `enabled: false`: the existing line changed, else a new one right after `owner:`."""
    if (changed := _replace_first(values, _ENABLED, ENABLED_LINE)) is not None:
        return changed
    lines = values.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if _OWNER.match(line):  # the new line goes right after the owner line's text, before its line ending
            body = line[:-1] if line.endswith("\n") else line
            lines[i] = f"{body}\n{ENABLED_LINE}" + ("\n" if line.endswith("\n") else "")
            return "".join(lines)
    raise BadLayoutError("deploy/values.yaml: no top-level owner line")


def stopped_marker(marker: str) -> str:
    """deploy/meru.yaml with `build: false`."""
    if (changed := _replace_first(marker, _BUILD, BUILD_LINE)) is None:
        raise BadLayoutError("deploy/meru.yaml: no `build:` line")
    return changed


def _has_line(text: str, pattern: str) -> bool:
    return any(re.match(pattern, line) for line in text.splitlines())


def is_disabled(values: str) -> bool:
    """Whether a values file carries top-level `enabled: false`."""
    return _has_line(values, r"enabled: false\b")


def is_stopped(values: str, marker: str) -> bool:
    """Whether both files carry the stop: top-level `enabled: false` and `build: false`."""
    return is_disabled(values) and _has_line(marker, r"build: false\b")


def parked(marker: str) -> str:
    """deploy/meru.yaml back into the template's deploy/meru.yaml.in: owner emptied, build back on."""
    owner_emptied = _replace_first(marker, _OWNER, 'owner: ""')
    if owner_emptied is None:
        raise BadLayoutError("deploy/meru.yaml: no owner line")
    built = _replace_first(owner_emptied, _BUILD, "build: true")
    if built is None:
        raise BadLayoutError("deploy/meru.yaml: no build line")
    return built
