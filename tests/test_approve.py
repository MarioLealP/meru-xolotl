import base64
import json
import time
from urllib.parse import parse_qs, unquote

import httpx2
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm
from pydantic import SecretStr

from xolotl.approval import OidcClient, nonce
from xolotl.approve import Approvals, Ask, Failed
from xolotl.gitea import Gitea

ISSUER = "https://auth.test/application/o/xolotl-approvals/"
HEAD = "a" * 40
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
REQUEST = """# the requester's own comment stays
apiVersion: meru.clockmost.com/v1
kind: App
metadata:
  name: golem

spec:
  owner: mario
  description: Test app
"""
CLIENT = OidcClient(
    issuer=ISSUER,
    client_id="xolotl-approvals",
    client_secret=SecretStr("s3cret"),
    redirect_uri="https://atlas.test/api/janus/approve/callback",
    group="meru-approvers",
)


def jwks() -> dict:
    public = json.loads(RSAAlgorithm.to_jwk(KEY.public_key()))
    return {"keys": [public | {"kid": "k1", "alg": "RS256", "use": "sig"}]}


def id_token(
    pr: int = 7, head: str = HEAD, decision: str = "approve", key: rsa.RSAPrivateKey = KEY, **over: object
) -> str:
    claims = {
        "iss": ISSUER,
        "aud": "xolotl-approvals",
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
        "jti": "tok-1",
        "nonce": nonce("Meru", "requests", pr, head, decision),  # ty: ignore[invalid-argument-type]
        "preferred_username": "akadmin",
        "groups": ["clockmost-admins", "meru-approvers"],
    } | over
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "k1"})


class Fake:
    """Authentik (token + JWKS), Gitea (Meru/requests PR #7) and Shikigami's /validate."""

    def __init__(self, token: str, head: str = HEAD, state: str = "open", problems: list[str] | None = None) -> None:
        self.token, self.head, self.state, self.problems = token, head, state, problems or []
        self.redeemed: list[dict[str, list[str]]] = []
        self.writes: list[dict] = []
        self.comments: list[str] = []
        self.closed = False
        self.validated: list[dict] = []
        self.code_ok = True

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        url, path, method = request.url, unquote(request.url.path), request.method
        if url.host == "auth.test":
            if path.endswith("/jwks/"):
                return httpx2.Response(200, json=jwks())
            self.redeemed.append(parse_qs(request.content.decode()))
            if not self.code_ok:
                return httpx2.Response(400, json={"error": "invalid_grant"})
            return httpx2.Response(200, json={"id_token": self.token, "access_token": "x", "token_type": "Bearer"})
        if url.host == "shikigami.test":
            self.validated.append(json.loads(request.content))
            return httpx2.Response(200, json={"commit": "main1", "problems": self.problems})
        assert request.headers["authorization"] == "token tok"  # private repo: always the bot
        tail = path.removeprefix("/api/v1/repos/Meru/requests")
        if tail == "/pulls/7" and method == "GET":
            body = "Request from Janus.\n\nRequested-By: agent-claude\nChecked-Against: main0"
            pull: dict[str, object] = {"number": 7, "state": self.state, "merged": False, "body": body}
            pull |= {"title": "feat(request): add app golem", "user": {"login": "janus"}}
            pull |= {"base": {"ref": "main"}, "head": {"sha": self.head}}
            return httpx2.Response(200, json=pull)
        if tail == "/pulls/7/files":
            return httpx2.Response(200, json=[{"filename": "apps/golem.yaml"}])
        if tail == "/raw/apps/golem.yaml" and url.params["ref"] == HEAD:
            return httpx2.Response(200, text=REQUEST)
        if tail == "/contents/apps/golem.yaml" and method == "GET":
            return httpx2.Response(404)  # a new app: not on main yet
        if tail == "/contents/apps/golem.yaml" and method == "POST":
            self.writes.append(json.loads(request.content))
            return httpx2.Response(201, json={"commit": {"sha": "c0ffee"}, "content": {}})
        if tail == "/issues/7/comments" and method == "POST":
            self.comments.append(json.loads(request.content)["body"])
            return httpx2.Response(201, json={})
        if tail == "/pulls/7" and method == "PATCH":
            self.closed = json.loads(request.content) == {"state": "closed"}
            return httpx2.Response(201, json={})
        return httpx2.Response(500, text=f"unexpected {method} {path}")


async def run(fake: Fake, decision: str = "approve", head: str = HEAD):
    http = httpx2.AsyncClient(transport=httpx2.MockTransport(fake.handler))
    approvals = Approvals(
        Gitea(http, "https://git.test", "Meru", "tok"),
        http,
        CLIENT,
        "Meru",
        "requests",
        "http://shikigami.test/validate",
    )
    return await approvals.handle(Ask(pr=7, head=head, decision=decision, code="one-time"))  # ty: ignore[invalid-argument-type]


def written(fake: Fake) -> str:
    [write] = fake.writes
    return base64.b64decode(write["content"]).decode()


async def test_an_approval_writes_the_file_with_approvedBy_to_main_and_closes_the_pr():
    fake = Fake(id_token())
    done = await run(fake)
    assert (done.decision, done.by, done.commit) == ("approve", "akadmin", "c0ffee")
    [form] = fake.redeemed
    assert form["code"] == ["one-time"] and form["client_secret"] == ["s3cret"]
    text = written(fake)
    assert "  name: golem\n  approvedBy: akadmin\n\nspec:" in text
    assert text.startswith("# the requester's own comment stays\n")  # the file is edited, not rewritten
    assert fake.validated == [{"path": "apps/golem.yaml", "content": text}]  # Shikigami saw exactly what went in
    message = fake.writes[0]["message"]
    assert message.startswith("feat(request): approve apps/golem.yaml (#7)")
    assert "Requested-By: agent-claude\nApproved-By: akadmin\nApproval-Token: tok-1" in message
    assert fake.writes[0]["branch"] == "main"
    assert fake.comments == ["Approved by akadmin in the atlas - on main in c0ffee."] and fake.closed


async def test_a_denial_closes_the_pr_and_writes_nothing():
    fake = Fake(id_token(decision="deny"))
    done = await run(fake, decision="deny")
    assert (done.decision, done.commit) == ("deny", None)
    assert fake.writes == [] and fake.comments == ["Denied by akadmin in the atlas."] and fake.closed


@pytest.mark.parametrize(
    ("token", "why"),
    [
        (id_token(pr=8), "for something else"),  # another PR
        (id_token(head="b" * 40), "for something else"),  # an older push of the same PR
        (id_token(decision="deny"), "for something else"),  # a Deny click can't approve
        (id_token(groups=["clockmost-admins"]), "isn't in meru-approvers"),
        (id_token(aud="temporal"), "fails a check"),  # another app's token, same Authentik key
        (id_token(iss="https://auth.test/application/o/atlas/"), "fails a check"),
        (id_token(exp=int(time.time()) - 60), "fails a check"),
        (id_token(key=OTHER_KEY), "fails a check"),  # not signed by Authentik's key
        (
            jwt.encode(
                {"nonce": "x"},
                "an HMAC secret, not the key Authentik signs with",
                algorithm="HS256",
                headers={"kid": "k1"},
            ),
            "fails a check",
        ),
        (id_token(preferred_username="true\nx"), "no usable username"),
    ],
)
async def test_anything_but_marios_yes_to_this_exact_pr_is_refused_and_nothing_happens(token: str, why: str):
    fake = Fake(token)
    with pytest.raises(Failed, match=why) as e:
        await run(fake)
    assert e.value.status == 403
    assert fake.writes == [] and fake.comments == [] and not fake.closed


async def test_a_code_authentik_wont_redeem_is_refused():
    fake = Fake(id_token())
    fake.code_ok = False
    with pytest.raises(Failed, match="didn't accept the approval code") as e:
        await run(fake)
    assert e.value.status == 403 and fake.writes == []


async def test_a_pr_that_moved_after_the_click_waits_for_a_new_look():
    fake = Fake(id_token(), head="c" * 40)  # pushed again after Mario looked
    with pytest.raises(Failed, match="moved on") as e:
        await run(fake)
    assert e.value.status == 409 and fake.writes == [] and not fake.closed


async def test_a_closed_pr_cant_be_approved():
    with pytest.raises(Failed, match="already closed") as e:
        await run(Fake(id_token(), state="closed"))
    assert e.value.status == 409


async def test_shikigamis_problems_on_todays_main_stop_the_write():
    fake = Fake(id_token(), problems=["apps/golem.yaml: host taken by apps/other.yaml"])
    with pytest.raises(Failed, match="host taken") as e:
        await run(fake)
    assert e.value.status == 422 and fake.writes == [] and not fake.closed
