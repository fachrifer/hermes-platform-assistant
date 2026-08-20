from __future__ import annotations

from office_gateway.roles import can_write


class ActionError(Exception):
    pass


def validate_propose(config, role: str, action: str, target: str) -> None:
    if not can_write(role):
        raise ActionError("role cannot write")
    allowed = config.write_targets.get(role, frozenset())
    if target not in allowed:
        raise ActionError(f"target {target} not in allowlist")
    if config.vector_env.get(target) == "prod":
        raise ActionError("writes disabled for prod")


class ActionService:
    def __init__(self, config, store) -> None:
        self.config = config
        self.store = store

    def propose(self, role: str, action: str, target: str, params: dict | None = None):
        if action != "restart_service":
            raise ActionError(f"unknown action: {action}")
        validate_propose(self.config, role, action, target)
        summary = f"restart {target}"
        return self.store.propose(
            action=action,
            target=target,
            params=params,
            summary=summary,
        )

    def execute(self, role: str, action_id: str):
        if not can_write(role):
            raise ActionError("role cannot write")
        action = self.store.get_action(action_id)
        if action is None:
            raise ActionError("action not found")
        action = self.store.mark_expired_if_needed(action)
        if action.status == "expired":
            raise ActionError("action expired")
        action = self.store.claim_pending(action_id)
        if action is None:
            raise ActionError("action already executed or not pending")
        validate_propose(self.config, role, action.action, action.target)
        result = self.store.mark_executed(
            action_id,
            ok=False,
            detail="restart adapter not available",
        )
        if result is None:
            raise ActionError("action already executed or not pending")
        return result
