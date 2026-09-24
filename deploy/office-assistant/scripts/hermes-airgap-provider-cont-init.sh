#!/command/with-contenv sh
set -e
# Athena has no LiteLLM config template; still patch Hermes so bot-peer
# messaging does not resolve OPENAI_API_KEY to OpenRouter, then stamp
# HERMES_HOME/.env (bind-mounted config.yaml stays :ro).
PY="/opt/hermes/.venv/bin/python3"
if [ ! -x "$PY" ]; then
  PY="python3"
fi
"$PY" /opt/office/patch-hermes-airgap-provider.py
"$PY" /opt/office/apply-office-llm-config.py
