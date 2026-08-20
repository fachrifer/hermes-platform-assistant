from __future__ import annotations

from office_gateway.roles import can_write


class ActionError(Exception):
    pass


def validate_propose(config, role: str, action: str, target: str) -> None:
    if not can_write(role):
        raise ActionError("role cannot write")
    if config.vector_env.get(target) == "prod":
        raise ActionError("writes disabled for prod")
    allowed = config.write_targets.get(role, frozenset())
    if target not in allowed:
        raise ActionError("unknown target")


def validate_execute(config, role: str, proposed_role: str, action: str, target: str) -> None:
    if role != proposed_role:
        raise ActionError("forbidden")
    validate_propose(config, role, action, target)


class ActionService:
    def __init__(self, config, store) -> None:
        self.config = config
        self.store = store

    def propose(self, role: str, action: str, target: str, params: dict | None = None):
        if action != "restart_service":
            raise ActionError("action not allowlisted")
        validate_propose(self.config, role, action, target)
        summary = f"restart {target}"
        return self.store.propose(
            action=action,
            target=target,
            role=role,
            params=params,
            summary=summary,
        )

    def execute(self, role: str, action_id: str):
        if not can_write(role):
            raise ActionError("role cannot write")
        action = self.store.get_action(action_id)
        if action is None:
            raise ActionError("action not found")
        validate_execute(self.config, role, action.role, action.action, action.target)
        claimed = self.store.claim_pending(action_id)
        if claimed is None:
            action = self.store.get_action(action_id)
            if action is None:
                raise ActionError("action not found")
            if action.status == "expired" or (
                action.status == "pending" and self.store._is_expired(action)
            ):
                self.store.mark_expired_if_needed(action)
                raise ActionError("action expired")
            raise ActionError("action not pending")
        result = self.store.mark_executed(
            action_id,
            ok=False,
            detail="failed:adapter_unavailable",
        )
        if result is None:
            raise ActionError("action not pending")
        return result
