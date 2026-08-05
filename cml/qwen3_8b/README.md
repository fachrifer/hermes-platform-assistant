# Qwen3 8B — CML Project `qwen3_8b`

Dedicated Cloudera AI project for serving **Qwen3-8B** as an Analytical Application.

| Setting | Value |
|---|---|
| **Project name** | `qwen3_8b` |
| **Runtime** | **PBJ Workbench** · **Python 3.12** · **Nvidia GPU** |
| **ML Runtimes** | 2025.06+ / **2026.04** (CUDA **12.5**) |
| **Script** | `/home/cdsw/run_app.py` |
| **GPU** | V100 32GB (or larger) |
| **Model** | `Qwen/Qwen3-8B` (FP16) |

## Layout (project root)

Upload **isi** folder `cml/qwen3_8b/` ke root project CML (bukan nested lagi):

```text
/home/cdsw/
  run_app.py
  engine.py
  serve.py
  requirements.txt
  cdsw-build.sh
  env.example
  __init__.py
  models/                 # optional local weights
    Qwen3-8B/
      config.json
      ...
```

## 1. Buat project

1. CML → **New Project** → name: **`qwen3_8b`**
2. Upload file-file di atas ke `/home/cdsw/`

## 2. Session — install di PBJ Workbench Python 3.12 GPU

1. **Sessions → New Session**
2. Pilih runtime:
   - Editor: **PBJ Workbench**
   - Kernel: **Python 3.12**
   - Edition: **Nvidia GPU**
3. Di **Terminal** (bukan paste bash mentah di cell):

```bash
chmod +x /home/cdsw/cdsw-build.sh
bash /home/cdsw/cdsw-build.sh
```

Atau di cell notebook:

```python
!bash /home/cdsw/cdsw-build.sh
```

Cek:

```python
import sys, torch, transformers, fastapi, uvicorn
print(sys.version)
print(torch.__version__, torch.cuda.is_available())
print(transformers.__version__)
```

Harus Python **3.12.x**, `torch` dengan `+cu124` (atau CUDA build), `transformers >= 4.51.0`.

Lalu **Kernel → Restart**.

## 3. Application

**Applications → New Application**

| Field | Value |
|---|---|
| Name | `qwen3-8b` |
| Script | `/home/cdsw/run_app.py` |
| Runtime | **PBJ Workbench** · **Python 3.12** · **Nvidia GPU** |
| Resource | GPU ≥ 32GB VRAM |

Environment:

```env
QWEN_APP_DIR=/home/cdsw
QWEN_MODEL_ID=Qwen/Qwen3-8B
HF_TOKEN=hf_xxxxxxxx
QWEN_PRELOAD=1
QWEN_ENABLE_THINKING=0
```

Atau weights lokal:

```env
QWEN_APP_DIR=/home/cdsw
QWEN_MODEL_PATH=/home/cdsw/models/Qwen3-8B
QWEN_PRELOAD=1
```

Start → tunggu log:

```text
Qwen app_dir=/home/cdsw
QWEN_PRELOAD=1 — loading weights...
Model ready — starting HTTP server
Uvicorn running on http://127.0.0.1:<CDSW_APP_PORT>
```

## 4. Verifikasi

```text
https://<app-host>/health
https://<app-host>/v1/models
```

```python
import httpx
BASE = "https://<app-host>"
print(httpx.get(f"{BASE}/health", timeout=30).json())
r = httpx.post(
    f"{BASE}/v1/chat/completions",
    json={
        "model": "Qwen3-8B",
        "messages": [{"role": "user", "content": "Halo, balas singkat."}],
        "max_tokens": 64,
    },
    timeout=600.0,
)
print(r.status_code, r.text[:1000])
```

## Catatan runtime PBJ + Python 3.12

- Semua ML Runtime baru adalah **PBJ-based**; jangan pakai Workbench non-PBJ lama.
- Image GPU membawa **CUDA 12.5**; PyTorch diinstall sebagai wheel **cu124** (kompatibel).
- `protobuf==4.25.3` dipin agar selaras dengan `raz-client` di PBJ.
- Jangan jalankan `run_app.py` dengan paste ke notebook cell — pakai **Application**.
- V100: engine memakai **FP16** (`torch.float16`), bukan BF16/FP8.

## Troubleshooting

| Masalah | Solusi |
|---|---|
| Python bukan 3.12 | Pilih runtime **Python 3.12** PBJ Workbench |
| `torch.cuda.is_available() == False` | Session/App harus **Nvidia GPU**; install ulang via `cdsw-build.sh` |
| `KeyError: qwen3` | `transformers<4.51` — upgrade ke `4.51.3` |
| `huggingface-hub 1.x` conflict | `pip install "huggingface-hub>=0.30,<1"` |
| Gagal download HF | Set `HF_TOKEN` atau `QWEN_MODEL_PATH` lokal |
| `asyncio` / running loop | Pastikan `run_app.py` terbaru (uvicorn di thread) |
