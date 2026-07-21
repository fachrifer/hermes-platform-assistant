# Hermes Agent — Design Document

Date: 2026-07-14
Status: Approved

## 1. Context

Personal assistant modular berbasis Docker, integrasi Money Manager (Realbyte MCP),
Gmail, Google Calendar, Microsoft Outlook, dan Telegram, dengan Google Gemini sebagai otak.
Target: ringan (MacBook Air M2 8GB), portabel (bisa dipindah ke PC rumah 32GB).

## 2. TASK 1 — Hasil Verifikasi Repo money-manager-mcp (BLOCKING, selesai)

Repo `shahlaukik/money-manager-mcp` **berbeda dari asumsi spec.md**:

| Aspek | Asumsi spec | Fakta terverifikasi |
|---|---|---|
| Runtime | Python | **Node.js/TypeScript** (npm package) |
| Entry | `python -m money_manager_mcp` | **`npx money-manager-mcp@latest --baseUrl http://IP:PORT`** |
| Database | SQLite sendiri | **Tidak ada DB sendiri** |
| Sifat | "bukan connector Realbyte" | **Justru connector KE app Realbyte Money Manager** |
| Transport | stdio | **stdio via npx** |
| Tools | tidak diketahui | **18 tools** (transaction_*, summary_*, asset_*, card_*, transfer_*, dashboard_*, init_get_data) |

Konsekuensi: butuh app Money Manager di HP + PC Manager web server aktif di WiFi yang sama.
Section 2.1 & TASK 3 (Dockerfile Python + DB volume) pada spec.md tidak berlaku.

## 3. Keputusan (hasil brainstorming dengan user)

- Money Manager: pakai repo Realbyte apa adanya (npx stdio).
- LLM: Google Gemini.
- Gmail: read-only (untuk briefing).
- Agenda: 3 sumber — Google Calendar + Microsoft Outlook (Graph) + manual via Telegram (SQLite).
- Briefing: startup + on-demand `/brief`. Isi: agenda + ringkasan keuangan + status konektor.
  **Tidak ada** daily summary terjadwal.
- Fitur nilai-tambah MVP: NL finance query, smart expense capture, conversational memory.
- Packaging: 1 container Docker ringan (python:3.11-slim + Node.js untuk npx). Telegram polling in-process.
- State: SQLite lokal lewat abstraksi DB (mudah pindah ke Turso/libSQL nanti).
- Sync lintas mesin: Opsi A — `data/` + `credentials/` di folder cloud-synced, di-mount ke container.
  Syarat: tidak dijalankan bersamaan di dua mesin.
- IP HP berbeda per jaringan: `MONEY_MANAGER_BASE_URL` via env per-mesin + command Telegram `/setmoney <ip:port>` (disimpan lokal, tidak di-sync).
- Persona: **squire ksatria** yang setia kepada user, bahasa Indonesia, dapat dikustom via config.

Ditunda: import Drive/Sheets, daily summary terjadwal, investment advisor IHSG, budget alert.

## 4. Arsitektur

Semua in-process dalam 1 container. Tiap konektor = modul Python dengan interface
`Connector` (`name`, `health_check()`, `is_available()`).

- Money Manager → MCP client resmi (Python `mcp` SDK) spawn `npx money-manager-mcp`.
- Gmail & Google Calendar → Google API Python client (1 OAuth Google, scope readonly).
- Outlook → MSAL + Microsoft Graph REST (Azure app, `Calendars.Read`).
- Agenda manual → SQLite.
- Telegram → `python-telegram-bot` (polling), file `core/telegram_bot.py` (hindari bentrok nama paket `telegram`).
- Core → FastAPI (`/health` + lifespan startup brief) + orchestrator + Gemini + memory.

## 5. Struktur Project

```
hermes-agent/
├── docker-compose.yml
├── Dockerfile
├── .env.example
├── requirements.txt
├── config/
│   ├── settings.py
│   └── persona.py
├── core/
│   ├── db.py
│   ├── connector.py
│   ├── agent.py
│   ├── llm_client.py
│   ├── memory.py
│   ├── briefing.py
│   ├── telegram_bot.py
│   └── main.py
├── connectors/
│   ├── money_manager.py
│   ├── gmail.py
│   ├── gcal.py
│   ├── outlook.py
│   └── agenda_manual.py
└── data/hermes.db  (+ credentials/ dari folder cloud-synced)
```

## 6. Alur

1. `docker compose up` → FastAPI lifespan → init agent → `health_check_all()` → startup brief ke Telegram.
2. Telegram chat → router: command (`/brief`, `/status`, `/agenda`, `/setmoney`, `/advice`) atau NL → Gemini `plan()` pilih aksi → panggil konektor → balas dengan persona squire.
3. Konektor mati → brief/aksi tetap jalan, bagian itu di-skip + warning. Tidak crash.

## 6b. Fitur: Scan Email Transaksi (ditambahkan)

On-demand (`/scan` atau NL "hari ini ada transaksi apa saja?"):
1. Gmail `scan_finance` ambil email hari ini via filter pengirim + kata kunci
   (`FINANCE_EMAIL_SENDERS`, `FINANCE_EMAIL_KEYWORDS`).
2. Buang email yang sudah diproses (tabel `recorded_emails`).
3. Money Manager `init_get_data` → daftar kategori asli.
4. Gemini `extract_transaction` per email → {amount, type, category, description, merchant}
   dipetakan ke kategori asli.
5. Tampilkan per transaksi di Telegram dengan tombol ✅ Catat / ⏭️ Lewati.
6. Catat → `transaction_create` + tandai email `recorded`. Lewati → tandai `skipped`.
   Keduanya mencegah kemunculan ulang (anti-dobel).

Komponen: `connectors/gmail.py::scan_finance`, `connectors/money_manager.py::get_init_data`,
`core/llm_client.py::extract_transaction`, `core/agent.py::scan_email_transactions/record_scanned_transaction`,
`core/telegram_bot.py` (/scan, callback buttons, NL routing), `core/db.py::recorded_emails`.

## 7. Graceful degradation

Setiap konektor mengimplementasi `is_available()` dan menangani error sendiri.
Agent & briefing melewati konektor yang tidak tersedia dengan penanda status.
