#!/usr/bin/env bash
# =============================================================================
# deploy/install_llama.sh
# Installation script for llama-server Linux binary and Qwen2.5-Coder-1.5B GGUF model
# Target Environment: Ubuntu 24.04 LTS x86_64 VPS (82.115.21.155)
# =============================================================================

set -euo pipefail

INSTALL_DIR="${1:-/root/ict_sniper}"
LLAMA_DIR="${INSTALL_DIR}/llama.cpp"
MODELS_DIR="${INSTALL_DIR}/models"
LLAMA_VERSION="b11009"
MODEL_FILENAME="qwen2.5-coder-1.5b-instruct-q4_k_m.gguf"
MODEL_URL="https://huggingface.co/Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF/resolve/main/qwen2.5-coder-1.5b-instruct-q4_k_m.gguf"
LLAMA_RELEASE_URL="https://github.com/ggml-org/llama.cpp/releases/download/${LLAMA_VERSION}/llama-${LLAMA_VERSION}-bin-ubuntu-x64.tar.gz"

echo "=== [1/4] Preparing directories in ${INSTALL_DIR} ==="
mkdir -p "${LLAMA_DIR}"
mkdir -p "${MODELS_DIR}"

echo "=== [2/4] Downloading prebuilt llama-server binary (${LLAMA_VERSION}) ==="
TMP_TAR="$(mktemp /tmp/llama-bin-XXXXXX.tar.gz)"
TMP_EXTRACT="$(mktemp -d /tmp/llama-extract-XXXXXX)"

if command -v curl >/dev/null 2>&1; then
    curl -fL --retry 3 --retry-delay 2 -o "${TMP_TAR}" "${LLAMA_RELEASE_URL}"
elif command -v wget >/dev/null 2>&1; then
    wget -q --tries=3 -O "${TMP_TAR}" "${LLAMA_RELEASE_URL}"
else
    echo "ERROR: Neither curl nor wget is available." >&2
    exit 1
fi

tar -xzf "${TMP_TAR}" -C "${TMP_EXTRACT}"

# Locate llama-server binary in extracted contents
SERVER_BIN="$(find "${TMP_EXTRACT}" -name "llama-server" -type f | head -n 1)"
if [ -z "${SERVER_BIN}" ]; then
    echo "ERROR: llama-server binary not found in downloaded release archive." >&2
    exit 1
fi

cp "${SERVER_BIN}" "${LLAMA_DIR}/llama-server"
chmod +x "${LLAMA_DIR}/llama-server"

# Copy any associated shared libraries (.so) if present
find "${TMP_EXTRACT}" -name "*.so*" -type f -exec cp -u {} "${LLAMA_DIR}/" \; 2>/dev/null || true

rm -rf "${TMP_TAR}" "${TMP_EXTRACT}"
echo "✓ llama-server installed to ${LLAMA_DIR}/llama-server"

echo "=== [3/4] Downloading sub-3B model (${MODEL_FILENAME}) ==="
TARGET_MODEL_PATH="${MODELS_DIR}/${MODEL_FILENAME}"

if [ -f "${TARGET_MODEL_PATH}" ] && [ -s "${TARGET_MODEL_PATH}" ]; then
    echo "✓ Model file ${TARGET_MODEL_PATH} already exists and is non-empty. Skipping download."
else
    TMP_MODEL="$(mktemp "${TARGET_MODEL_PATH}.part-XXXXXX")"
    echo "Downloading from ${MODEL_URL}..."
    if command -v curl >/dev/null 2>&1; then
        curl -fL --retry 3 --retry-delay 2 -o "${TMP_MODEL}" "${MODEL_URL}"
    else
        wget --tries=3 -O "${TMP_MODEL}" "${MODEL_URL}"
    fi
    mv "${TMP_MODEL}" "${TARGET_MODEL_PATH}"
    echo "✓ Model downloaded successfully to ${TARGET_MODEL_PATH}"
fi

echo "=== [4/4] Verifying installation ==="
ls -lh "${LLAMA_DIR}/llama-server"
ls -lh "${TARGET_MODEL_PATH}"

echo "=== Installation Complete! ==="
