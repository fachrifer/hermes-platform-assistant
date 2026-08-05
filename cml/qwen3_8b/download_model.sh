#!/usr/bin/env bash
# Download Qwen3-8B ke disk CML (jalankan di Session, bukan Application).
# Setelah selesai, set Application env:
#   QWEN_MODEL_PATH=/home/cdsw/models/Qwen3-8B
#   QWEN_PRELOAD=1
# dan kosongkan/abaikan download ulang dari hub.
set -euo pipefail

MODEL_ID="${QWEN_MODEL_ID:-Qwen/Qwen3-8B}"
TARGET="${QWEN_MODEL_PATH:-/home/cdsw/models/Qwen3-8B}"

echo "==> Python: $(python -V)"
echo "==> Model : ${MODEL_ID}"
echo "==> Target: ${TARGET}"

if [[ -z "${HF_TOKEN:-}" && -z "${HUGGING_FACE_HUB_TOKEN:-}" ]]; then
  echo "WARNING: HF_TOKEN belum di-set. Set dulu jika model gated / rate-limited:" >&2
  echo "  export HF_TOKEN=hf_xxxxxxxx" >&2
fi

python -m pip install -U "huggingface-hub>=0.30.0,<1.0"

mkdir -p "$(dirname "${TARGET}")"

python - <<PY
import os
from pathlib import Path
from huggingface_hub import snapshot_download

model_id = os.environ.get("QWEN_MODEL_ID", "Qwen/Qwen3-8B")
target = Path(os.environ.get("QWEN_MODEL_PATH", "/home/cdsw/models/Qwen3-8B"))
token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN") or None

print(f"Downloading {model_id} -> {target}")
path = snapshot_download(
    repo_id=model_id,
    local_dir=str(target),
    local_dir_use_symlinks=False,
    token=token,
    resume_download=True,
    max_workers=4,
)
cfg = Path(path) / "config.json"
assert cfg.is_file(), f"Download incomplete: missing {cfg}"
print("OK:", path)
print("config.json:", cfg)
PY

echo "==> Done. Next Application env:"
echo "    QWEN_APP_DIR=/home/cdsw"
echo "    QWEN_MODEL_PATH=${TARGET}"
echo "    QWEN_PRELOAD=1"
echo "    # do NOT rely on QWEN_MODEL_ID download in Application"
