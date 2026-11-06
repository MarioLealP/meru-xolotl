"""Mario's approval of one request PR (D98): an ID token only Xolotl can redeem, bound to the PR it approves.

The token comes from Authentik's xolotl-approvals app.

The atlas sends Mario's browser to Authentik with prompt=none and nonce = nonce(...) below; Authentik answers with a
one-time code; Janus passes the code on; Xolotl redeems it with its own client secret (redeem) and checks the ID token
(verify). The nonce names the repo, the PR, its head commit and the decision, so a token for one click can't approve
another PR, a later push to the same PR, or turn a Deny into an Approve.
"""

import hashlib
import re
from typing import Literal

import httpx2
import jwt
from pydantic import PrivateAttr, SecretStr

from xolotl.models.base import Incoming, Service, Value

Decision = Literal["approve", "deny"]
# Usernames go into the request file as metadata.approvedBy - plain YAML scalars only, never anything to escape.
USERNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,149}$")


class RefusedError(Exception):
    """The approval doesn't hold: a bad or reused code, a token that fails a check, someone not allowed."""


class Approver(Value):
    """Who approved, from the verified ID token."""

    username: str
    token_id: str  # the ID token's jti, for the commit message: which sign-in approved it


class OidcClient(Value):
    """The xolotl-approvals app at Authentik (projects/authentik/blueprints/meru-approvals.yaml)."""

    issuer: str
    client_id: str
    client_secret: SecretStr
    redirect_uri: str
    group: str  # only its members may approve

    @property
    def token_url(self) -> str:
        """Authentik's token endpoint, the same for all its apps."""
        return re.sub(r"/application/o/[^/]+/$", "/application/o/token/", self.issuer)

    @property
    def jwks_url(self) -> str:
        """The public keys of this app."""
        return f"{self.issuer}jwks/"


class _TokenAnswer(Incoming):
    id_token: str


def nonce(org: str, repo: str, pr: int, head: str, decision: Decision) -> str:
    """What the atlas asks Authentik to sign into the ID token for this one click."""
    return hashlib.sha256(f"{org}/{repo}#{pr}@{head}:{decision}".encode()).hexdigest()


async def redeem(http: httpx2.AsyncClient, client: OidcClient, code: str) -> str:
    """The one-time code -> the ID token, straight from Authentik (a code works once, for a minute)."""
    response = await http.post(
        client.token_url,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": client.redirect_uri,
            "client_id": client.client_id,
            "client_secret": client.client_secret.get_secret_value(),
        },
    )
    if response.status_code in (400, 401):
        raise RefusedError("Authentik didn't accept the approval code (used, expired or not ours)")
    response.raise_for_status()
    return _TokenAnswer.model_validate_json(response.content).id_token


class Keys(Service):
    """Authentik's public keys for the app, fetched when a token names a key not seen yet (a rotation)."""

    http: httpx2.AsyncClient
    url: str
    _known: dict[str, jwt.PyJWK] = PrivateAttr(default_factory=dict)  # the only thing here that changes

    async def get(self, kid: str) -> jwt.PyJWK:
        """The key named `kid`; RefusedError if Authentik doesn't publish it."""
        if kid not in self._known:
            response = await self.http.get(self.url)
            response.raise_for_status()
            published = {k.key_id: k for k in jwt.PyJWKSet.from_dict(response.json()).keys if k.key_id}
            self._known.clear()  # a key Authentik stopped publishing goes
            self._known.update(published)
        if kid not in self._known:
            raise RefusedError("the approval token is signed with a key Authentik doesn't publish for this app")
        return self._known[kid]


async def verify(id_token: str, keys: Keys, client: OidcClient, expected_nonce: str) -> Approver:
    """Every check that makes the token Mario's approval of this PR, or RefusedError."""
    try:
        kid = jwt.get_unverified_header(id_token).get("kid")
    except jwt.PyJWTError as e:
        raise RefusedError(f"not a JWT: {e}") from e
    if not isinstance(kid, str):
        raise RefusedError("the approval token names no signing key")
    key = await keys.get(kid)
    try:
        claims = jwt.decode(
            id_token,
            key,
            algorithms=["RS256"],  # never what the token itself asks for
            audience=client.client_id,
            issuer=client.issuer,
            options={"require": ["exp", "iat", "iss", "aud", "nonce", "jti"]},
        )
    except jwt.PyJWTError as e:
        raise RefusedError(f"the approval token fails a check: {e}") from e
    if claims["nonce"] != expected_nonce:
        raise RefusedError("the approval was given for something else (another PR, commit or decision)")
    groups = claims.get("groups")
    if not isinstance(groups, list) or client.group not in groups:
        raise RefusedError(f"the approver isn't in {client.group}")
    username = claims.get("preferred_username")
    if not isinstance(username, str) or not USERNAME.fullmatch(username):
        raise RefusedError("the approval token carries no usable username")
    return Approver(username=username, token_id=str(claims["jti"]))
