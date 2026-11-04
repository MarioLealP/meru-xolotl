# xolotl

Meru's merger - checks and merges Shikigami's trust PRs

Shikigami can only *propose* changes to what the cluster trusts - an app's fence (Meru/meru), signing key + build
exception (Meru/kyverno) and push rule (Meru/zot) - as pull requests. Xolotl decides them (D87):

1. reads the app's request on Meru/requests `main` - it must exist, and `removed` must match onboard / retire;
2. rebuilds, from the PR's own base commit, what every file must hold - its own version of the rules
   (`expected.py`), written apart from Shikigami's so a bug on one side doesn't pass both;
3. merges only a byte-for-byte match, and exactly the commit it checked (`head_commit_id`), then pings Shikigami;
4. a mismatch is never merged: it comments the diff and reports Shikigami to Cú Chulainn (D88).

A person's PR is never touched. A sweep runs at start, when Shikigami pings `POST /ping`, and every 15 minutes.
Until its Gitea bot exists (`XOLOTL_GITEA_TOKEN`), it checks and logs only.

| file | job |
|------|-----|
| `src/xolotl/expected.py` | the trust rules: fence, build exception, key block, push rule, kustomization lines |
| `src/xolotl/app_request.py` | the three request fields the check needs |
| `src/xolotl/check.py` | the verdict for one PR: merge / mismatch / wait / not ours |
| `src/xolotl/gitea.py` | the Gitea calls |
| `src/xolotl/sweep.py` | one pass over the open PRs, acting on each verdict |
| `src/xolotl/app.py` | `/heartbeat`, `/ping`, the sweep loop |

The tests run the check on the nine trust PRs Shikigami really opened (`tests/fixtures/prs/`) and on tampered copies.

## Run locally

```sh
uv sync
uv run uvicorn --app-dir src xolotl.app:app --port 8080
curl localhost:8080/heartbeat   # no token: sweeps read-only and log
```

## Development

```sh
scripts/check.sh   # lint, format, types, file size, lockfile, pip-audit, tests - exactly what CI runs
```

## Deploy

`deploy/meru.yaml` makes this repo a Meru app: [Meru/meru](https://git.clockmost.com/Meru/meru) finds it and deploys it
into namespace `xolotl`. Shikigami adds it once the app's fence, signing key and build credentials exist,
from `deploy/meru.yaml.in` (the template's chart release and check image) with the owner from the request.
`deploy/values.yaml` is the app's whole deployment, rendered with the `nuwa-app` chart from
[Meru/nuwa](https://git.clockmost.com/Meru/nuwa). Merging to `main` runs the checks, builds the image, signs it and rolls
it out; builds run in namespace `xolotl-build` on the runner from [Meru/sindri](https://git.clockmost.com/Meru/sindri).
No secrets live in this repo: they come from OpenBao, through the External Secrets Operator or the build's own login.

## License

MIT - see `LICENSE`.
