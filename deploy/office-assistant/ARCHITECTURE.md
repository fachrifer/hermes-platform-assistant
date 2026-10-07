# Arsitektur Fleet Agen Office Assistant

Dokumen ini menjelaskan topologi, layout kontainer, komponen, dan konfigurasi
teknis fleet yang berjalan di Lab VM (`10.216.4.80`). Untuk prosedur operasi
harian (deploy, password approver, Desktop, rollback), lihat `README.md` di
folder yang sama.

## 1. Ringkasan

Satu agen supervisor (Athena) plus enam agen spesialis, satu `office-gateway`
sebagai satu-satunya jalan ke sistem lab, dan satu Traefik (`office-edge`)
sebagai pintu depan HTTPS. Setiap agen hanya berbicara ke lab lewat endpoint
MCP gateway (`/mcp`, toolset `mcp-office`) dengan token per-role. Agen tidak
punya tool terminal, file, web, maupun browser: mereka membaca dan
**mengusulkan** perubahan. Manusia menyetujui tiap usulan di halaman Approvals.

## 2. Topologi

```
Operator (LAN / VPN)
  │  HTTPS :443, HTTP :80 (redirect)
  ▼
office-edge (Traefik v3.3)
  ├── /approvals/ ──► office-www (nginx, file statis console)
  │                     └── API /approvals/* ──► office-gateway :8080
  ├── /dash/ ──► hermes-agent :9119 (web dashboard Athena)
  └── /attu/, /toolbox/ ──► host (lihat edge/edge-routes)
  │
  │  HTTP dashboard langsung (tanpa Traefik)
  ├── hermes-agent      :9119  (Athena / supervisor)
  ├── hermes-lab-host   :9121
  ├── hermes-vector     :9122
  ├── hermes-cluster-gpu:9123
  ├── hermes-llm        :9124
  ├── hermes-obs        :9125
  └── hermes-ingress    :9126

Agen ──► office-gateway:8080/mcp (Bearer token per-role)
  │
  ├── Docker socket Lab VM (role lab-host, ingress)
  ├── host.docker.internal:19530 → milvus-dev (role vector)
  ├── 10.216.221.100 → RKE2/K8s + LiteLLM /llm/v1
  ├── 10.216.78.130  → Grafana + VictoriaMetrics/Prometheus
  ├── 10.216.203.132 → milvus-prod (baca)
  └── 10.216.78.129  → Rancher (cakupan obs)

Semua agen ──► LiteLLM (model qwen3.8-fast) untuk inferensi
```

Nama kontainer memakai prefix project compose `office-`, misalnya
`office-office-gateway-1`, `office-hermes-agent-1`, `office-hermes-obs-1`.

## 3. Layout compose (`docker-compose.yml`, network `office` 172.30.80.0/24)

| Service | Image | Port host | Volume data | Fungsi |
|---|---|---|---|---|
| `office-gateway-init` | busybox:1.36 | - | `office_gateway_data` | siapkan `/var/lib/hermes-office-gateway`, salin kubeconfig, `run once` |
| `office-gateway` | `office-gw:local` (build `Dockerfile.office-gateway`) | expose 8080 internal | `office_gateway_data` | API + MCP `/mcp`, mesin approval |
| `hermes-agent` | `nousresearch/hermes-agent:v2026.9.21` | `10.216.4.80:9119` | `hermes_supervisor_v2` | Athena (supervisor) |
| `hermes-lab-host` | sama | `:9121` | `hermes_lab_host_v2` | Hephaestus |
| `hermes-vector` | sama | `:9122` | `hermes_vector_v2` | Mnemosyne |
| `hermes-cluster-gpu` | sama | `:9123` | `hermes_cluster_gpu_v2` | Surtr |
| `hermes-llm` | sama | `:9124` | `hermes_llm_v2` | Iris |
| `hermes-obs` | sama | `:9125` | `hermes_obs_v2` | Argus |
| `hermes-ingress` | sama | `:9126` | `hermes_ingress_v2` | Janus |
| `office-www` | nginx:1.27-alpine | internal :80 | - | file statis console (`console/www`) |
| `office-edge` | traefik:v3.3 | `:80`, `:443` | - | terminasi TLS, auth Approvals, routing path |

Mount penting `office-gateway`: kode `office_gateway/` read-only (ship kode
tanpa rebuild image), `/var/run/docker.sock` read-only, `./edge` read-write
(tulis `edge-routes` + `routes.yml` setelah approval), `./certs` read-only,
`KUBECONFIG=/var/lib/hermes-office-gateway/kubeconfig`. Proses berjalan
sebagai UID 10001 + grup socket host (`DOCKER_GID`).

Setiap agen mount read-only: `hermes/<role>/config.yaml`,
`skills/<role>/`, dua cont-init (`08-hermes-cli`, `30-bot-mode-marker`).

Lab tanpa internet: compose memblokir DNS publik lewat `extra_hosts`
(air-gap) dan proxy mati `http://127.0.0.1:9`; `NO_PROXY` mencantumkan semua
IP lab per alamat (httpx mengabaikan entri CIDR).

## 4. Agen: persona, role, tool

| Persona | Service | Role id | Port | Skill | Tool MCP | Tulis |
|---|---|---|---|---|---|---|
| Athena | `hermes-agent` | `supervisor` | 9119 | office-supervisor | `fleet_status` + `message_agent` (Bot Mode) | tidak ada; delegasi ke spesialis |
| Hephaestus | `hermes-lab-host` | `lab-host` | 9121 | office-lab-host | `list_containers`, `inspect_container`, `tail_logs`, `host_resources`, `list_host_services`, `propose_restart`, `action_status` | restart kontainer/service (usulan) |
| Mnemosyne | `hermes-vector` | `vector` | 9122 | office-vector | `vector_status`, `milvus_databases`, `milvus_collections`, `milvus_collection`, `milvus_users`, `milvus_roles` | tidak ada (inspeksi saja) |
| Surtr | `hermes-cluster-gpu` | `cluster-gpu` | 9123 | office-cluster-gpu | `k8s_get`, `mig_map`, `gpu_usage` | tidak ada |
| Iris | `hermes-llm` | `llm` | 9124 | office-llm | `llm_status`, `list_models` | tidak ada |
| Argus | `hermes-obs` | `obs` | 9125 | office-obs | `metrics_query`, `grafana_dashboards`, `grafana_dashboard`, `grafana_panel`, `grafana_report`, `grafana_links`, `propose_dashboard`, `action_status` | dashboard Grafana baru (usulan); `grafana_report` menulis file HTML ke folder reports bersama |
| Janus | `hermes-ingress` | `ingress` | 9126 | office-ingress | `list_routes`, `edge_status`, `tls_status`, `tail_traefik_logs`, `validate_route_change`, `propose_route_change`, `propose_route_rollback`, `action_status` | route/rollback Traefik (usulan) |

### 4.1 Konfigurasi umum agen (`hermes/<role>/config.yaml`)

- Model `qwen3.8-fast` via provider `office-litellm` (`chat_completions`,
  timeout 60 dtk, thinking mati, context 131072).
- `max_turns: 6`, `api_max_retries: 1`, budget jalan 180 dtk.
- Toolset aktif di semua platform hanya `[mcp-office, skills]`;
  toolset `terminal, file, web, browser, code_execution, delegation, memory,
  session_search, todo, kanban, clarify, a2a, vision, image_gen, tts, cronjob`
  dimatikan.
- Memory jangka panjang mati (`memory_enabled: false`): agen hanya ingat isi
  chat yang sedang terbuka.
- Satu skill per role, auto-load `office-<role>` (`skills/<role>/SKILL.md`).
- MCP `office`: `http://office-gateway:8080/mcp`, header
  `Authorization: Bearer ${OFFICE_GATEWAY_TOKEN}`, timeout 15 dtk.

### 4.2 Delegasi Bot Chat

Athena mencapai spesialis lewat `message_agent` ke peer `peer-lab-host`,
`peer-ingress`, `peer-llm`, `peer-cluster-gpu`, `peer-vector`, `peer-obs`.
Tool itu **hanya ada di sesi berjudul persis `Bot Chat`** (plus marker
`bot_mode_marker` dari cont-init). Sesi lain hanya bisa menjawab status fleet.
Satu sesi dipegang satu proses TUI; tab dashboard/Desktop yang masih memegang
Bot Chat membuat jendela lain mendapat pesan "open in another window".
Tombol **Release Bot Chat lock** di halaman Approvals melepas pegangan itu
tanpa menghapus sesi.

## 5. office-gateway

- Serve HTTP `0.0.0.0:8080` (internal). Endpoint MCP: `POST /mcp`
  (`initialize`, `ping`, `tools/list`, `tools/call`).
- Auth: Bearer per-role (`OFFICE_GATEWAY_TOKEN_<ROLE>`), nilai harus unik
  antar role. Token approver hanya disuntik Traefik di halaman Approvals,
  tidak pernah diberikan ke agen.
- Batas jawaban: `RESULT_CAP = 14000` karakter per balasan MCP;
  daftar dipotong per budget (`fit_items`, default 1500; detail dashboard
  12000) dengan field `omitted`. Timeout tool 12 dtk, upstream 10 dtk.
- Tulis = usulan: `propose_*` menyimpan aksi + diff, manusia klik Approve di
  `/approvals/`, gateway mengeksekusi. Usulan kedaluwarsa setelah
  `OFFICE_ACTION_TTL_SECONDS` (default 600).
- Kode tool: `office_gateway/tools/<domain>.py`, didaftarkan di
  `tools/__init__.py` (`ALL_TOOLS`). File `.py` baru tidak aktif sebelum
  container `office-gateway` dibuat ulang.
- Route khusus: `POST /v1/approvals/bot-chat/release` (lepas TUI Bot Chat).

## 6. Konfigurasi teknis (file)

- `.env` (gitignored, hanya dibaca `office-gateway`): token per-role
  `OFFICE_GATEWAY_TOKEN_*` + `OFFICE_GATEWAY_TOKEN_APPROVER`,
  `OFFICE_LITELLM_URL` / `OFFICE_LITELLM_MASTER_KEY`, `OFFICE_METRICS_URL`,
  `OFFICE_GRAFANA_BASE_URL` / `OFFICE_GRAFANA_TOKEN` /
  `OFFICE_GRAFANA_FOLDER_UID` / `OFFICE_GRAFANA_DATASOURCE_UID` /
  `OFFICE_GRAFANA_DASHBOARDS` (format `name=uid`, opsional, hanya pin) /
  `OFFICE_GRAFANA_PANELS`, `OFFICE_MILVUS_DEV_URL` /
  `OFFICE_MILVUS_DEV_TOKEN`, `OFFICE_KUBECONFIG`,
  `OFFICE_MIG_EXPECTED` (contoh `1g.18gb:7,2g.35gb:2,3g.71gb:2,4g.71gb:1`),
  `OFFICE_SERVICE_URLS`, `OFFICE_VECTOR_INSTANCES`, path edge
  (`OFFICE_EDGE_ROUTES_PATH`, `OFFICE_EDGE_DYNAMIC_PATH`,
  `OFFICE_EDGE_BACKUP_DIR`, `OFFICE_EDGE_TLS_CERT_PATH`), `OFFICE_CONSOLE_URL`,
  `DOCKER_GID`. Contoh lengkap: `.env.example`.
- `models.env`: hanya `OFFICE_LLM_BASE_URL` (dibaca semua agen).
- `hermes/<role>/.env`: `OFFICE_GATEWAY_TOKEN`, `OFFICE_LLM_API_KEY`
  (virtual key LiteLLM agen itu), `API_SERVER_KEY`,
  `HERMES_DASHBOARD_SESSION_TOKEN`. Supervisor juga memegang
  `HERMES_PEER_PEER_<ROLE>_KEY` tiap spesialis + login dashboard.
- `.local-login` (gitignored): password dashboard, password approver,
  session token. Bukan untuk di-commit.
- `edge/edge-routes` (sumber route, format `path host [:port] [strip]`),
  dirender ke `edge/traefik-dynamic/routes.yml`; 10 backup terbaru disimpan.
  `core.yml` dirender dari template (berisi token, gitignored).
- `certs/tls.crt`: leaf 90 hari, SAN IP `10.216.4.80` + `127.0.0.1` dari Lab
  Internal CA (`ca/` hanya di host). Klien perlu trust `ca/ca.crt` sekali.

## 7. Batasan yang disengaja

Chat umum di luar Bot Chat tidak bisa menyuruh spesialis; memory antar sesi
tidak ada; troubleshooting terbatas pada data yang dimiliki tool domainnya
(tanpa shell, file, browser, atau PromQL bebas — obs memakai named query dan
query tersimpan panel Grafana); satu giliran berhenti setelah 6 langkah.
