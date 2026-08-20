#!/usr/bin/env bash
set -euo pipefail

# Run on a networked machine. Pulls/builds the fleet images and writes a gzip
# tarball for air-gap docker load on the Lab VM.
#
# Usage: ./scripts/save-images.sh [path/to/office-fleet-images.tar.gz]

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

OUT="${1:-$DEFAULT_TARBALL}"
HERMES_IMAGE="$(office_hermes_image)"
: "${OFFICE_GW_IMAGE:=office-gw:local}"
: "${BUSYBOX_IMAGE:=busybox:1.36}"
mkdir -p "$(dirname "$OUT")"

echo "pull $HERMES_IMAGE"
docker pull "$HERMES_IMAGE"
echo "pull $BUSYBOX_IMAGE"
docker pull "$BUSYBOX_IMAGE"
echo "build $OFFICE_GW_IMAGE"
docker build -f "$REPO_ROOT/Dockerfile.office-gateway" -t "$OFFICE_GW_IMAGE" "$REPO_ROOT"

echo "save $HERMES_IMAGE $BUSYBOX_IMAGE $OFFICE_GW_IMAGE -> $OUT"
docker save "$HERMES_IMAGE" "$BUSYBOX_IMAGE" "$OFFICE_GW_IMAGE" | gzip > "$OUT"
echo "wrote $OUT"
