# Shared paths for office-assistant deploy scripts. Source only; do not execute.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$DEPLOY_DIR/../.." && pwd)"
DEFAULT_TARBALL="$DEPLOY_DIR/images/office-fleet-images.tar.gz"
HERMES_ROLES="supervisor lab-host vector cluster-gpu llm-edge obs"
BUSYBOX_IMAGE="busybox:1.36"
OFFICE_GW_IMAGE="office-gw:local"

office_hermes_image() {
  if [[ -n "${HERMES_IMAGE:-}" ]]; then
    printf '%s\n' "$HERMES_IMAGE"
    return
  fi
  local file line
  for file in "$DEPLOY_DIR/.env" "$DEPLOY_DIR/.env.example"; do
    if [[ -f "$file" ]]; then
      line="$(grep -E '^HERMES_IMAGE=' "$file" | tail -n 1 || true)"
      if [[ -n "$line" ]]; then
        printf '%s\n' "${line#HERMES_IMAGE=}"
        return
      fi
    fi
  done
  printf '%s\n' "nousresearch/hermes-agent:v2026.8.3"
}

office_compose() {
  docker compose --project-directory "$DEPLOY_DIR" -f "$DEPLOY_DIR/docker-compose.yml" "$@"
}
