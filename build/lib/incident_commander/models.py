"""Typed models for the Incident Commander OpenEnv environment."""

from __future__ import annotations

from typing import Any, Literal

from openenv.core.env_server.types import Action, Observation, State
from pydantic import BaseModel, Field

Difficulty = Literal["easy", "medium", "hard"]
ServiceHealth = Literal["healthy", "degraded", "down"]
LogLevel = Literal["INFO", "WARN", "ERROR", "FATAL"]
ActionType = Literal[
    "run_query",
    "scale_service",
    "restart_pod",
    "rollback",
    "page_team",
    "toggle_feature",
    "post_status",
    "submit_rca",
]


class ServiceStatus(BaseModel):
    """Current status for one production service."""

    name: str = Field(..., description="Service name")
    status: ServiceHealth = Field(..., description="Current health state")
    error_rate: float = Field(..., ge=0.0, description="Request error rate")
    p99_latency_ms: float = Field(..., ge=0.0, description="P99 latency in milliseconds")
    replicas: int = Field(..., ge=1, description="Current replica count")


class LogEntry(BaseModel):
    """Structured log line exposed to the agent."""

    timestamp: str = Field(..., description="HH:MM:SS timestamp")
    level: LogLevel = Field(..., description="Log severity")
    service: str = Field(..., description="Service emitting the log")
    message: str = Field(..., description="Log message")


class ActionTrace(BaseModel):
    """Recorded action taken during the episode."""

    step: int = Field(..., description="Step number")
    action_type: ActionType = Field(..., description="Action type executed")
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Serialized action parameters excluding action_type",
    )
    outcome: str = Field(..., description="Human-readable result of the action")
    reward_delta: float = Field(..., description="Reward delta produced by the step")


class ProgressMetric(BaseModel):
    """Dense progress metric shown to the agent."""

    name: str = Field(..., description="Metric label")
    score: float = Field(..., ge=0.0, le=1.0, description="Metric score")
    description: str = Field(..., description="What the metric measures")


class IncidentAction(Action):
    """Atomic action for the incident-response environment."""

    action_type: ActionType = Field(..., description="Operation to execute")
    service_name: str | None = Field(
        default=None,
        description="Service to scale, restart, or roll back when applicable",
    )
    replicas: int | None = Field(
        default=None, ge=1, description="Replica target for scale_service"
    )
    version: str | None = Field(
        default=None, description="Deployment version for rollback"
    )
    team: str | None = Field(default=None, description="On-call team to page")
    query: str | None = Field(
        default=None, description="Free-text query for logs or metrics"
    )
    feature_flag: str | None = Field(
        default=None, description="Feature flag to toggle"
    )
    enabled: bool | None = Field(
        default=None, description="Desired enabled state for a feature flag"
    )
    message: str | None = Field(
        default=None, description="Status update or RCA text"
    )


class IncidentObservation(Observation):
    """Observation returned from reset() and step()."""

    task_id: str = Field(..., description="Current scenario identifier")
    difficulty: Difficulty = Field(..., description="Task difficulty")
    title: str = Field(..., description="Task title")
    objective: str = Field(..., description="Concrete success criterion")
    instructions: str = Field(..., description="Operating instructions")
    step_count: int = Field(..., description="Current step count")
    steps_remaining: int = Field(..., description="Remaining step budget")
    services: list[ServiceStatus] = Field(
        default_factory=list,
        description="Current health of all services in the incident",
    )
    recent_logs: list[LogEntry] = Field(
        default_factory=list,
        description="Recent logs visible to the incident commander",
    )
    active_alerts: list[str] = Field(
        default_factory=list,
        description="Current alert feed entries",
    )
    metrics: dict[str, float] = Field(
        default_factory=dict,
        description="Current high-level metrics for the incident",
    )
    incident_id: str = Field(..., description="Stable incident identifier")
    incident_started_at: str = Field(..., description="Incident start timestamp")
    elapsed_seconds: int = Field(..., ge=0, description="Elapsed time in seconds")
    affected_users: int = Field(..., ge=0, description="Estimated affected users")
    feature_flags: dict[str, bool] = Field(
        default_factory=dict,
        description="Current feature-flag state exposed to the agent",
    )
    paged_teams: list[str] = Field(
        default_factory=list,
        description="Teams paged so far",
    )
    status_updates: list[str] = Field(
        default_factory=list,
        description="Public status-page updates posted so far",
    )
    investigation_findings: list[str] = Field(
        default_factory=list,
        description="Evidence surfaced through relevant queries",
    )
    actions_taken: list[ActionTrace] = Field(
        default_factory=list,
        description="Full action trace for the episode so far",
    )
    progress: list[ProgressMetric] = Field(
        default_factory=list,
        description="Deterministic sub-scores used for dense reward shaping",
    )
    resolved: bool = Field(..., description="Whether the incident is operationally resolved")
    last_action_result: str = Field(
        default="Episode ready.",
        description="Human-readable description of the latest action result",
    )


class IncidentState(State):
    """Serializable environment state used for grading and inspection."""

    task_id: str = Field(..., description="Current scenario identifier")
    difficulty: Difficulty = Field(..., description="Task difficulty")
    title: str = Field(..., description="Task title")
    objective: str = Field(..., description="Task objective")
    description: str = Field(..., description="Scenario description")
    max_steps: int = Field(..., ge=1, description="Step budget")
    incident_id: str = Field(..., description="Stable incident identifier")
    incident_started_at: str = Field(..., description="Incident start timestamp")
    affected_users: int = Field(..., ge=0, description="Estimated affected users")
    services: list[ServiceStatus] = Field(
        default_factory=list,
        description="Mutable service state for the episode",
    )
    logs: list[LogEntry] = Field(
        default_factory=list,
        description="Complete log stream accumulated during the episode",
    )
    active_alerts: list[str] = Field(
        default_factory=list,
        description="Current alerts",
    )
    metrics: dict[str, float] = Field(
        default_factory=dict,
        description="Current incident metrics",
    )
    feature_flags: dict[str, bool] = Field(
        default_factory=dict,
        description="Feature flags toggled during the episode",
    )
    paged_teams: list[str] = Field(
        default_factory=list,
        description="Teams paged during the episode",
    )
    status_updates: list[str] = Field(
        default_factory=list,
        description="Customer-facing status updates",
    )
    query_history: list[str] = Field(
        default_factory=list,
        description="Free-text investigation queries issued so far",
    )
    investigation_finding_ids: list[str] = Field(
        default_factory=list,
        description="Internal identifiers for surfaced findings",
    )
    investigation_findings: list[str] = Field(
        default_factory=list,
        description="Human-readable surfaced findings",
    )
    resolution_markers: dict[str, bool] = Field(
        default_factory=dict,
        description="Internal milestone flags used to derive scenario state",
    )
    actions_taken: list[ActionTrace] = Field(
        default_factory=list,
        description="Complete action trace",
    )
    submitted_rca_text: str = Field(
        default="",
        description="Final RCA text submitted by the agent",
    )
    resolved: bool = Field(default=False, description="Operational resolution flag")
    invalid_actions: int = Field(default=0, ge=0, description="Invalid action count")
    repeated_actions: int = Field(default=0, ge=0, description="Repeated action count")
    destructive_actions: list[str] = Field(
        default_factory=list,
        description="Known-bad actions taken during the episode",
    )
    current_progress_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Current grader-aligned progress score",
    )
    total_reward: float = Field(
        default=0.0,
        description="Accumulated dense reward over the trajectory",
    )
    last_action_result: str = Field(
        default="Episode ready.",
        description="Human-readable result of the latest action",
    )
