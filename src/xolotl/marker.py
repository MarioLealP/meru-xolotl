"""What Shikigami's marker PR must hold (D81, D99): the template's deploy/meru.yaml.in becomes deploy/meru.yaml with
the request's owner, and deploy/values.yaml gets the owner and the request's secrets as env vars from the app's own
OpenBao entry. On a restore (D106) the parked marker is the template again, and the retire stop's `enabled: false`
line comes out of values.yaml. Xolotl's own version of the rules, written apart from Shikigami's (shikigami/marker.py)
like expected.py - SECRETS_COMMENT and the block's layout are the shared contract, byte for byte. Pure text work."""

import re

from xolotl.expected import BadLayout
from xolotl.retired import ENABLED_LINE

SECRETS_COMMENT = (
    "# Secrets from the request (D99): Janus wrote the values to OpenBao apps/meru/<app>/eso/env when the app was"
    " requested."
)
_EMPTY_OWNER = re.compile(r'^owner: ""(?:[ \t]*#[^\n]*)?$', re.MULTILINE)
_SET_KEYS = re.compile(r"^(?:externalSecrets|secretEnv):", re.MULTILINE)
_NAME = re.compile(r"^[a-z]([-a-z0-9]{0,28}[a-z0-9])?$")
_STOPPED = re.compile(rf"^{re.escape(ENABLED_LINE)}\n?", re.MULTILINE)


def owned(text: str, owner: str, path: str) -> str:
    """The one `owner: ""` line (a trailing comment goes with it) set to the owner."""
    filled, count = _EMPTY_OWNER.subn(f"owner: {owner}", text)
    if count != 1:
        raise BadLayout(f'{path}: expected one `owner: ""` line, found {count}')
    return filled


def owner_unset(text: str) -> bool:
    return _EMPTY_OWNER.search(text) is not None


def env_var(name: str) -> str:
    if not _NAME.match(name):  # the request was validated, but this decides an env var: check again
        raise BadLayout(f"secret name {name!r} isn't a request name")
    return name.upper().replace("-", "_")


def wired(text: str, secrets: tuple[str, ...]) -> str:
    """values.yaml with the request's secrets appended: ExternalSecret `env` + one env var per name. Kept if exactly
    that block already ends the file (a restore, D106: the first onboarding wrote it); other wiring is refused."""
    if not secrets:
        return text
    lines = ["", SECRETS_COMMENT, "externalSecrets:", "  env: {}", "secretEnv:"]
    for name in secrets:
        lines.extend((f"  {env_var(name)}:", "    secret: env", f"    key: {name}"))
    block = "\n".join(lines) + "\n"
    if text.endswith(block):
        return text
    if _SET_KEYS.search(text):
        raise BadLayout("deploy/values.yaml already sets externalSecrets or secretEnv")
    return text + ("" if text.endswith("\n") else "\n") + block


def marker_files(template: str, values: str | None, owner: str, secrets: tuple[str, ...]) -> dict[str, str | None]:
    """path -> what it must hold after the marker PR (None = deleted)."""
    want: dict[str, str | None] = {
        "deploy/meru.yaml": owned(template, owner, "deploy/meru.yaml.in"),
        "deploy/meru.yaml.in": None,
    }
    if values is None:
        if secrets:
            raise BadLayout("no deploy/values.yaml to wire the request's secrets into")
        return want
    text = _STOPPED.sub("", values, count=1)  # a restore (D106): the retire stop's exact line goes
    text = owned(text, owner, "deploy/values.yaml") if owner_unset(text) else text
    want["deploy/values.yaml"] = wired(text, secrets)
    return want
