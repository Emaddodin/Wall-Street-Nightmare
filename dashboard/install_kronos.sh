#!/bin/bash
# Installs Kronos (github.com/shiyu-coder/Kronos, MIT) next to Gold Desk for the same python3.
# The model itself (Kronos-small, ~100 MB) downloads from Hugging Face on first use.
set -e
cd "$(dirname "$0")"
if [ ! -d ../Kronos ]; then
  git clone --depth 1 https://github.com/shiyu-coder/Kronos.git ../Kronos
fi
python3 -m pip install --user --upgrade torch "einops==0.8.1" huggingface_hub safetensors pandas tqdm
python3 -c "import torch; print('torch', torch.__version__, '| Apple GPU (MPS):', torch.backends.mps.is_available())"
echo "Kronos installed. Backtest it: python3 kronos_backtest.py --litefinance-days 20"
