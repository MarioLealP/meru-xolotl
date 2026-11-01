import json
from pathlib import Path

import pytest

from xolotl.app_request import AppRequest, parse
from xolotl.check import Decision, Proposal, Verdict, decide, parse_title, paths

FIXTURES = Path(__file__).parent / "fixtures"
GOLEM = AppRequest(name="golem", description="Test app - every onboarding step runs on it first", removed=False)
GOLEM_REMOVED = AppRequest(name="golem", description=GOLEM.description, removed=True)
XOLOTL = parse((FIXTURES / "request-xolotl.yaml").read_text())


def files(side: Path, wanted: tuple[str, ...]) -> dict[str, str | None]:
    return {path: (side / path).read_text() if (side / path).exists() else None for path in wanted}


def real_pr(name: str, **change) -> Proposal:
    """A PR Shikigami really opened (tests/fixtures/prs/<repo>-<n>: its files at base and head), optionally altered."""
    d = FIXTURES / "prs" / name
    meta = json.loads((d / "pr.json").read_text())
    t = parse_title(meta["title"])
    assert t is not None
    wanted = paths(t.app, t.what)
    pr = Proposal(
        repo=meta["repo"],
        number=meta["number"],
        title=meta["title"],
        author="shikigami",
        changed=frozenset(meta["changed"]),
        base=files(d / "base", wanted),
        head=files(d / "head", wanted),
    )
    return Proposal(**{**pr.__dict__, **change})


def verdict(pr: Proposal, request: AppRequest | None) -> Decision:
    return decide(pr, request, shikigami="shikigami")


@pytest.mark.parametrize(
    ("pr", "request_"),
    [
        ("meru-21", GOLEM),
        ("kyverno-8", GOLEM),
        ("zot-4", GOLEM),
        ("meru-23", GOLEM_REMOVED),
        ("kyverno-9", GOLEM_REMOVED),
        ("zot-5", GOLEM_REMOVED),
        ("meru-25", XOLOTL),
        ("kyverno-10", XOLOTL),
        ("zot-6", XOLOTL),
    ],
)
def test_every_real_trust_pr_shikigami_opened_is_merged(pr: str, request_: AppRequest):
    # golem's onboarding + retirement and Xolotl's own onboarding, byte for byte as Gitea has them
    decision = verdict(real_pr(pr), request_)
    assert decision.verdict == Verdict.MERGE, decision.reason


def test_a_persons_pr_is_left_alone():
    assert verdict(real_pr("meru-25", author="MarioGLealP"), XOLOTL).verdict == Verdict.NOT_OURS


def test_a_fence_with_one_more_namespace_is_a_mismatch():
    pr = real_pr("meru-25")
    fence = pr.head["projects/xolotl.yaml"]
    assert fence is not None
    wider = fence.replace(
        "      namespace: xolotl-build\n", "      namespace: xolotl-build\n    - server: x\n      namespace: kyverno\n"
    )
    decision = verdict(real_pr("meru-25", head={**pr.head, "projects/xolotl.yaml": wider}), XOLOTL)
    assert decision.verdict == Verdict.MISMATCH
    assert "projects/xolotl.yaml" in decision.reason


def test_touching_another_file_is_a_mismatch():
    pr = real_pr("meru-25", changed=frozenset({"kustomization.yaml", "projects/xolotl.yaml", "projects/echo.yaml"}))
    decision = verdict(pr, XOLOTL)
    assert decision.verdict == Verdict.MISMATCH
    assert "projects/echo.yaml" in decision.reason


def test_a_key_under_another_apps_name_is_a_mismatch():
    pr = real_pr("kyverno-10")
    keys = pr.head["keys/meru-signing-keys.yaml"]
    assert keys is not None
    swapped = keys.replace("  xolotl: |\n", "  echo2: |\n")
    decision = verdict(real_pr("kyverno-10", head={**pr.head, "keys/meru-signing-keys.yaml": swapped}), XOLOTL)
    assert decision.verdict == Verdict.MISMATCH


def test_a_key_that_isnt_a_p256_public_key_is_a_mismatch():
    pr = real_pr("kyverno-10")
    keys = pr.head["keys/meru-signing-keys.yaml"]
    assert keys is not None
    bogus = keys.replace("MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE/SE5", "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAA/SE5")
    decision = verdict(real_pr("kyverno-10", head={**pr.head, "keys/meru-signing-keys.yaml": bogus}), XOLOTL)
    assert decision.verdict == Verdict.MISMATCH
    assert "P-256" in decision.reason


def test_a_retire_that_also_drops_another_apps_key_is_a_mismatch():
    pr = real_pr("kyverno-9")
    keys = pr.head["keys/meru-signing-keys.yaml"]
    assert keys is not None
    start = keys.index("  echo: |\n")
    end = keys.index("  thoth: |\n")
    decision = verdict(
        real_pr("kyverno-9", head={**pr.head, "keys/meru-signing-keys.yaml": keys[:start] + keys[end:]}), GOLEM_REMOVED
    )
    assert decision.verdict == Verdict.MISMATCH


def test_a_push_rule_for_more_users_is_a_mismatch():
    pr = real_pr("zot-6")
    config = pr.head["config.json"]
    assert config is not None
    wider = config.replace('"xolotl-builder"', '"xolotl-builder",\n                "shikigami"')
    decision = verdict(real_pr("zot-6", head={"config.json": wider}), XOLOTL)
    assert decision.verdict == Verdict.MISMATCH


def test_a_title_for_another_repo_is_a_mismatch():
    decision = verdict(real_pr("meru-25", title="onboard xolotl: push rule"), XOLOTL)
    assert decision.verdict == Verdict.MISMATCH


@pytest.mark.parametrize(
    ("pr", "request_", "why"),
    [
        ("meru-25", None, "no request"),
        ("meru-21", GOLEM_REMOVED, "marked removed"),
        ("meru-23", GOLEM, "isn't marked removed"),
    ],
)
def test_no_request_behind_it_waits_without_blaming_anyone(pr: str, request_: AppRequest | None, why: str):
    decision = verdict(real_pr(pr), request_)
    assert decision.verdict == Verdict.WAIT
    assert why in decision.reason


def test_a_base_without_the_kustomization_waits():
    pr = real_pr("meru-25")
    decision = verdict(real_pr("meru-25", base={**pr.base, "kustomization.yaml": None}), XOLOTL)
    assert decision.verdict == Verdict.WAIT


def test_the_request_fixtures_parse():
    golem = parse((FIXTURES / "request-golem.yaml").read_text())
    assert (golem.name, golem.removed) == ("golem", True)
    assert (XOLOTL.name, XOLOTL.removed) == ("xolotl", False)
    with pytest.raises(ValueError):
        parse("kind: Database\n")
