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

    sweep_seconds: int = 900  # the backstop when no ping comes
    http_timeout: float = 20.0
