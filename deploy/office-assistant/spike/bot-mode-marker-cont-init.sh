#!/command/with-contenv sh
set -eu
MARKER=/opt/data/profile.yaml
if [ ! -f "$MARKER" ]; then
  printf 'ui_meta:\n  hermes-bots: {}\n' > "$MARKER"
elif ! grep -q 'hermes-bots' "$MARKER"; then
  echo "bot-mode-marker: $MARKER exists without hermes-bots; refusing to edit" >&2
  exit 1
fi
chown "$(stat -c %u:%g /opt/data)" "$MARKER"
