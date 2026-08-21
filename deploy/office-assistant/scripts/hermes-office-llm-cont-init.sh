#!/command/with-contenv sh
set -e
# Runs after Hermes stage2 seeds HERMES_HOME. Stamps LiteLLM credentials
# from Docker env_file so gateway run does not send a dummy bearer token.
python3 /opt/office/apply-office-llm-config.py
