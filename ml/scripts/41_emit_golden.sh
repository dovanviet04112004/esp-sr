#!/usr/bin/env bash
# Emit every golden set of srpipe into contracts/golden/ (KEHOACH 3.14); extra arguments go to the emitter.
set -euo pipefail
cd "$(dirname "$0")/.."
uv run python -m srpipe.dsp.emit_golden "$@"
uv run python -m srpipe.lang.emit_golden "$@"
uv run python -m srpipe.tasks.command.kws.postproc.decide "$@"
