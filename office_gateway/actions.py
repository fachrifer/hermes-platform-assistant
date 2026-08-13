"""Propose / execute orchestration with hard allowlist checks."""

from __future__ import annotations

from dataclasses import asdict

from office_gateway.adapters import WriteAdapter, WriteResult
from office_gateway.config import ALLOWLISTED_ACTIONS, GatewayConfig
from office_gateway.store import GatewayStore, PendingAction


class ActionError(ValueError):
    """Raised when a propose/execute request is rejected by policy."""


def _summary(action: str, target: str, params: dict) -> str:
    if action == "restart_service":
        return f"Restart service `{target}`"
    if action == "scale_replicas":
        return f"Scale `{target}` to {params.get('replicas')} replicas"
    if action == "clear_queue":
        return f"Clear queue on `{target}`"
    if action == "set_feature_flag":
        return (
            f"Set feature flag `{params.get('flag')}` on `{target}` "
            f"to `{params.get('value')}`"
        )
    return f"{action} on `{target}`"


def validate_propose(config: GatewayConfig, action: str, target: str, params: dict) -> dict:
    if action not in ALLOWLISTED_ACTIONS:
        raise ActionError(f"action not allowlisted: {action}")
    if target not in config.service_urls and target not in config.write_targets:
        raise ActionError(f"unknown target: {target}")
    if not config.allows(target, action):
        raise ActionError(f"action `{action}` not allowed for target `{target}`")

    cleaned = dict(params or {})
    if action == "scale_replicas":
        if "replicas" not in cleaned:
            raise ActionError("scale_replicas requires params.replicas")
        try:
            replicas = int(cleaned["replicas"])
        except (TypeError, ValueError) as exc:
            raise ActionError("params.replicas must be an integer") from exc
        bounds = config.scale_bounds.get(target)
        if bounds is None:
            raise ActionError(f"no scale bounds configured for `{target}`")
        low, high = bounds
        if replicas < low or replicas > high:
            raise ActionError(f"replicas {replicas} outside bounds [{low}, {high}]")
        cleaned = {"replicas": replicas}
    elif action == "set_feature_flag":
        flag = cleaned.get("flag")
        if not isinstance(flag, str) or not flag.strip():
            raise ActionError("set_feature_flag requires params.flag")
        if "value" not in cleaned:
            raise ActionError("set_feature_flag requires params.value")
        cleaned = {"flag": flag.strip(), "value": cleaned["value"]}
    elif action in {"restart_service", "clear_queue"}:
        cleaned = {}
    return cleaned


class ActionService:
    def __init__(
        self,
        config: GatewayConfig,
        store: GatewayStore,
        adapter: WriteAdapter | None = None,
    ):
        self.config = config
        self.store = store
        self.adapter = adapter or WriteAdapter(config.adapter_endpoints)

    def propose(self, action: str, target: str, params: dict | None = None) -> PendingAction:
        cleaned = validate_propose(self.config, action, target, params or {})
        return self.store.propose(
            action=action,
            target=target,
            params=cleaned,
            summary=_summary(action, target, cleaned),
            ttl_seconds=self.config.action_ttl_seconds,
        )

    async def execute(self, action_id: str) -> dict:
        pending = self.store.get_action(action_id)
        if pending is None:
            raise ActionError("unknown action_id")
        pending = self.store.mark_expired_if_needed(pending)
        if pending.status == "expired":
            raise ActionError("action expired; propose again")
        if pending.status != "pending":
            raise ActionError(f"action not executable (status={pending.status})")

        result: WriteResult = await self.adapter.execute(
            pending.action, pending.target, pending.params
        )
        self.store.mark_executed(action_id, ok=result.ok, detail=result.detail)
        payload = {
            "action_id": action_id,
            "status": "executed" if result.ok else "failed",
            "detail": result.detail,
            "action": asdict(pending),
        }
        if not result.ok:
            raise ActionError(result.detail)
        return payload
