"""What Shikigami's marker PR must hold (D81, D99).

The template's deploy/meru.yaml.in becomes deploy/meru.yaml with the request's owner, and deploy/values.yaml gets the
owner and the request's secrets as env vars from the app's own OpenBao entry. On a restore (D106) the parked marker is
the template again, and the retire stop's `enabled: false` line comes out of values.yaml. Xolotl's own version of the
rules, written apart from Shikigami's (shikigami/marker.py) like expected.py - SECRETS_COMMENT and the block's layout
are the shared contract, byte for byte. Pure text work.
"""

import re

from xolotl.expected import BadLayoutError
from xolotl.retired import ENABLED_LINE

SECRETS_COMMENT = (
    "# Secrets from the request (D99): Janus wrote the values to OpenBao apps/meru/<app>/eso/env when the app was"
    " requested."
)
GLITCHTIP_COMMENT = "# Crash reports (D125): this app's GlitchTip project, which Shikigami made at onboarding."
# The DSN Shikigami adds is the one value Xolotl can't rebuild (GlitchTip draws it): it must have GlitchTip's shape and
# point at our GlitchTip, so a marker PR can't send an app's crash reports anywhere else.
DSN = re.compile(r"^https://[0-9a-f]{32}@glitchtip\.clockmost\.com/[0-9]+$")
_DSN_LINE = re.compile(r"^glitchtip:\n  dsn: ([^\n]*)$", re.MULTILINE)
_GLITCHTIP_SET = re.compile(r"^glitchtip:", re.MULTILINE)
_EMPTY_OWNER = re.compile(r'^owner: ""(?:[ \t]*#[^\n]*)?$', re.MULTILINE)
_SET_KEYS = re.compile(r"^(?:externalSecrets|secretEnv):", re.MULTILINE)
_NAME = re.compile(r"^[a-z]([-a-z0-9]{0,28}[a-z0-9])?$")
_STOPPED = re.compile(rf"^{re.escape(ENABLED_LINE)}\n?", re.MULTILINE)


def owned(text: str, owner: str, path: str) -> str:
    """The one `owner: ""` line (a trailing comment goes with it) set to the owner."""
    filled, count = _EMPTY_OWNER.subn(f"owner: {owner}", text)
    if count != 1:
        raise BadLayoutError(f'{path}: expected one `owner: ""` line, found {count}')
    return filled


def owner_unset(text: str) -> bool:
    """Whether the file still has an empty `owner: ""` line."""
    return _EMPTY_OWNER.search(text) is not None


def env_var(name: str) -> str:
    """The env var a secret's name becomes; BadLayoutError if it isn't a request name."""
    if not _NAME.match(name):  # the request was validated, but this decides an env var: check again
        raise BadLayoutError(f"secret name {name!r} isn't a request name")
    return name.upper().replace("-", "_")


def dsn_line(values: str | None) -> str | None:
    """The DSN a values.yaml's `glitchtip:` block names, unchecked; None without one."""
    found = _DSN_LINE.search(values or "")
    return found.group(1) if found else None


def with_glitchtip(text: str, dsn: str | None) -> str:
    """values.yaml with the app's GlitchTip DSN appended (D125); kept when there's none or the file sets glitchtip."""
    if dsn is None or _GLITCHTIP_SET.search(text):
        return text
    block = "\n".join(["", GLITCHTIP_COMMENT, "glitchtip:", f"  dsn: {dsn}"]) + "\n"
    return text + ("" if text.endswith("\n") else "\n") + block


def wired(text: str, secrets: tuple[str, ...], entries: tuple[tuple[str, str], ...] = ()) -> str:
    """values.yaml with the request's secrets appended: one ExternalSecret per OpenBao entry + one env var per name.

    `entries` pairs the names a change moved off `env` with their entry (D123); with none it is the D99 block. Each
    entry used is listed once, first use first. Kept if exactly that block already ends the file (a restore, D106: the
    first onboarding wrote it); other wiring is refused.
    """
    if not secrets:
        return text
    moved = dict(entries)
    entry = {name: moved.get(name, "env") for name in secrets}
    lines = ["", SECRETS_COMMENT, "externalSecrets:"]
    lines.extend(f"  {used}: {{}}" for used in dict.fromkeys(entry.values()))
    lines.append("secretEnv:")
    for name in secrets:
        lines.extend((f"  {env_var(name)}:", f"    secret: {entry[name]}", f"    key: {name}"))
    block = "\n".join(lines) + "\n"
    if text.endswith(block):
        return text
    if _SET_KEYS.search(text):
        raise BadLayoutError("deploy/values.yaml already sets externalSecrets or secretEnv")
    return text + ("" if text.endswith("\n") else "\n") + block


def marker_files(
    template: str,
    values: str | None,
    owner: str,
    secrets: tuple[str, ...],
    entries: tuple[tuple[str, str], ...] = (),
    dsn: str | None = None,
) -> dict[str, str | None]:
    """Each path and what it must hold after the marker PR (None = deleted). `dsn`: the PR's, already checked."""
    want: dict[str, str | None] = {
        "deploy/meru.yaml": owned(template, owner, "deploy/meru.yaml.in"),
        "deploy/meru.yaml.in": None,
    }
    if values is None:
        if secrets:
            raise BadLayoutError("no deploy/values.yaml to wire the request's secrets into")
        return want
    text = _STOPPED.sub("", values, count=1)  # a restore (D106): the retire stop's exact line goes
    text = owned(text, owner, "deploy/values.yaml") if owner_unset(text) else text
    want["deploy/values.yaml"] = wired(with_glitchtip(text, dsn), secrets, entries)
    return want
