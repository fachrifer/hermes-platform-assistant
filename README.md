# Hermes Agent

Personal assistant modular berbasis Docker dengan persona futuristik **Nyx Assistant**.
Terintegrasi dengan **spreadsheet finance**, **Google Calendar**,
**Microsoft Outlook**, **Tavily Search**, dan **Telegram**, dengan **Google Gemini**
sebagai otak.

Ringan (jalan di MacBook Air M2 8GB), portabel (pindah laptop ↔ PC via folder cloud-synced),
dan tahan gangguan (konektor mati → dilewati, tidak crash).

---

## Fitur

- 🤖 **Telegram bot** — perintah + bahasa natural, dengan persona Nyx yang bisa dikustom.
- 💰 **Spreadsheet finance** — paste/upload transaksi, staging, ekspor bulanan TSV Money Manager.
- 📥 **Import transaksi** (`/import`) — pilih **Gmail** (scan Pribadi+Kerja) atau **Paste** (teks/upload `.txt`/`.eml`/`.html`/PDF/gambar);
  Gemini ekstrak + rekomendasi kategori, lalu `/batch` → `/export`.
- 🧠 **Financial advisor** (`/advice`) — analisis & saran keuangan dari Gemini.
- 📅 **Agenda gabungan** — Google Calendar + Outlook + agenda manual.
- 🌌 **Briefing** — otomatis saat startup + on-demand `/brief` (agenda + keuangan + status), suara Nyx.
- 💬 **Conversational memory** — obrolan nyambung antar pesan.
- 🌐 **Web search** — pencarian informasi umum dan berita terbaru melalui Tavily,
  dengan URL sumber untuk verifikasi.
- 📊 **Quota monitor** (`/quota`) — pemakaian credits Tavily dan status/quota resmi
  Gemini jika service account Google Cloud sudah dikonfigurasi.
- 📎 **Agenda dari file** — baca image/PDF, ekstrak semua agenda, lalu konfirmasi
  satu per satu sebelum disimpan.
- 📅 **Google Calendar write** — agenda bertanggal dikirim ke Calendar akun Pribadi.

---

## Arsitektur

Satu container ringan Python. Semua konektor in-process
dengan interface seragam (`health_check`, `is_available`) sehingga bisa degrade dengan anggun.

```
Telegram ─▶ Hermes Core (Gemini + orchestrator + memory)
                 ├─ Finance spreadsheets (XLSX/TSV + SQLite staging)
                 ├─ Google Calendar
                 ├─ Outlook / MS Graph (read-only)
                 ├─ Tavily Search (web + news)
                 └─ Agenda manual (SQLite)
State: SQLite (data/hermes.db) + kredensial (credentials/)
```

Desain lengkap: `docs/superpowers/specs/2026-07-14-hermes-agent-design.md`.

---

## Prasyarat kredensial

| Layanan | Yang dibutuhkan | Sumber |
|---|---|---|
| Telegram | Bot token + chat id | @BotFather, @userinfobot |
| Gemini | API key | https://aistudio.google.com |
| Tavily | API key | https://app.tavily.com |
| Google (Gmail + Calendar) | OAuth client (Desktop app) | Google Cloud Console (aktifkan Gmail API + Calendar API) |
| Gemini quota resmi | Service account JSON + role `Service Usage Viewer` dan `Monitoring Viewer` | Google Cloud Console |
| Outlook | App registration (delegated `Calendars.Read`) | Azure Portal |
| Finance spreadsheet | Workbook kategori + history di `/app/data/reference` | File pribadi yang dimount |

> Hermes tidak terhubung langsung ke aplikasi Money Manager. Hermes membuat XLSX
> untuk review dan TSV untuk diimpor manual ke aplikasi.

---

## Setup

### 1. Konfigurasi
```bash
cp .env.example .env
# isi TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, GEMINI_API_KEY, dst.
```

### 2. Login sekali untuk Google (Gmail + Calendar) & Microsoft (di host, sekali saja)
Butuh Python 3.11+ dan dependency lokal:
```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Google OAuth — Gmail finance + Calendar Pribadi.
# Aktifkan Gmail API + Google Calendar API pada OAuth client.
# Tambahkan akun sebagai Test user di Google Cloud (atau set consent In production).
GOOGLE_CLIENT_SECRETS="/path/ke/credentials/google_client_secret.json" \
GOOGLE_TOKEN_PATH="/path/ke/credentials/google_token.json" \
python -m scripts.google_login

# Akun kedua (opsional)
GOOGLE_CLIENT_SECRETS="/path/ke/credentials/google_client_secret.json" \
GOOGLE_TOKEN_PATH="/path/ke/credentials/google_token_2.json" \
python -m scripts.google_login

# Microsoft (Outlook) — device code flow
python -m scripts.ms_login
```
Token tersimpan di `credentials/` dan dipakai ulang oleh container.
Atur `GOOGLE_TOKEN_PATHS` dan `GOOGLE_ACCOUNT_LABELS` di `.env`.

**Re-login per akun** (mis. setelah scope berubah atau token kedaluwarsa):

```bash
GOOGLE_TOKEN_PATH="/path/ke/credentials/google_token.json" python -m scripts.google_login
GOOGLE_TOKEN_PATH="/path/ke/credentials/google_token_2.json" python -m scripts.google_login
```

> Setelah scope OAuth berubah, token lama harus di-authorize ulang (hapus file token
> lama atau jalankan login di atas). Prefer OAuth consent **In production** agar
> refresh token tidak kedaluwarsa setelah 7 hari (mode Testing).

Untuk penulisan Google Calendar pada akun Pribadi:

```bash
rm "/path/ke/credentials/google_token.json"
GOOGLE_CALENDAR_WRITE=1 \
GOOGLE_CLIENT_SECRETS="/path/ke/credentials/client_secret_pribadi.json" \
GOOGLE_TOKEN_PATH="/path/ke/credentials/google_token.json" \
python -m scripts.google_login
```

Lalu set di `.env`:

```env
GOOGLE_CALENDAR_WRITE_ACCOUNT=Pribadi
GOOGLE_CALENDAR_WRITE=1
```

### 3. Jalankan lokal
```bash
docker compose up --build
```
Hermes akan bangun, cek semua konektor, dan mengirim **startup brief** ke Telegram.

### 4. Konfigurasi Tavily dan Gemini quota

Tambahkan API key Tavily ke `.env`:

```env
TAVILY_API_KEY=tvly-...
```

Untuk quota Gemini resmi, buat service account pada project Google Cloud yang
terkait dengan `GEMINI_API_KEY`, lalu berikan role minimal:

- `Service Usage Viewer`
- `Monitoring Viewer`

Simpan JSON key di folder OneDrive:

```text
hermes-assistant/credentials/gemini-quota-service-account.json
```

Tambahkan konfigurasi berikut ke `.env`:

```env
GEMINI_QUOTA_PROJECT_ID=project-id-gemini
GEMINI_QUOTA_CREDENTIALS=/app/credentials/gemini-quota-service-account.json
```

Quota Gemini bersifat opsional. Tanpa service account, `/quota` tetap menampilkan
status API key dan model Gemini, tetapi quota resmi akan ditandai belum tersedia.

Perubahan `.env`, API key, token, atau file service account tidak memerlukan rebuild:

```bash
docker compose up -d
```

Perubahan kode Python memerlukan rebuild:

```bash
docker compose up --build -d
```

---

## Environment Variables

Semua konfigurasi runtime dibaca dari `.env` dan diteruskan ke container melalui
`docker-compose.yml`. Jangan commit `.env`, API key, token OAuth, atau service
account JSON.

### Core dan Telegram

| Variable | Fungsi |
|---|---|
| `TZ` | Zona waktu, contoh `Asia/Jakarta` |
| `HERMES_PORT` | Port host untuk health endpoint, default `8000` |
| `LOG_LEVEL` | Level log, contoh `INFO` atau `DEBUG` |
| `TELEGRAM_BOT_TOKEN` | Token bot dari BotFather |
| `TELEGRAM_CHAT_ID` | Chat ID untuk startup brief |

### Gemini dan Search

| Variable | Fungsi |
|---|---|
| `GEMINI_API_KEY` | API key Gemini dari AI Studio |
| `GEMINI_MODEL` | Model Gemini yang dipakai, misalnya `gemini-3.1-flash-lite` |
| `TAVILY_API_KEY` | API key Tavily untuk web search dan berita real-time |
| `TAVILY_COUNTRY` | Boost negara untuk search general (default `indonesia`) |
| `TAVILY_SEARCH_DEPTH` | `basic` atau `advanced` (default `advanced`, lebih ketat) |
| `GEMINI_QUOTA_PROJECT_ID` | Project Google Cloud untuk pembacaan quota resmi Gemini |
| `GEMINI_QUOTA_CREDENTIALS` | Path container ke service account quota Gemini |

### Google OAuth

| Variable | Fungsi |
|---|---|
| `GOOGLE_CLIENT_SECRETS` | Fallback client secret untuk login interaktif |
| `GOOGLE_TOKEN_PATH` | Fallback token satu akun |
| `GOOGLE_TOKEN_PATHS` | Daftar token akun, dipisahkan koma |
| `GOOGLE_ACCOUNT_LABELS` | Label akun dengan urutan yang sama seperti token |
| `GOOGLE_CALENDAR_WRITE_ACCOUNT` | Akun yang boleh menulis Calendar, default `Pribadi` |
| `GOOGLE_CALENDAR_WRITE` | Aktifkan Calendar write runtime setelah token write dibuat |

Contoh dua akun:

```env
GOOGLE_TOKEN_PATHS=/app/credentials/google_token.json,/app/credentials/google_token_2.json
GOOGLE_ACCOUNT_LABELS=Pribadi,Kerja
```

### Konektor lain dan state

| Variable | Fungsi |
|---|---|
| `FINANCE_ACCOUNT` | Account default pada spreadsheet, default `Cash` |
| `FINANCE_CATEGORY_REFERENCE` | Workbook kategori/subkategori |
| `FINANCE_HISTORY_WORKBOOK` | Workbook history read-only |
| `FINANCE_EXPORT_DIR` | Folder XLSX/TSV export bulanan |
| `MS_OUTLOOK_ENABLED` | `0` untuk disabled (default), `1` untuk aktif |
| `MS_CLIENT_ID` | Client ID Azure App Registration |
| `MS_TENANT_ID` | Tenant Microsoft, default `common` |
| `MS_TOKEN_CACHE` | Cache token MSAL di container |
| `HERMES_DB_PATH` | Lokasi SQLite, default `/app/data/hermes.db` |
| `HERMES_SYNC_DIR` | Folder host yang berisi `data/` dan `credentials/` |
| `FINANCE_EMAIL_SENDERS` | Pengirim/domain email finansial, dipisahkan koma |
| `FINANCE_EMAIL_KEYWORDS` | Kata kunci email transaksi, dipisahkan koma |

> Path `/app/credentials/...` dan `/app/data/...` adalah path di dalam container.
> File fisiknya berada di folder host yang ditentukan oleh `HERMES_SYNC_DIR`.

---

## Sync antar mesin (kantor ↔ rumah) via OneDrive

State Hermes = 1 file SQLite kecil + kredensial. Data keuangan/email/kalender ada di
cloud/HP jadi otomatis ikut. Untuk memindahkan state, gunakan folder OneDrive
**Personal** `hermes-assistant/` (berisi `data/` + `credentials/`).

> Gunakan OneDrive **Personal**, bukan OneDrive kantor — folder `credentials/`
> menyimpan token OAuth & data pribadi.

Set `HERMES_SYNC_DIR` **per-mesin** (path Mac ≠ Windows) di file `.env`:

- **Mac (laptop):**
  ```
  HERMES_SYNC_DIR=/Users/<user>/Library/CloudStorage/OneDrive-Personal/hermes-assistant
  ```
- **Windows (PC rumah):**
  ```
  HERMES_SYNC_DIR=C:/Users/<user>/OneDrive/hermes-assistant
  ```

Lalu:
```bash
docker compose up -d
```

> ⚠️ **OneDrive Files On-Demand**: klik kanan folder `hermes-assistant/` →
> "Always keep on this device", agar `hermes.db` tidak jadi placeholder online-only.
> ⚠️ Jangan jalankan di dua mesin bersamaan (SQLite bukan multi-writer).

---

## Perintah Telegram

| Perintah | Fungsi |
|---|---|
| `/brief` | Laporan lengkap (agenda + keuangan + status) |
| `/status` | Status konektor |
| `/quota` | Cek credits Tavily dan status/quota Gemini |
| _calendar query_ | mis. `cek agenda saya di Google Calendar` |
| `/agenda` | Lihat agenda |
| `/agenda tambah <judul>` | Tambah agenda; event bertanggal masuk Calendar Pribadi |
| `/agenda hapus <id>` | Hapus agenda |
| `/advice` | Analisis & saran keuangan |
| `/scan` | Pindai email hari ini untuk transaksi (legacy; pakai `/import gmail`) |
| `/import` | Pilih Gmail atau Paste untuk periode bulan berjalan |
| `/import gmail 2026-07` | Scan Gmail Pribadi+Kerja ke staging |
| `/import paste 2026-07` | Buka sesi paste/upload untuk satu bulan |
| `/import 2026` | Import email transaksi tahun 2026 ke staging per bulan |
| `/import 2026-01` | Import satu bulan ke staging (mode picker) |
| `/batch` | Daftar batch import dan status review |
| `/batch 2026-01` | Lihat detail transaksi satu batch |
| `/edit <id> <field> <value>` | Edit transaksi di staging |
| `/export 2026-01` | Buat XLSX dan TSV untuk import manual |
| `/finance 2026-01` | Laporan dari spreadsheet |
| `/forget` | Lupakan konteks percakapan |
| _teks biasa_ | mis. `catat kopi 25000`, `pengeluaran makan bulan ini berapa?`, `cari berita terbaru tentang ekonomi Indonesia` |

### Agenda dari image/PDF

Kirim foto, JPG, PNG, WEBP, atau PDF ke Telegram. Nyx akan membaca semua agenda
yang ditemukan dan menampilkannya satu per satu:

```text
Agenda 1/3
Judul: Rapat dengan Client A
Tanggal: 2026-07-20
Waktu: 10:00-11:00
Lokasi: Jakarta

✅ Catat    ⏭️ Lewati
```

Tidak ada agenda yang disimpan sebelum tombol **Catat** dipilih. Agenda dengan
tanggal dan waktu disimpan ke Google Calendar akun `Pribadi`. Agenda tanpa waktu,
atau agenda ketika Calendar gagal, disimpan ke SQLite lokal sebagai fallback.

Untuk agenda teks:

```text
/agenda tambah 2026-07-20 10:00 Rapat dengan Client A
```

`/agenda` membaca kembali database lokal yang sama setelah penyimpanan. Jika event
berhasil dibuat di Google Calendar, balasan menyertakan link event; jika tidak,
balasan menjelaskan bahwa item disimpan lokal.

### Google Calendar query dan reminder

Pertanyaan natural-language berikut dibaca dari Google Calendar akun `Pribadi`:

```text
cek agenda saya di Google Calendar
ada agenda besok?
jadwal saya minggu ini apa?
```

Akun `Kerja` tidak digunakan untuk membaca atau menulis Calendar.

Agenda tidak memiliki reminder default. Reminder hanya dibuat jika diminta:

```text
/agenda tambah 2026-07-20 10:00 Rapat Client A
```

Contoh di atas membuat event tanpa reminder.

```text
/agenda tambah 2026-07-20 10:00 Rapat Client A, ingatkan 30 menit sebelumnya
```

Tanpa metode, reminder menggunakan popup Google Calendar. Email hanya digunakan
jika diminta secara eksplisit:

```text
buat agenda rapat besok jam 10, ingatkan 1 jam sebelumnya lewat email
```

Beberapa reminder juga didukung dan dibuat sebagai override terpisah.

Untuk mengubah reminder event yang sudah ada:

```text
ubah reminder Kereta Parahyangan 134B menjadi 3 jam sebelumnya
sesuaikan semua agenda perjalanan Kereta Parahyangan 139B ke 3 jam sebelumnya
```

Nyx akan mencari event yang cocok di Calendar Pribadi, menampilkan event satu per
satu, lalu meminta **Konfirmasi** atau **Lewati**. Tidak ada event yang diubah
sebelum konfirmasi. Jika tidak ada event yang cocok, tidak ada perubahan yang
dilakukan.

### Historical finance import

Historical import menggunakan staging SQLite. Proses `/import`, melihat `/batch`,
dan `/edit` tidak mengubah Money Manager. Data baru dikirim ke Money Manager
hanya setelah command konfirmasi eksplisit.

#### 1. Import ke staging

```text
/import                 → pilih Gmail atau Paste
/import gmail 2026-07   → scan Gmail Pribadi+Kerja
/import paste 2026-07   → sesi paste/upload
/import 2026
/import 2026-01
```

`/import` tanpa mode menampilkan tombol **Gmail** / **Paste** untuk periode yang
diminta (default: bulan berjalan). `/import gmail YYYY-MM` memindai inbox finansial
akun Pribadi dan Kerja ke staging per bulan. `/import paste YYYY-MM` membuka sesi
paste/upload seperti alur manual.

`/import 2026` mengambil email dari Januari sampai Desember 2026 dan
mengelompokkannya per bulan. Email yang sama tidak dibuat ulang saat import diulang.

#### 2. Review batch

```text
/batch
/batch 2026-01
```

Detail batch menampilkan jumlah transaksi, total pengeluaran/pemasukan, transaksi
yang perlu diperiksa, dan kandidat duplikat. Kandidat duplikat tidak dibuang
otomatis; Anda yang memutuskan saat review.

#### 3. Edit transaksi

```text
/edit 123 tanggal 2026-01-15
/edit 123 nominal 125000
/edit 123 tipe expense
/edit 123 kategori Makanan
/edit 123 merchant Nama Toko
/edit 123 deskripsi Makan siang
```

Field yang tersedia: `tanggal`, `nominal`, `tipe`, `kategori`, `subkategori`,
`akun`, `merchant`, dan `deskripsi`. Semua perubahan hanya terjadi di staging.

#### 4. Export dan import manual

```text
/export 2026-01
```

Command ini membuat `2026-01.xlsx` untuk review dan `2026-01.tsv` untuk diimpor
manual ke Money Manager. Hermes tidak mengirim transaksi ke aplikasi.

### Search web dan berita

Nyx menggunakan Tavily ketika pertanyaan membutuhkan informasi dari internet.
Contoh:

```text
cari berita terbaru tentang ekonomi Indonesia
apa update terbaru Gemini API?
berapa harga emas hari ini?
cari dokumentasi resmi FastAPI tentang lifespan
```

Jawaban diringkas oleh Gemini dan menyertakan URL sumber. Jika `TAVILY_API_KEY`
kosong, kuota habis, atau Tavily tidak tersedia, Hermes menyampaikan bahwa search
belum tersedia dan fitur lain tetap berjalan.

### Quota

Gunakan:

```text
/quota
```

Contoh output Tavily:

```text
✅ Tavily: 125 / 1.000 credits terpakai
Sisa: 875 credits
```

Bagian Gemini menampilkan model aktif, status API key, dan metadata quota resmi jika
service account serta izin Google Cloud sudah tersedia. Gemini tidak menampilkan
angka perkiraan ketika data pemakaian aktual tidak dapat dibaca.

---

## Import transaksi (Gmail + paste / upload)

Hermes mendukung dua mode intake lewat `/import`:

1. **Gmail** — scan inbox finansial akun Pribadi + Kerja ke staging
2. **Paste/upload** — paste teks notifikasi/email, atau kirim `.txt` / `.eml` / `.html` / PDF / gambar

Alur umum:

1. `/import` atau `/import paste 2026-01` — pilih mode atau langsung buka sesi paste
2. Hermes ekstrak + rekomendasi kategori → staging
3. `/import done` (paste saja) → `/batch YYYY-MM` → `/export YYYY-MM tsv`

> Calendar tetap hanya akun **Pribadi** (baca/tulis). Gmail scan memakai Pribadi + Kerja.
> Import ke aplikasi Money Manager tetap manual dari file TSV.

## Kustomisasi persona

Ubah di `.env`:
- `HERMES_PERSONA` — `nyx`, `squire`, `butler`, `casual`, `professional`
- `HERMES_ADDRESS` — panggilan untukmu (mis. `Tuan`, `Ksatria`, atau namamu)
- `HERMES_PERSONA_FILE` — path prompt custom (override penuh; boleh pakai `{address}`)

## Deploy Cloud

Hermes dapat dijalankan sebagai satu container di VPS/cloud server. Telegram
memakai polling sehingga port publik tidak diperlukan untuk fungsi utama.

1. Siapkan Docker di VPS dan buat direktori persistent:

```bash
mkdir -p /opt/hermes/reference /opt/hermes/data /opt/hermes/credentials
```

2. Salin `Kategori Money Manager.xlsx` dan `MoneyManager-2025.xlsx` ke
   `/opt/hermes/reference`. Jangan commit atau expose workbook tersebut.

3. Salin `deploy/cloud.env.example` menjadi `deploy/cloud.env`, isi secret
   Telegram, Gemini, Google OAuth, dan ubah `FINANCE_REFERENCE_DIR` jika perlu.

4. Jalankan service:

```bash
docker compose --env-file deploy/cloud.env -f docker-compose.cloud.yml up -d --build
docker compose --env-file deploy/cloud.env -f docker-compose.cloud.yml logs -f hermes
curl http://127.0.0.1:8000/health
```

`data` dan `credentials` adalah volume Docker persistent. Backup keduanya,
terutama `hermes.db` dan token OAuth. Jangan menjalankan dua instance Hermes
yang memakai SQLite yang sama. Port health hanya bind ke localhost; gunakan
reverse proxy dengan autentikasi jika endpoint tersebut perlu diakses dari luar.

---

## Office AI Assistant (supported)

Intranet VM **`10.216.4.80`**, directory **`/home/timai/hermes-assistant`**.
Upstream **Nous Hermes Agent Web Dashboard** plus **office-gateway**.
The VM has **no internet**: build images on a PC, ship a tar, load locally
(`pull_policy: never`).

Hermes uses the intranet **LiteLLM** gateway at `http://10.216.221.100/llm/v1`
(base URL + API key). office-gateway probes the other apps on this VM via
`host.docker.internal`.
This stack does **not** take their ports; Dashboard binds **:9119** and
nginx **dashboard-proxy** publishes **:80** for external HTTP access.

```text
Browser → http://10.216.4.80/      (dashboard-proxy → Hermes)
       or http://10.216.4.80:9119  (Hermes Dashboard direct)
              ├─ LLM  → http://10.216.221.100/llm/v1
              └─ tools → office-gateway :8080 (internal)
                           └─ health of inference, workflow, dashboard, milvus, …
```

| Container | Host port | Role |
|---|---|---|
| LiteLLM `10.216.221.100/llm/v1` | — | LLM for Hermes |
| `aiplatform-agent-inference` | 8010 | monitored |
| `aiplatform-workflow` | 8001 | monitored |
| `aiplatform-dashboard` | 3001 | monitored |
| `common-service-frontend` | 555 | monitored |
| `milvus-standalone` | 9091 / 19530 / 2379 | vector DB (probe 9091) |
| `attu` | 8000 | Milvus UI |
| `qdrant_timai` | 6333–6334 | vector DB (probe 6333) |
| `hermes-agent` | **9119** | Web Dashboard (new) |
| `dashboard-proxy` | **80** | nginx → dashboard (external HTTP) |
| `office-gateway` | internal 8080 | monitor + APPROVE writes (new) |

Design: `docs/superpowers/specs/2026-08-12-office-hermes-assistant-design.md`.
Runbook: `deploy/office-assistant/README.md`.

```bash
# On PC (internet):
chmod +x deploy/office-assistant/scripts/*.sh
./deploy/office-assistant/scripts/build-and-ship.sh
# scripts/configs only: ./deploy/office-assistant/scripts/ship-to-vm.sh --scripts-only

# On VM:
cd /home/timai/hermes-assistant
cp -n .env.example .env && cp -n hermes/.env.example hermes/.env
# set OPENAI_API_KEY, LITELLM_BASE_URL, LITELLM_MODEL, dashboard password
chmod +x scripts/*.sh
./scripts/load-and-start.sh
# equivalent: ./scripts/load-images.sh && docker compose --env-file .env -f docker-compose.yml up -d
```

Writes: propose → reply exactly `APPROVE <action_id>` → execute.
Telegram office delivery is out of v1.

---

## Ditunda (fase berikutnya)

Import data Drive/Sheets, daily summary terjadwal, investment advisor IHSG, budget alert.
