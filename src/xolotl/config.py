"""Xolotl's settings, from XOLOTL_* environment variables (Nüwa's env + secretEnv)."""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="XOLOTL_")

    gitea_url: str = "https://git.clockmost.com"
    org: str = "Meru"
    trust_repos: tuple[str, ...] = ("meru", "kyverno", "zot")
    requests_repo: str = "requests"  # private: read with the token (the bot is a Read collaborator)
    shikigami_user: str = "shikigami"  # whose PRs are checked; anyone else's are a person's to merge
    # Xolotl's Gitea bot (D87): Write on the trust repos, Read on requests. Until the admin run makes it, Xolotl
    # checks and logs but merges, comments and reads requests nothing - every PR waits.
    gitea_token: SecretStr | None = None

    # Who hears about it: Shikigami after a merge (D84), Cú Chulainn on a mismatch (D88 - not built yet: the request
    # fails and is logged, nothing more).
    shikigami_ping_url: str = "http://shikigami.shikigami.svc/ping"
    cuchulainn_url: str = "http://cuchulainn.cuchulainn.svc/quarantine"

    # Approvals (D98): the xolotl-approvals app at Authentik (meru-approvals.yaml). Its client secret comes from
    # OpenBao (meru-oidc-secret.sh -> apps/meru/xolotl/eso/approvals-oidc); without it /approve answers 503.
    approvals_issuer: str = "https://auth.clockmost.com/application/o/xolotl-approvals/"
    approvals_client_id: str = "xolotl-approvals"
    approvals_client_secret: SecretStr | None = None
    approvals_redirect_uri: str = "https://atlas.clockmost.com/api/janus/approve/callback"
    approvers_group: str = "meru-approvers"
    shikigami_validate_url: str = "http://shikigami.shikigami.svc/validate"  # the final file is checked once more

    sweep_seconds: int = 900  # the backstop when no ping comes
    http_timeout: float = 20.0
