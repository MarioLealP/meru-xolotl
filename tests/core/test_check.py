import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from xolotl.core.check import Decision, Proposal, Verdict, decide, parse_title, paths
from xolotl.models.app_request import AppRequest, parse

FIXTURES = Path(__file__).parent.parent / "fixtures"
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
    return Proposal(**(dict(pr) | change))  # rebuilt, not model_copy: the altered fields are validated too


def verdict(pr: Proposal, request: AppRequest | None) -> Decision:
    return decide(pr, request, shikigami="shikigami")


@pytest.mark.parametrize(
    ("pr", "request_"),
    [
        ("meru-21", GOLEM),
        ("zot-4", GOLEM),
        ("meru-23", GOLEM_REMOVED),
        ("zot-5", GOLEM_REMOVED),
        ("meru-25", XOLOTL),
        ("zot-6", XOLOTL),
    ],
)
def test_every_real_trust_pr_shikigami_opened_is_merged(pr: str, request_: AppRequest):
    # golem's onboarding + retirement and Xolotl's own onboarding, byte for byte as Gitea has them
    decision = verdict(real_pr(pr), request_)
    assert decision.verdict == Verdict.MERGE, decision.reason


XOLOTL_ENVS = XOLOTL.model_copy(update={"environments": ("uat", "prod")})


def test_a_fence_with_environments_as_shikigami_writes_it_is_merged():
    # tests/fixtures/meru-xolotl-fence-environments.yaml is Shikigami's trust.fence("xolotl", ..., ["uat", "prod"])
    pr = real_pr("meru-25")
    fence = (FIXTURES / "meru-xolotl-fence-environments.yaml").read_text()
    with_envs = real_pr("meru-25", head={**pr.head, "projects/xolotl.yaml": fence})
    assert verdict(with_envs, XOLOTL_ENVS).verdict == Verdict.MERGE
    assert verdict(with_envs, XOLOTL).verdict == Verdict.MISMATCH  # environments the request doesn't have
    assert verdict(pr, XOLOTL_ENVS).verdict == Verdict.MISMATCH  # the request's environments left out


@pytest.mark.parametrize(
    "title",
    [
        "chore(trust): onboard golem - signing key",  # Shikigami's real Meru/kyverno#8, #9, #10 (before 2026-11-15)
        "chore(trust): retire golem - signing key",
        "chore(trust): onboard xolotl - signing key",
    ],
)
def test_a_signing_key_pr_is_no_longer_shikigamis_since_kyverno_went(title: str):
    assert parse_title(title) is None


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


def test_a_push_rule_for_more_users_is_a_mismatch():
    pr = real_pr("zot-6")
    config = pr.head["config.json"]
    assert config is not None
    wider = config.replace('"xolotl-builder"', '"xolotl-builder",\n                "shikigami"')
    decision = verdict(real_pr("zot-6", head={"config.json": wider}), XOLOTL)
    assert decision.verdict == Verdict.MISMATCH


def test_a_title_for_another_repo_is_a_mismatch():
    decision = verdict(real_pr("meru-25", title="chore(trust): onboard xolotl - push rule"), XOLOTL)
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
    with pytest.raises(ValidationError):
        parse("kind: Database\n")


def test_a_request_with_environments_reads_their_names_in_order():
    text = (FIXTURES / "request-xolotl.yaml").read_text() + "  environments: [{name: uat}, {name: prod, route: {}}]\n"
    assert parse(text).environments == ("uat", "prod")
    assert parse((FIXTURES / "request-xolotl.yaml").read_text()).environments == ()


@pytest.mark.parametrize("name", ["Prod", "production", "u-at", "", "1uat"])
def test_an_environment_name_that_cant_be_a_namespace_suffix_is_refused(name: str):
    text = (FIXTURES / "request-xolotl.yaml").read_text() + f"  environments: [{{name: '{name}'}}]\n"
    with pytest.raises(ValidationError):
        parse(text)


@pytest.mark.parametrize("config", ["{not json", "[]", '{"http": []}', '{"http": {"accessControl": 3}}'])
def test_a_push_rule_base_that_isnt_zots_config_waits_instead_of_raising(config: str):
    pr = real_pr("zot-6")
    pr = pr.model_copy(update={"base": {**pr.base, "config.json": config}})
    decision = verdict(pr, XOLOTL)
    assert decision.verdict == Verdict.WAIT
    assert "can't rebuild" in decision.reason
