#!/usr/bin/env bash
# Every check CI runs, runnable locally: lint, format, types, the lockfile, tests.
set -euo pipefail
cd "$(dirname "$0")/.."

step() { printf '\n== %s\n' "$*"; }

step ruff
uv run ruff check .
uv run ruff format --check .
step ty
uv run ty check
step "uv.lock up to date"
uv lock --check
step pytest
uv run pytest -q
printf '\nall checks passed\n'
