#!/usr/bin/env bash
# Fetch the corpus of data/manifests/<kind>/<name>.yaml into raw/<kind>/<name>/ of SRPIPE_DATA_ROOT, each archive
# checked against its published checksum (KEHOACH 1.2, 4.4.1); only this script and host/session.py write raw/.
# Usage: scripts/10_prepare.sh <kind>/<name>, e.g. speech/common_voice_vi. KEEP_ARCHIVES=1 keeps the archives.
set -euo pipefail
cd "$(dirname "$0")/.."
env_value() { awk -v k="$1" 'index($0, k "=") == 1 { print substr($0, length(k) + 2) }' .env; }
manifest_value() { python3 -c 'import sys, yaml; print(yaml.safe_load(open(sys.argv[1]))[sys.argv[2]])' "$@"; }
json_value() { python3 -c 'import json, sys; print(json.load(sys.stdin)[sys.argv[1]])' "$1"; }
DATA_ROOT="${SRPIPE_DATA_ROOT:-$(env_value SRPIPE_DATA_ROOT)}"
MDC_API="${MDC_API_URL:-https://mozilladatacollective.com/api}"

# Resumes an interrupted download, checks it, prints its sha256 for the manifest, unpacks it.
fetch() {
  local url=$1 dir=$2 file=$3 algo=$4 sum=$5
  curl -fL -C - --retry 5 -o "$dir/$file" "$url"
  if [ "$algo" != "-" ]; then echo "$sum  $dir/$file" | "${algo}sum" -c -; fi
  echo "sha256 $(sha256sum "$dir/$file" | cut -d' ' -f1)  $file"
  case "$file" in
    *.zip) unzip -q -o "$dir/$file" -d "$dir" ;;
    *.tar.gz | *.tgz) tar -xzf "$dir/$file" -C "$dir" ;;
  esac
  if [ "${KEEP_ARCHIVES:-0}" != 1 ] && [[ "$file" == *.zip || "$file" == *.tar.gz || "$file" == *.tgz ]]; then
    rm "$dir/$file"
  fi
}

# Mozilla Data Collective signs a fresh URL per request; the terms are accepted once on its website.
mdc_fetch() {
  local manifest=$1 dir=$2 key meta
  key="${MDC_API_KEY:-$(env_value MDC_API_KEY)}"
  meta=$(curl -sf -X POST -H "Authorization: Bearer $key" \
    "$MDC_API/datasets/$(manifest_value "$manifest" mdc_dataset_id)/download")
  fetch "$(json_value downloadUrl <<<"$meta")" "$dir" "$(json_value filename <<<"$meta")" sha256 \
    "$(json_value checksum <<<"$meta")"
}

# files: [{url, name, md5 | sha256}] in the manifest; a file without a published checksum gets its sha256 recorded.
files_fetch() {
  local manifest=$1 dir=$2
  python3 -c '
import sys, yaml
for f in yaml.safe_load(open(sys.argv[1])).get("files", []):
    algo = next((a for a in ("sha256", "md5") if a in f), "-")
    print(f["url"], f["name"], algo, f.get(algo, "-"), sep="\t")' "$manifest" |
    while IFS=$'\t' read -r url file algo sum; do fetch "$url" "$dir" "$file" "$algo" "$sum"; done
}

target=${1:?usage: $0 <kind>/<name> of a manifest under data/manifests}
manifest=data/manifests/$target.yaml
test -f "$manifest" || { echo "no $manifest" >&2; exit 2; }
dir="$DATA_ROOT/raw/$target"
mkdir -p "$dir"
if grep -q '^mdc_dataset_id:' "$manifest"; then mdc_fetch "$manifest" "$dir"; else files_fetch "$manifest" "$dir"; fi
echo "$target is in $dir"
