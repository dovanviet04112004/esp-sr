#!/usr/bin/env bash
# Pack every model contracts/models.lock.json lists, each held to its sha256, into one image and write it to
# models_0, the one model partition of the board on PORT (KEHOACH 4.5.6, 6.1, 6.3). Needs ESP-IDF's environment.
set -euo pipefail
cd "$(dirname "$0")/.."
port="${PORT:-/dev/ttyUSB0}"
image=artifacts/models.bin
uv run python -m srpipe.export.pack_models "$image" --lock
python "$IDF_PATH/components/partition_table/parttool.py" --port "$port" \
  --partition-table-file ../firmware/partitions.csv write_partition --partition-name models_0 --input "$image"
