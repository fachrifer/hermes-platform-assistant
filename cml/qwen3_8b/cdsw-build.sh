#!/usr/bin/env bash
# CML project build for qwen3_8b
# Runtime: PBJ Workbench · Python 3.12 · Nvidia GPU
# V100 (sm_70) requires a PyTorch wheel that still ships Volta kernels.
# Prefer cu118 official wheels (include sm_70). Avoid random PyPI CUDA wheels.
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

# Official PyTorch CUDA index — cu118 keeps Volta (V100 / sm_70).
TORCH_INDEX="${TORCH_INDEX_URL:-https://download.pytorch.org/whl/cu118}"
TORCH_VERSION="${TORCH_VERSION:-2.6.0}"

echo "==> Python: $(python -V)"
echo "==> App root: ${ROOT}"
echo "==> Torch: ${TORCH_VERSION} from ${TORCH_INDEX}"

python -m pip install -U pip wheel setuptools
python -m pip uninstall -y torch torchvision torchaudio 2>/dev/null || true
python -m pip install "torch==${TORCH_VERSION}" --index-url "${TORCH_INDEX}"
python -m pip install -r "${REQ}"

python - <<'PY'
import sys
assert sys.version_info[:2] == (3, 12), f"Expected Python 3.12, got {sys.version}"
import torch, transformers, fastapi, uvicorn

print("ok", "python", sys.version.split()[0])
print("torch", torch.__version__, "cuda_available", torch.cuda.is_available())
print("transformers", transformers.__version__)
if torch.cuda.is_available():
    name = torch.cuda.get_device_name(0)
    major, minor = torch.cuda.get_device_capability(0)
    arches = torch.cuda.get_arch_list()
    print("gpu", name, f"sm_{major}{minor}", "arch_list", arches)
    if (major, minor) == (7, 0) and not any(a.startswith("sm_70") for a in arches):
        raise SystemExit(
            "ERROR: GPU is V100 (sm_70) but this torch build has no sm_70 kernels. "
            "Reinstall with: pip install torch==2.6.0 --index-url "
            "https://download.pytorch.org/whl/cu118"
        )
PY

echo "==> Build complete"
