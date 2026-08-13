#!/bin/sh
set -eu
# Named volumes mount as root; ensure the gateway user can write SQLite.
mkdir -p /var/lib/hermes-office-gateway
chown -R hermes:hermes /var/lib/hermes-office-gateway

# Docker socket is root:docker on the VM. Join that GID so hermes can ping
# the daemon without staying root.
sock="${OFFICE_DOCKER_SOCK:-/var/run/docker.sock}"
if [ -S "$sock" ]; then
  gid="$(stat -c %g "$sock")"
  grp="$(getent group "$gid" | cut -d: -f1 || true)"
  if [ -z "$grp" ]; then
    groupadd -g "$gid" dockersock
    grp=dockersock
  fi
  exec runuser -u hermes -g hermes -G "$grp" -- "$@"
fi

exec runuser -u hermes -- "$@"
