# Shared paths for office-assistant deploy scripts. Source only; do not execute.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
# Repo checkout: compose lives in deploy/office-assistant, gateway sources at repo root.
# Flattened Lab VM: /home/timai/hermes-assistant contains compose + office_gateway.
if [[ -f "$DEPLOY_DIR/Dockerfile.office-gateway" && -d "$DEPLOY_DIR/office_gateway" ]]; then
  REPO_ROOT="$DEPLOY_DIR"
else
  REPO_ROOT="$(cd "$DEPLOY_DIR/../.." && pwd)"
fi
DEFAULT_TARBALL="$DEPLOY_DIR/images/office-fleet-images.tar.gz"
HERMES_ROLES="supervisor lab-host ingress llm cluster-gpu vector obs"
BUSYBOX_IMAGE="busybox:1.36"
CONSOLE_IMAGE="nginx:1.27-alpine"
OFFICE_GW_IMAGE="office-gw:local"
# Lab VM is linux/amd64. Laptop save-images must cross-build; native arm64
# tarballs fail init with exec format error (compose exit 255).
OFFICE_IMAGE_PLATFORM="${OFFICE_IMAGE_PLATFORM:-linux/amd64}"

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
  printf '%s\n' "nousresearch/hermes-agent:v2026.9.21"
}

office_compose() {
  docker compose --project-directory "$DEPLOY_DIR" -f "$DEPLOY_DIR/docker-compose.yml" "$@"
}

office_docker_arch() {
  case "$(docker info --format '{{.Architecture}}' 2>/dev/null)" in
    x86_64|amd64) printf '%s\n' amd64 ;;
    aarch64|arm64) printf '%s\n' arm64 ;;
    *) docker info --format '{{.Architecture}}' 2>/dev/null || true ;;
  esac
}

office_image_arch() {
  docker image inspect "$1" --format '{{.Architecture}}' 2>/dev/null || true
}

# Flattened Lab tree has Dockerfile.office-gateway next to compose.
# The git checkout keeps that file at the repo root, not in deploy/office-assistant.
office_is_lab_tree() {
  [[ "$REPO_ROOT" == "$DEPLOY_DIR" ]]
}

office_env_value() {
  local key="$1"
  local default="${2:-}"
  local line=""
  if [[ -f "$DEPLOY_DIR/.env" ]]; then
    line="$(grep -E "^${key}=" "$DEPLOY_DIR/.env" | tail -n 1 || true)"
  fi
  if [[ -n "$line" ]]; then
    printf '%s\n' "${line#*=}"
  else
    printf '%s\n' "$default"
  fi
}

# Stop any container still publishing host port from HERMES_*_PUBLISH (ip:port).
# The Lab already runs an older hermes-assistant stack on :9119 / :80.
office_stop_port_holders() {
  local spec="$1"
  local port="${spec##*:}"
  local ids names
  port="${port%%/*}"
  if [[ ! "$port" =~ ^[0-9]+$ ]]; then
    return 0
  fi
  ids="$(docker ps -q --filter "publish=${port}" || true)"
  if [[ -z "$ids" ]]; then
    return 0
  fi
  names="$(docker ps --filter "publish=${port}" --format '{{.Names}}' | tr '\n' ' ')"
  echo "stopping containers still publishing :${port}: ${names}"
  # shellcheck disable=SC2086
  docker stop $ids >/dev/null
}
