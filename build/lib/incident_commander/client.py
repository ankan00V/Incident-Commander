"""Typed WebSocket client for the Incident Commander environment."""

from __future__ import annotations

from typing import Any

from openenv.core import EnvClient
from openenv.core.client_types import StepResult

from .models import IncidentAction, IncidentObservation, IncidentState


class IncidentEnv(EnvClient[IncidentAction, IncidentObservation, IncidentState]):
    """Persistent OpenEnv client backed by a WebSocket session."""

    def _step_payload(self, action: IncidentAction) -> dict[str, Any]:
        return action.model_dump(exclude_none=True, exclude={"metadata"})

    def _parse_result(self, payload: dict[str, Any]) -> StepResult[IncidentObservation]:
        observation = IncidentObservation.model_validate(
            {
                **payload.get("observation", {}),
                "reward": payload.get("reward"),
                "done": payload.get("done", False),
            }
        )
        return StepResult(
            observation=observation,
            reward=payload.get("reward"),
            done=payload.get("done", False),
        )

    def _parse_state(self, payload: dict[str, Any]) -> IncidentState:
        return IncidentState.model_validate(payload)

    async def rollback(self, service_name: str, version: str) -> StepResult[IncidentObservation]:
        return await self.step(
            IncidentAction(
                action_type="rollback",
                service_name=service_name,
                version=version,
            )
        )

    async def toggle(
        self, feature_flag: str, enabled: bool
    ) -> StepResult[IncidentObservation]:
        return await self.step(
            IncidentAction(
                action_type="toggle_feature",
                feature_flag=feature_flag,
                enabled=enabled,
            )
        )

    async def page(self, team: str) -> StepResult[IncidentObservation]:
        return await self.step(IncidentAction(action_type="page_team", team=team))

    async def submit_rca(self, message: str) -> StepResult[IncidentObservation]:
        return await self.step(
            IncidentAction(action_type="submit_rca", message=message)
        )


IncidentCommanderEnv = IncidentEnv

__all__ = ["IncidentCommanderEnv", "IncidentEnv"]
