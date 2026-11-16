"""What an app's trust files must look like (D82, D87).

Its fence in Meru/meru and its push rule in Meru/zot - each added at onboarding, removed at retirement (the signing key
+ build exception in Meru/kyverno went with Kyverno, 2026-11-15). Xolotl's own version of the rules, written apart from
Shikigami's on purpose: a bug on one side shows up as a mismatch instead of passing both. Pure text work; the same real
files are the tests on both sides.
"""

import json
import re


class BadLayoutError(Exception):
    """A trust file doesn't have the shape the rules add to - nothing to compare against."""


def title(app: str) -> str:
    """The app's name with a capital first letter, as the trust files write it."""
    return app[:1].upper() + app[1:]


def fence(app: str, description: str, environments: tuple[str, ...] = ()) -> str:
    """Meru/meru projects/<app>.yaml; each environment (D126) adds namespace <app>-<env> after the build's."""
    names = [app, f"{app}-build"] + [f"{app}-{env}" for env in environments]
    destinations = "".join(f"    - server: https://kubernetes.default.svc\n      namespace: {n}\n" for n in names)
    namespace_kinds = "".join(f'    - {{ group: "", kind: Namespace, name: {n} }}\n' for n in names)
    scope = (
        "into exactly its namespaces - the app's, its build's and its environments' -"
        if environments
        else "into exactly its two namespaces,"
    )
    return f"""# {title(app)}'s fence: where its Application may read from and deploy to - its own repo plus the
# Nüwa and Sindri charts, {scope} which it may also create.
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
{destinations}  clusterResourceWhitelist:
{namespace_kinds}"""


def with_line(kustomization: str, folder: str, path: str) -> str:
    """`  - <path>` right after the last entry from the same folder."""
    entries = list(re.finditer(rf"^  - {re.escape(folder)}/[^\n]+$", kustomization, re.MULTILINE))
    if not entries:
        raise BadLayoutError(f"kustomization.yaml has no `  - {folder}/...` entry")
    end = entries[-1].end()
    return f"{kustomization[:end]}\n  - {path}{kustomization[end:]}"


def without_line(kustomization: str, path: str) -> str:
    """The kustomization without its `  - <path>` line."""
    return re.sub(rf"^  - {re.escape(path)}\n", "", kustomization, count=1, flags=re.MULTILINE)


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
        raise BadLayoutError("config.json has no http.accessControl.repositories")
    return parsed, repositories


def with_push_rule(config: str, app: str) -> str:
    """Zot's config.json with the app's push rule; BadLayoutError if it already has one."""
    parsed, repositories = _repositories(config)
    if f"meru/{app}" in repositories:
        raise BadLayoutError(f"config.json already has a rule for meru/{app}")
    repositories[f"meru/{app}"] = push_rule(app)
    return json.dumps(parsed, indent=2) + "\n"


def without_push_rule(config: str, app: str) -> str:
    """Zot's config.json without the app's push rule."""
    parsed, repositories = _repositories(config)
    repositories.pop(f"meru/{app}", None)
    return json.dumps(parsed, indent=2) + "\n"
