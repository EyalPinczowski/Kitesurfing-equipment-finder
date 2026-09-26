#!/usr/bin/env bash
# Step gate: lint + offline tests + coverage. Must pass before moving to the next build step.
set -euo pipefail
cd "$(dirname "$0")/.."
ruff check kitefinder tests
ruff format --check kitefinder tests
python -m pytest --cov=kitefinder --cov-report=term-missing --cov-fail-under=85 "$@"
