# Agent guardrails: Lab VM `10.216.4.80`

The Hermes office fleet runs live on the Lab VM in `/home/timai/hermes-assistant`
(compose project `office`). This repository, branch `fleet/sync-vm-20260924` and
its descendants, is the source of truth for that directory. Read this before you
touch anything that reaches the VM.

## What went wrong before

An older checkout (`hermes-platform-assistant`, "August layout") ran
`ship-to-vm.sh`, which does `rsync -az --delete` of its own stale tree onto
`/home/timai/hermes-assistant`. That removed the fleet layout, env files and certs
and had to be rolled back from backups. Do not repeat it.

## Hard rules

1. Update the Lab only with `deploy/office-assistant/scripts/ship-phase1b.ps1`
   (ships committed files via `git archive`) and `unpack-phase1b.sh` (makes a
   backup first, never overwrites `.env`, certs or gateway-owned route files).
   Follow `deploy/office-assistant/README.md`.
2. Never run `rsync --delete`, `rm -rf`, `docker compose down -v`, or
   `docker volume rm` against the Lab directory or the `office_*` volumes without
   the user's explicit instruction in the current conversation.
3. Never ship from any other checkout (`hermes-platform-assistant`, old worktrees,
   `main`). If the VM layout and your tree disagree, the VM wins: pull its files,
   do not push yours.
4. Do not bump `HERMES_IMAGE` (pinned `nousresearch/hermes-agent:v2026.9.21`) or
   rotate tokens and keys that already work unless asked.
5. `scripts/deploy.sh` refuses to run on purpose (`OFFICE_ALLOW_LAB_OVERWRITE=1`
   is for a throwaway host only). Do not set it for the Lab.
6. Before any change on the VM, take a backup (`tar` of the directory, mode 600) and
   tell the user what you are about to do. Prefer read-only checks first
   (`docker compose ps`, `docker logs`, `status.sh`, `doctor.sh`).
7. Secrets stay on the VM and in the user's hands. Do not commit `.env`, certs,
   `*.htpasswd`, kubeconfigs or image tarballs, and do not print key values.
   The user pastes keys themselves; SSH is key-only (no passwords).

## Before you finish a change

- Run the tests: `python -m pytest -q tests` (or the `office-gw-test` Docker image).
- Keep shell scripts LF and executable; `ship-phase1b.ps1` rejects CRLF and a
  dirty tree under the shipped paths.
- Commit to a branch; never force-push and never push to `main` without being asked.
