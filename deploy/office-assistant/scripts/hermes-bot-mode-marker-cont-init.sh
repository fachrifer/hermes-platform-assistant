#!/command/with-contenv sh
set -eu
HOME_DIR=/opt/data
MARKER="$HOME_DIR/profile.yaml"
if [ ! -f "$MARKER" ]; then
  printf 'ui_meta:\n  hermes-bots: {}\n' > "$MARKER"
elif ! grep -q 'hermes-bots' "$MARKER"; then
  echo "bot-mode-marker: $MARKER exists without hermes-bots; refusing to edit" >&2
  exit 1
fi
if [ ! -f "$HOME_DIR/.no-bundled-skills" ]; then
  printf 'Office fleet: bundled skills are not seeded.\n' > "$HOME_DIR/.no-bundled-skills"
fi
owner="$(stat -c %u:%g "$HOME_DIR")"
chown "$owner" "$MARKER" "$HOME_DIR/.no-bundled-skills"
# Docker creates skills/ as root for the nested read-only skill mount; Hermes needs it writable.
if [ -d "$HOME_DIR/skills" ]; then
  chown "$owner" "$HOME_DIR/skills"
fi
