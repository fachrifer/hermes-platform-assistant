# Deploy di Cloudera Machine Learning (CML)

Qwen3 8B punya **project sendiri** `qwen3_8b` di runtime baru. Office AI
assistant is `deploy/office-assistant/` (Hermes Dashboard + office-gateway),
not a CML app. Qwen/LiteLLM serving docs below remain useful as the on-prem
model backend.

| | |
|---|---|
| Project | `qwen3_8b` |
| Runtime | **PBJ Workbench** · **Python 3.12** · **Nvidia GPU** (CUDA 12.5) |

Alur Qwen:

```text
New Project qwen3_8b → upload ke /home/cdsw → Session PBJ 3.12 GPU → cdsw-build.sh → Application → /health
```

---

# Bagian A — Application Qwen3 8B (project `qwen3_8b`)

Panduan lengkap khusus project: [`cml/qwen3_8b/README.md`](qwen3_8b/README.md).

## A1. Buat project CML

1. Buka CML / Cloudera AI.
2. **New Project** → name: **`qwen3_8b`**.
3. Masuk ke project tersebut.

## A2. Upload isi `cml/qwen3_8b/` ke root project

Upload **file di dalam** folder (bukan nested folder lagi). Di CML harus ada:

```text
/home/cdsw/
  run_app.py
  engine.py
  serve.py
  requirements.txt
  cdsw-build.sh
  env.example
  __init__.py
```

Cek:

```bash
ls /home/cdsw/run_app.py /home/cdsw/engine.py /home/cdsw/serve.py
```

## A3. Install di Session — PBJ Workbench Python 3.12 GPU

1. Project → **Sessions** → **New Session**.
2. Pilih runtime:
   - Editor: **PBJ Workbench**
   - Kernel: **Python 3.12**
   - Edition: **Nvidia GPU**
3. Install (Terminal atau cell dengan `!`):

```bash
chmod +x /home/cdsw/cdsw-build.sh
bash /home/cdsw/cdsw-build.sh
```

```python
!bash /home/cdsw/cdsw-build.sh
```

Script itu menginstall **torch 2.6.0+cu124** (cocok CUDA 12.5 di PBJ) lalu sisa `requirements.txt`.

4. Cek:

```python
import sys, torch, transformers, fastapi, uvicorn
print(sys.version)
print(torch.__version__, "cuda=", torch.cuda.is_available())
print("transformers", transformers.__version__)
```

Harus Python **3.12.x**, `transformers >= 4.51.0`, lalu **Kernel → Restart**.

> Jangan stop Session di tengah install.  
> Application memakai environment runtime yang sama — install di runtime GPU yang akan dipakai App.

## A3b. Jangan jalankan `run_app.py` di notebook cell

| Tujuan | Cara |
|---|---|
| Cek import | `from engine import TransformersEngine` (di `/home/cdsw`) |
| Menjalankan server | **Applications → New Application** |

```python
import sys
sys.path.insert(0, "/home/cdsw")
from engine import TransformersEngine
from serve import create_app
print("ok", TransformersEngine)
```

## A4. Buat Application `qwen3-8b`

1. Project → **Applications** → **New Application**.
2. Isi:

| Field | Isi |
|---|---|
| Name | `qwen3-8b` |
| Script | `/home/cdsw/run_app.py` |
| Runtime | **PBJ Workbench** · **Python 3.12** · **Nvidia GPU** |
| Resource | **GPU**, VRAM **≥ 32GB** (V100 32GB) |

3. Environment variables:

| Env | Contoh | Ket. |
|---|---|---|
| `QWEN_APP_DIR` | `/home/cdsw` | Root project `qwen3_8b` |
| `QWEN_MODEL_ID` | `Qwen/Qwen3-8B` | Download dari Hugging Face |
| `HF_TOKEN` | `hf_...` | Token HF — sering **wajib** |
| `QWEN_MODEL_PATH` | `/home/cdsw/models/Qwen3-8B` | Alternatif weights lokal |
| `QWEN_PRELOAD` | `1` | Load model dulu sebelum HTTP serve |

### Token Hugging Face (login)

Download lewat `QWEN_MODEL_ID` memakai library Hugging Face. Banyak lingkungan (termasuk kantor) **perlu login/token**, terutama jika:

- model bersifat gated / perlu accept license di halaman model
- kena rate-limit tanpa auth
- proxy/firewall hanya mengizinkan request ber-token

Langkah:

1. Buat akun di https://huggingface.co (jika belum).
2. Buka halaman model `Qwen/Qwen3-8B` → jika ada tombol **Agree and access**, klik dulu.
3. Buat token: https://huggingface.co/settings/tokens → **Read**.
4. Di Application env CML, tambahkan:
   ```text
   HF_TOKEN=hf_xxxxxxxxxxxxxxxx
   QWEN_MODEL_ID=Qwen/Qwen3-8B
   ```
5. Restart Application.

Cek akses dari Session (sebelum andalkan Application):

```python
!python -m pip install -U huggingface_hub
import os
os.environ["HF_TOKEN"] = "hf_xxxxxxxx"  # atau set di Project env
from huggingface_hub import hf_hub_download
print(hf_hub_download("Qwen/Qwen3-8B", "config.json"))
```

Kalau perintah itu gagal (401/403/timeout), Application juga tidak akan bisa download — perbaiki token/network dulu, atau beralih ke `QWEN_MODEL_PATH` lokal.

> Jangan commit token ke Git. Simpan hanya di CML Application / Project environment.

4. Start Application → tunggu **Running**.

## A5. Cek Qwen sudah hidup

Log sukses kira-kira seperti ini:

```text
INFO:root:Qwen app_dir=/home/cdsw python=3.12.x (project=qwen3_8b, runtime=PBJ Workbench 3.12)
INFO:root:Model ready — starting HTTP server
INFO:     Uvicorn running on http://127.0.0.1:8100
```

1. Buka URL Application dari CML UI (sudah login):

```text
https://qwen3-8b.cml.apps.dataservice.kemenkeu.go.id/health
https://qwen3-8b.cml.apps.dataservice.kemenkeu.go.id/v1/models
```

Hasil `/health`: `{"status":"ok"}`  
Hasil `/v1/models`: JSON berisi `"id": "Qwen/Qwen3-8B"` dan/atau `"Qwen3-8B"`

**Bukan** `https://.../models` saja di browser lama tanpa upload `serve.py` baru — pakai **`/v1/models`**.  
Endpoint list models **tidak mendownload** weights; kalau “not found” di browser biasanya login CML / path salah, bukan gagal download.

2. Dari Session (ganti `BASE`):

```python
BASE = "https://qwen3-8b.cml.apps.dataservice.kemenkeu.go.id"
!curl -sS "{BASE}/health"
!curl -sS "{BASE}/v1/models"
```

3. Test chat (baru di sini weights di-load; request pertama bisa sangat lama):

```python
import httpx
BASE = "https://qwen3-8b.cml.apps.dataservice.kemenkeu.go.id"
r = httpx.post(
    f"{BASE}/v1/chat/completions",
    json={
        "model": "Qwen3-8B",
        "messages": [{"role": "user", "content": "Halo, balas singkat."}],
        "max_tokens": 64,
    },
    timeout=600.0,
)
print(r.status_code, r.text[:2000])
```

Kalau chat gagal dengan pesan Hugging Face / connection / 401 — **download gagal**. Pakai `QWEN_MODEL_PATH` lokal.

4. **Salin URL Application** (tanpa `/health`) — dipakai sebagai LiteLLM / OpenAI-compatible base URL.

---

# Checklist Qwen (project `qwen3_8b`)

| # | Langkah | Perintah |
|---|---|---|
| 1 | Buat project | New Project → **`qwen3_8b`** |
| 2 | Upload | isi `cml/qwen3_8b/` → `/home/cdsw/` |
| 3 | Session runtime | **PBJ Workbench** · **Python 3.12** · **Nvidia GPU** |
| 4 | Install | `bash /home/cdsw/cdsw-build.sh` |
| 5 | Cek | `import torch, transformers; print(torch.__version__, transformers.__version__)` |
| 6 | Application | Script `/home/cdsw/run_app.py`, runtime PBJ 3.12 GPU |
| 7 | Health | `curl $BASE/health` → `status ok` |

---

# Troubleshooting

| Masalah | Solusi |
|---|---|
| Runtime bukan Python 3.12 | Pilih **PBJ Workbench · Python 3.12 · Nvidia GPU** |
| `torch.cuda.is_available() == False` | App/Session harus Nvidia GPU; jalankan ulang `cdsw-build.sh` |
| `huggingface-hub>=0.30.0,<1.0` but found `1.x` | `!pip install "huggingface-hub>=0.30.0,<1.0"` |
| `KeyError: 'qwen3'` | Upgrade: `!pip install -U "transformers==4.51.3"` lalu restart |
| Gagal download / HF timeout | `HF_TOKEN` atau `QWEN_MODEL_PATH` lokal |
| Browser `/models` not found | Pakai `/v1/models` |
| `asyncio.run() cannot be called from a running event loop` | Upload `run_app.py` terbaru, Stop → Start |
| `NameError: __file__ is not defined` | Jangan paste ke notebook — pakai **Application** |
| `No module named 'engine'` | File di `/home/cdsw/`; set `QWEN_APP_DIR=/home/cdsw` |
| `SyntaxError` di `python -m pip ...` | Pakai `!pip ...` atau **Terminal** |
| `raz-client ... protobuf` warning | `!pip install protobuf==4.25.3` lalu restart kernel |
| `No module named 'uvicorn'` / `torch` | `bash /home/cdsw/cdsw-build.sh` di runtime GPU yang sama |
| Start lama | Normal: download `Qwen/Qwen3-8B` pertama kali |
