#!/usr/bin/env bash
# Pack every model contracts/models.lock.json lists, each held to its sha256, into one image and write it to
# models_0, the one model partition of the board on PORT (KEHOACH 4.5.6, 6.1, 6.3). Needs ESP-IDF's environment.
set -euo pipefail
cd "$(dirname "$0")/.."
port="${PORT:-/dev/ttyUSB0}"
baud="${BAUD:-921600}"
image=artifacts/models.bin
uv run python -m srpipe.export.pack_models "$image" --lock
offset=$(python "$IDF_PATH/components/partition_table/parttool.py" --partition-table-file ../firmware/partitions.csv \
  get_partition_info --partition-name models_0 --info offset)
# Written where the flash differs, the rest kept: the image's header gives every model's size (KEHOACH 2.1, 6.3).
python -m esptool --chip esp32s3 --port "$port" --baud "$baud" --after "${AFTER:-hard-reset}" write-flash --skip-flashed \
  "$offset" "$image"
