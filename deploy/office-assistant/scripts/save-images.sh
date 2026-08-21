#!/usr/bin/env bash
set -euo pipefail

# Run on a networked machine. Pulls/builds linux/amd64 fleet images (Lab VM
# arch) and writes a gzip tarball for air-gap docker load.
#
# Usage: ./scripts/save-images.sh [path/to/office-fleet-images.tar.gz]
# Override: OFFICE_IMAGE_PLATFORM=linux/arm64 ./scripts/save-images.sh

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

OUT="${1:-$DEFAULT_TARBALL}"
HERMES_IMAGE="$(office_hermes_image)"
: "${OFFICE_GW_IMAGE:=office-gw:local}"
: "${BUSYBOX_IMAGE:=busybox:1.36}"
: "${CONSOLE_IMAGE:=nginx:1.27-alpine}"
: "${OFFICE_IMAGE_PLATFORM:=linux/amd64}"
mkdir -p "$(dirname "$OUT")"

want_arch="${OFFICE_IMAGE_PLATFORM#linux/}"

require_arch() {
  local image="$1"
  local got
  got="$(office_image_arch "$image")"
  if [[ "$got" != "$want_arch" ]]; then
    echo "error: $image is ${got:-unknown} after pin; wanted $want_arch" >&2
    exit 1
  fi
  echo "$image $got"
}

# Docker Desktop keeps a multi-arch index on the tag; inspect/save then follow
# the host (arm64). Rebuild FROM the image with --platform so the tag is a
# single-arch image the Lab VM can load.
pin_single_platform() {
  local image="$1"
  echo "pin $image -> $OFFICE_IMAGE_PLATFORM"
  docker pull --platform "$OFFICE_IMAGE_PLATFORM" "$image"
  docker build --platform "$OFFICE_IMAGE_PLATFORM" -t "$image" - <<EOF
FROM $image
EOF
  require_arch "$image"
}

echo "platform $OFFICE_IMAGE_PLATFORM"
pin_single_platform "$HERMES_IMAGE"
pin_single_platform "$BUSYBOX_IMAGE"
pin_single_platform "$CONSOLE_IMAGE"
echo "build $OFFICE_GW_IMAGE"
docker build --platform "$OFFICE_IMAGE_PLATFORM" -f "$REPO_ROOT/Dockerfile.office-gateway" -t "$OFFICE_GW_IMAGE" "$REPO_ROOT"
require_arch "$OFFICE_GW_IMAGE"

echo "save $HERMES_IMAGE $BUSYBOX_IMAGE $CONSOLE_IMAGE $OFFICE_GW_IMAGE -> $OUT"
docker save "$HERMES_IMAGE" "$BUSYBOX_IMAGE" "$CONSOLE_IMAGE" "$OFFICE_GW_IMAGE" | gzip > "$OUT"
echo "wrote $OUT"
