#!/command/with-contenv sh
set -e
# Runs after Hermes stage2 seeds HERMES_HOME. Stop OpenRouter auto-detect
# on OPENAI_API_KEY, then stamp LiteLLM credentials from Docker env_file.
# Image python3 is system Python; hermes_cli lives in the venv.
PY="/opt/hermes/.venv/bin/python3"
if [ ! -x "$PY" ]; then
  PY="python3"
fi
"$PY" /opt/office/patch-hermes-airgap-provider.py
"$PY" /opt/office/apply-office-llm-config.py
