#!/command/with-contenv sh
set -e
# Image PATH does not include the venv. Recreate drops a manual
# /usr/local/bin/hermes, so restore it on every boot before cron/CLI.
ln -sf /opt/hermes/.venv/bin/hermes /usr/local/bin/hermes
