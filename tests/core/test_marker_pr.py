"""Shikigami's marker PR in a new app's own repo (D99). The base is the real nuwa-python template's deploy files; the PR
contents were written by Shikigami's own marker.py - so this checks Xolotl's rules against Shikigami's, not itself."""

from pathlib import Path

import pytest

from xolotl.core.check import Proposal, Verdict, decide, parse_title, paths
from xolotl.core.retired import ENABLED_LINE
from xolotl.models.app_request import AppRequest, parse

F = Path(__file__).parent.parent / "fixtures" / "marker-tanuki"
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
    assert t is not None
    assert (t.direction, t.app, t.what) == ("onboard", "tanuki", "marker")
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
    assert decision.verdict == Verdict.MISMATCH
    assert why in decision.reason


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
    assert decision.verdict == Verdict.WAIT
    assert "already has deploy/meru.yaml" in decision.reason


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


def test_a_change_entry_in_the_request_is_read():
    text = (
        "apiVersion: meru.clockmost.com/v1\nkind: App\nmetadata:\n  name: tanuki\n"
        "spec:\n  owner: mario\n  description: Test app\n  secrets:\n"
        "    - name: discord-token\n      entry: env-20261105-a1b2c3\n    - name: session-key\n"
    )
    assert parse(text).entries == (("discord-token", "env-20261105-a1b2c3"),)


@pytest.mark.parametrize("entry", ["env-", "other", "env-20261105-A1B2C3", "env-20261105-a1b2c3/x"])
def test_an_entry_that_isnt_env_or_a_change_name_is_refused(entry: str):
    text = (
        "apiVersion: meru.clockmost.com/v1\nkind: App\nmetadata:\n  name: tanuki\n"
        f"spec:\n  owner: mario\n  description: Test app\n  secrets:\n    - name: a\n      entry: {entry}\n"
    )
    with pytest.raises(ValueError, match="entry"):
        parse(text)


def test_a_restore_after_a_change_rebuilds_shikigamis_block_byte_for_byte():
    # The same text Shikigami's test_a_restore_after_a_change_wires_each_name_to_its_entry expects (the contract).
    from xolotl.core.marker import SECRETS_COMMENT, wired

    want = (
        "name: echo\nowner: mario\n\n" + SECRETS_COMMENT + "\n"
        "externalSecrets:\n  env: {}\n  env-20261105-a1b2c3: {}\n"
        "secretEnv:\n"
        "  DISCORD_TOKEN:\n    secret: env\n    key: discord-token\n"
        "  API_KEY:\n    secret: env-20261105-a1b2c3\n    key: api-key\n"
    )
    entries = (("api-key", "env-20261105-a1b2c3"),)
    assert wired("name: echo\nowner: mario\n", ("discord-token", "api-key"), entries) == want
    assert wired(want, ("discord-token", "api-key"), entries) == want  # already there: kept


def test_a_marker_pr_wired_to_a_change_entry_needs_the_request_to_say_so():
    moved = (("discord-token", "env-20261105-a1b2c3"),)
    head = dict(PR)
    head["deploy/values.yaml"] = (
        PR["deploy/values.yaml"]
        .replace("  env: {}\n", "  env-20261105-a1b2c3: {}\n  env: {}\n")
        .replace(
            "    secret: env\n    key: discord-token\n", "    secret: env-20261105-a1b2c3\n    key: discord-token\n"
        )
    )
    assert decide(marker_pr(head), TANUKI.model_copy(update={"entries": moved}), "shikigami").verdict == Verdict.MERGE
    assert decide(marker_pr(head), TANUKI, "shikigami").verdict != Verdict.MERGE  # the request doesn't move it


DSN = "https://0123456789abcdef0123456789abcdef@glitchtip.clockmost.com/7"
# The block Shikigami's marker.with_glitchtip writes (the contract - its tests/test_glitchtip.py expects the same text).
GLITCHTIP_BLOCK = (
    "\n# Crash reports (D125): this app's GlitchTip project, which Shikigami made at onboarding.\n"
    f"glitchtip:\n  dsn: {DSN}\n"
)


def with_dsn(dsn: str) -> dict[str, str]:
    """The PR as Shikigami writes it with a DSN: owner, then the glitchtip block, then the secrets."""
    values = PR["deploy/values.yaml"]
    at = values.index("\n# Secrets from the request")
    return {**PR, "deploy/values.yaml": values[:at] + GLITCHTIP_BLOCK.replace(DSN, dsn) + values[at:]}


def test_a_marker_pr_with_our_glitchtip_dsn_is_merged():
    decision = decide(marker_pr(with_dsn(DSN)), TANUKI, "shikigami")
    assert decision.verdict == Verdict.MERGE, decision.reason


@pytest.mark.parametrize(
    "dsn",
    [
        "https://0123456789abcdef0123456789abcdef@glitchtip.evil.example/7",  # someone else's GlitchTip
        "https://0123456789abcdef0123456789abcdef@glitchtip.clockmost.com.evil.example/7",
        "http://0123456789abcdef0123456789abcdef@glitchtip.clockmost.com/7",  # not TLS
        "https://not-a-key@glitchtip.clockmost.com/7",
    ],
)
def test_a_dsn_that_isnt_ours_is_a_mismatch(dsn: str):
    decision = decide(marker_pr(with_dsn(dsn)), TANUKI, "shikigami")
    assert decision.verdict == Verdict.MISMATCH
    assert "isn't one of ours" in decision.reason


def test_the_glitchtip_block_is_shikigamis_text():
    from xolotl.core.marker import with_glitchtip

    assert with_glitchtip("name: echo\nowner: mario\n", DSN) == "name: echo\nowner: mario\n" + GLITCHTIP_BLOCK
    assert with_glitchtip("glitchtip:\n  dsn: ''\n", DSN) == "glitchtip:\n  dsn: ''\n"  # the owner's own: kept
