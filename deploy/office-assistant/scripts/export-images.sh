#!/usr/bin/env bash
# Build/pull office-assistant images on a networked PC, then pack for an air-gapped VM.
# Default platform is linux/amd64 (VM 10.216.4.80). Override with OFFICE_IMAGE_PLATFORM.
#
# On Apple Silicon, `docker pull --platform` often leaves a multi-arch index tagged
# :latest (empty Architecture). We resolve the PLATFORM digest via buildx imagetools
# and tag that concrete image before saving.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEPLOY="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT="$(cd "$DEPLOY/../.." && pwd)"
DOCKERFILE="$ROOT/Dockerfile.office-gateway"
OUT_DIR="${OFFICE_ASSISTANT_BUNDLE_DIR:-$DEPLOY/dist}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BUNDLE="$OUT_DIR/office-assistant-images-${STAMP}.tar"
MANIFEST="$OUT_DIR/office-assistant-images-${STAMP}.txt"

GATEWAY_IMAGE="${OFFICE_GATEWAY_IMAGE:-hermes-office-gateway:1.0.0}"
HERMES_IMAGE="${HERMES_IMAGE:-nousresearch/hermes-agent:latest}"
PROXY_IMAGE="${DASHBOARD_PROXY_IMAGE:-nginx:1.27-alpine}"
PLATFORM="${OFFICE_IMAGE_PLATFORM:-linux/amd64}"
EXPECTED_ARCH="${PLATFORM##*/}"

_arch_of() {
  docker image inspect --format '{{.Architecture}}' "$1" 2>/dev/null || true
}

_require_arch() {
  local img="$1"
  local arch
  arch="$(_arch_of "$img")"
  if [[ -z "$arch" ]]; then
    echo "ERROR: image missing or multi-arch index (no Architecture): $img" >&2
    docker image inspect "$img" --format 'Os={{.Os}} Arch={{.Architecture}} Id={{.Id}}' >&2 || true
    exit 1
  fi
  echo "    $img → $arch"
  if [[ "$arch" != "$EXPECTED_ARCH" ]]; then
    echo "ERROR: $img is $arch, need $EXPECTED_ARCH" >&2
    exit 1
  fi
}

_materialize_remote() {
  local image="$1"
  local digest
  echo "==> Resolve $PLATFORM digest for $image"
  digest="$(
    docker buildx imagetools inspect "$image" \
      --raw 2>/dev/null \
      | python3 -c "
import json,sys
idx=json.load(sys.stdin)
want='${PLATFORM}'
for m in idx.get('manifests',[]):
    p=m.get('platform') or {}
    plat=f\"{p.get('os','')}/{p.get('architecture','')}\"
    if plat==want and m.get('annotations',{}).get('vnd.docker.reference.type')!='attestation-manifest':
        print(m['digest']); break
else:
    raise SystemExit('no digest for '+want)
"
  )"
  echo "    digest=$digest"
  docker pull --platform "$PLATFORM" "${image%:*}@$digest"
  local id
  id="$(docker image inspect "${image%:*}@$digest" --format '{{.Id}}')"
  docker tag "$id" "$image"
}

if [[ ! -f "$DOCKERFILE" ]]; then
  echo "Dockerfile not found: $DOCKERFILE" >&2
  exit 1
fi

if ! docker buildx version >/dev/null 2>&1; then
  echo "ERROR: docker buildx is required" >&2
  exit 1
fi

docker buildx use default >/dev/null 2>&1 \
  || docker buildx use desktop-linux >/dev/null 2>&1 \
  || true

mkdir -p "$OUT_DIR"

echo "==> Target platform: $PLATFORM (expected arch=$EXPECTED_ARCH)"
echo "==> Host arch: $(uname -m)"

echo "==> Removing previous local tags (if any)…"
docker image rm -f "$GATEWAY_IMAGE" "$HERMES_IMAGE" "$PROXY_IMAGE" 2>/dev/null || true

_materialize_remote "$HERMES_IMAGE"
_require_arch "$HERMES_IMAGE"

_materialize_remote "$PROXY_IMAGE"
_require_arch "$PROXY_IMAGE"

echo "==> Build office-gateway image: $GATEWAY_IMAGE"
docker buildx build \
  --platform "$PLATFORM" \
  --load \
  -f "$DOCKERFILE" \
  -t "$GATEWAY_IMAGE" \
  "$ROOT"
_require_arch "$GATEWAY_IMAGE"

echo "==> Save images → $BUNDLE"
docker save -o "$BUNDLE" "$GATEWAY_IMAGE" "$HERMES_IMAGE" "$PROXY_IMAGE"

{
  echo "created_at_utc=$STAMP"
  echo "platform=$PLATFORM"
  echo "gateway_image=$GATEWAY_IMAGE"
  echo "hermes_image=$HERMES_IMAGE"
  echo "proxy_image=$PROXY_IMAGE"
  echo "bundle=$(basename "$BUNDLE")"
  docker image inspect --format '{{.Id}} {{.Os}}/{{.Architecture}} {{.RepoTags}}' \
    "$GATEWAY_IMAGE" "$HERMES_IMAGE" "$PROXY_IMAGE"
} >"$MANIFEST"

echo "==> Done (verified $PLATFORM)"
cat "$MANIFEST"
echo
echo "Ship: $DEPLOY/scripts/ship-to-vm.sh $BUNDLE"
