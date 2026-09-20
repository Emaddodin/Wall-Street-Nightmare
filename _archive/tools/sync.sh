#!/usr/bin/env bash
# =============================================================================
# tools/sync.sh
# Synchronize codebase from local Mac to production VPS (stratton: 82.115.21.155)
# Target directory: /root/ict_sniper/
# =============================================================================
#
#   tools/sync.sh                # dry run: preview changes
#   tools/sync.sh --apply        # apply sync to VPS
#   tools/sync.sh --with-dataset --apply
#
set -euo pipefail
cd "$(dirname "$0")/.."

TARGET_HOST="stratton"
TARGET_DIR="/root/ict_sniper/"

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
  --exclude '.git'
  --exclude '__pycache__'
  --exclude '*.pyc'
  --exclude '.env'
  --exclude '.env.*'
  --exclude '.DS_Store'
  --exclude 'data/'
  --exclude 'data_archive/'
  --exclude 'logs/'
  --exclude '.claude'
  --exclude '.agents/'
  --exclude 'venv/'
  --exclude 'kronos_venv/'
  --exclude 'kronos/'
  --exclude 'tls/'
  --exclude 'PAPER_ONLY'
  --exclude 'bot_execution.log'
  --exclude '*.bak*'
  --exclude 'llama.cpp/'
  --exclude 'models/*.gguf'
  --exclude 'scratch/'
  --exclude 'old/'
)

RSYNC=(rsync -avz "${EXCLUDES[@]}")

if [[ -n "$APPLY" ]]; then
  echo "==> Synchronizing codebase to ${TARGET_HOST}:${TARGET_DIR}..."
  "${RSYNC[@]}" ./ "${TARGET_HOST}:${TARGET_DIR}"
  if [[ -n "$WITH_DATA" ]]; then
    echo "==> Synchronizing dataset..."
    rsync -avz data/dataset/ "${TARGET_HOST}:${TARGET_DIR}data/dataset/"
  fi
  ssh "${TARGET_HOST}" "echo 'Sync complete on: '\$(hostname) 'at' \$(date)"
else
  echo "==> DRY RUN: Previewing sync to ${TARGET_HOST}:${TARGET_DIR} (pass --apply to execute)..."
  "${RSYNC[@]}" -n ./ "${TARGET_HOST}:${TARGET_DIR}"
  if [[ -n "$WITH_DATA" ]]; then
    rsync -avzn data/dataset/ "${TARGET_HOST}:${TARGET_DIR}data/dataset/"
  fi
  echo
  echo "(dry run -- pass --apply to push)"
fi
