#!/usr/bin/env bash
# Byte-exact mirror, Mac -> server. Dry run by default.
#
# Code, docs, tests and service units are pushed; data/ is NOT, because it
# holds live state on both sides (the book, the scout, the recorder's own
# files) and syncing it either way would overwrite one machine's truth with
# the other's snapshot. The dataset outputs travel with --with-dataset.
#
#   tools/sync.sh                show what would change
#   tools/sync.sh --apply        push it
#   tools/sync.sh --with-dataset --apply
set -euo pipefail
cd "$(dirname "$0")/.."

APPLY=""
WITH_DATA=""
for a in "$@"; do
  case "$a" in
    --apply) APPLY=1 ;;
    --with-dataset) WITH_DATA=1 ;;
    *) echo "unknown flag: $a" >&2; exit 1 ;;
  esac
done

EXCLUDES=(
  --exclude '.git' --exclude '__pycache__' --exclude '*.pyc'
  --exclude '.env' --exclude '.env.bak.*' --exclude '.DS_Store'
  --exclude 'data/' --exclude 'logs' --exclude '.claude'
  --exclude 'scratch/' --exclude 'old/'
)

RSYNC=(rsync -a --delete "${EXCLUDES[@]}")

if [[ -n "$APPLY" ]]; then
  "${RSYNC[@]}" ./ tbt:/home/tbt/bot/
  if [[ -n "$WITH_DATA" ]]; then
    rsync -a data/dataset/ tbt:/home/tbt/bot/data/dataset/
  fi
  ssh tbt "chown -R tbt:tbt /home/tbt/bot && echo synced: \$(hostname)"
else
  "${RSYNC[@]}" -n ./ tbt:/home/tbt/bot/ | head -60
  if [[ -n "$WITH_DATA" ]]; then
    rsync -an data/dataset/ tbt:/home/tbt/bot/data/dataset/ | head -20
  fi
  echo
  echo "(dry run -- pass --apply to push, --with-dataset to include the"
  echo " regenerated data/dataset outputs)"
fi
