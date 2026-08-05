#!/usr/bin/env bash
# CML project build for qwen3_8b
# Runtime: PBJ Workbench · Python 3.12 · Nvidia GPU (CUDA 12.5)
set -euo pipefail

ROOT="${QWEN_APP_DIR:-/home/cdsw}"
if [[ -f "${ROOT}/requirements.txt" ]]; then
  REQ="${ROOT}/requirements.txt"
elif [[ -f /home/cdsw/qwen3_8b/requirements.txt ]]; then
  ROOT=/home/cdsw/qwen3_8b
  REQ="${ROOT}/requirements.txt"
else
  echo "requirements.txt not found under /home/cdsw or QWEN_APP_DIR" >&2
  exit 1
fi

echo "==> Python: $(python -V)"
echo "==> App root: ${ROOT}"

python -m pip install -U pip wheel setuptools

# CUDA wheel for PBJ Nvidia GPU (CUDA 12.5 host; cu124 wheels are compatible)
python -m pip install "torch==2.6.0" --index-url https://download.pytorch.org/whl/cu124

python -m pip install -r "${REQ}"

python - <<'PY'
import sys
assert sys.version_info[:2] == (3, 12), f"Expected Python 3.12, got {sys.version}"
import torch, transformers, fastapi, uvicorn
print("ok", "python", sys.version.split()[0], "torch", torch.__version__, "cuda", torch.cuda.is_available(), "transformers", transformers.__version__)
PY

echo "==> Build complete"
