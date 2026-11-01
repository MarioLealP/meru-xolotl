"""What an app's trust files must look like (D82, D87): its fence in Meru/meru, its signing key + build exception in
Meru/kyverno, its push rule in Meru/zot - each added at onboarding, removed at retirement. Xolotl's own version of
the rules, written apart from Shikigami's on purpose: a bug on one side shows up as a mismatch instead of passing
both. Pure text work; the same real files are the tests on both sides."""

import base64
import json
import re

import yaml


class BadLayout(Exception):
    """A trust file doesn't have the shape the rules add to - nothing to compare against."""


def title(app: str) -> str:
    return app[:1].upper() + app[1:]


def fence(app: str, description: str) -> str:
    """Meru/meru projects/<app>.yaml."""
    return f"""# {title(app)}'s fence: where its Application may read from and deploy to - its own repo plus the
# Nüwa and Sindri charts, into exactly its two namespaces, which it may also create. Written by Shikigami (D82).
apiVersion: argoproj.io/v1alpha1
kind: AppProject
metadata:
  name: meru-{app}
  namespace: argocd
spec:
  description: {json.dumps(f"{title(app)} - {description}", ensure_ascii=False)}
  sourceRepos:
    - https://git.clockmost.com/Meru/{app}.git
    - https://git.clockmost.com/Meru/nuwa.git
    - https://git.clockmost.com/Meru/sindri.git
  destinations:
    - server: https://kubernetes.default.svc
      namespace: {app}
    - server: https://kubernetes.default.svc
      namespace: {app}-build
  clusterResourceWhitelist:
    - {{ group: "", kind: Namespace, name: {app} }}
    - {{ group: "", kind: Namespace, name: {app}-build }}
"""


def build_exception(app: str) -> str:
    """Meru/kyverno exceptions/<app>-build.yaml."""
    return f"""apiVersion: policies.kyverno.io/v1
kind: PolicyException
metadata:
  name: {app}-build
  namespace: kyverno
  annotations:
    meru.clockmost.com/reason: >-
      {title(app)}'s build namespace (D32). Its job pods run rootless BuildKit, which needs privilege escalation,
      SETUID/SETGID, SELinux container_engine_t and a custom seccomp profile - Pod Security "baseline", not
      "restricted". Its check pods and the app (namespace {app}) stay restricted. Every other rule still applies.
      Written by Shikigami (D82).
spec:
  policyRefs:
    - name: require-restricted-namespaces
      kind: ValidatingPolicy
  matchConditions:
    - name: {app}-build-namespace-only
      expression: "object.metadata.name == '{app}-build'"
"""


def with_line(kustomization: str, folder: str, path: str) -> str:
    """`  - <path>` right after the last entry from the same folder."""
    entries = list(re.finditer(rf"^  - {re.escape(folder)}/[^\n]+$", kustomization, re.MULTILINE))
    if not entries:
        raise BadLayout(f"kustomization.yaml has no `  - {folder}/...` entry")
    end = entries[-1].end()
    return f"{kustomization[:end]}\n  - {path}{kustomization[end:]}"


def without_line(kustomization: str, path: str) -> str:
    return re.sub(rf"^  - {re.escape(path)}\n", "", kustomization, count=1, flags=re.MULTILINE)


# A cosign key: ECDSA P-256 in SubjectPublicKeyInfo (DER) - this fixed header, then the 65-byte uncompressed point.
P256_HEADER = bytes.fromhex("3059301306072a8648ce3d020106082a8648ce3d030107034200")


def public_key(pem: str) -> str:
    """The key's PEM if it is exactly one P-256 public key, else ValueError: the one value Xolotl can't rebuild, so
    it is checked for shape - the rest of the file is rebuilt around it."""
    lines = pem.strip().splitlines()
    if len(lines) < 3 or lines[0] != "-----BEGIN PUBLIC KEY-----" or lines[-1] != "-----END PUBLIC KEY-----":
        raise ValueError("not a PEM public key")
    try:
        der = base64.b64decode("".join(lines[1:-1]), validate=True)
    except ValueError as e:
        raise ValueError("the key isn't base64") from e
    if len(der) != len(P256_HEADER) + 65 or not der.startswith(P256_HEADER) or der[len(P256_HEADER)] != 4:
        raise ValueError("not an ECDSA P-256 public key")
    return "\n".join(lines) + "\n"


def keys_data(signing_keys: str) -> dict:
    data = (yaml.safe_load(signing_keys) or {}).get("data")
    if not isinstance(data, dict):
        raise BadLayout("keys/meru-signing-keys.yaml has no data section")
    return data


def with_key(signing_keys: str, app: str, key: str) -> str:
    """The app's key block appended - `data` is the file's last section."""
    if app in keys_data(signing_keys):
        raise BadLayout(f"keys/meru-signing-keys.yaml already has a key for {app}")
    block = f"  {app}: |\n" + "".join(f"    {line}\n" for line in key.splitlines())
    return signing_keys.rstrip("\n") + "\n" + block


def without_key(signing_keys: str, app: str) -> str:
    return re.sub(rf"^  {re.escape(app)}: \|\n(?:    [^\n]*\n)*", "", signing_keys, count=1, flags=re.MULTILINE)


def push_rule(app: str) -> dict:
    """Anyone reads meru/<app>; only its builder (its Zot login) pushes."""
    return {
        "anonymousPolicy": ["read"],
        "defaultPolicy": ["read"],
        "policies": [{"users": [f"{app}-builder"], "actions": ["read", "create", "update"]}],
    }


def _repositories(config: str) -> tuple[dict, dict]:
    parsed = json.loads(config)
    repositories = parsed.get("http", {}).get("accessControl", {}).get("repositories")
    if not isinstance(repositories, dict):
        raise BadLayout("config.json has no http.accessControl.repositories")
    return parsed, repositories


def with_push_rule(config: str, app: str) -> str:
    parsed, repositories = _repositories(config)
    if f"meru/{app}" in repositories:
        raise BadLayout(f"config.json already has a rule for meru/{app}")
    repositories[f"meru/{app}"] = push_rule(app)
    return json.dumps(parsed, indent=2) + "\n"


def without_push_rule(config: str, app: str) -> str:
    parsed, repositories = _repositories(config)
    repositories.pop(f"meru/{app}", None)
    return json.dumps(parsed, indent=2) + "\n"
