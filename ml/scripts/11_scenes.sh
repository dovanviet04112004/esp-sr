#!/usr/bin/env bash
# Build a labelled scene set into interim/scenes/<name>/ (E4-T4, KEHOACH 4.4.1); the default is configs/scenes/standard.yaml.
set -euo pipefail
cd "$(dirname "$0")/.."
uv run python -m srpipe.scenes.room "$@"
