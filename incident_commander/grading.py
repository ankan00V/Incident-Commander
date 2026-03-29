"""Deterministic graders and dense progress scoring for incident-response tasks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .models import IncidentState, ProgressMetric, ServiceStatus
from .task_bank import ActionRequirement, ScenarioDefinition, get_task


@dataclass(frozen=True)
class GraderResult:
    score: float
    breakdown: dict[str, float]
    metrics: list[ProgressMetric]


def _clamp(score: float) -> float:
    return max(0.0, min(score, 1.0))


def _service_map(state: IncidentState) -> dict[str, ServiceStatus]:
    return {service.name: service for service in state.services}


def _action_indices(
    state: IncidentState,
    predicate: Callable[[str, dict], bool],
) -> list[int]:
    return [
        index
        for index, trace in enumerate(state.actions_taken)
        if predicate(trace.action_type, trace.params)
    ]


def _first_action_index(
    state: IncidentState,
    predicate: Callable[[str, dict], bool],
) -> int | None:
    matches = _action_indices(state, predicate)
    return matches[0] if matches else None


def _text_contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(token in text for token in tokens)


def _requirement_match(requirement: ActionRequirement, trace_params: dict, action_type: str) -> bool:
    if action_type != requirement.action_type:
        return False
    if requirement.service_name is not None and trace_params.get("service_name") != requirement.service_name:
        return False
    if requirement.version is not None and trace_params.get("version") != requirement.version:
        return False
    if requirement.team is not None and trace_params.get("team") != requirement.team:
        return False
    if requirement.feature_flag is not None and trace_params.get("feature_flag") != requirement.feature_flag:
        return False
    if requirement.enabled is not None and trace_params.get("enabled") is not requirement.enabled:
        return False
    if requirement.min_replicas is not None:
        replicas = trace_params.get("replicas")
        if not isinstance(replicas, int) or replicas < requirement.min_replicas:
            return False
    if requirement.message_required:
        message = (trace_params.get("message") or "").strip()
        if len(message) < 20:
            return False
    return True


def _coverage_score(state: IncidentState, requirements: tuple[ActionRequirement, ...]) -> float:
    if not requirements:
        return 1.0
    hits = 0
    for requirement in requirements:
        if any(
            _requirement_match(requirement, trace.params, trace.action_type)
            for trace in state.actions_taken
        ):
            hits += 1
    return hits / len(requirements)


def _investigation_score(state: IncidentState, task: ScenarioDefinition) -> float:
    if not task.required_findings:
        return 1.0
    finding_ids = set(state.investigation_finding_ids)
    hits = sum(1 for finding_id in task.required_findings if finding_id in finding_ids)
    return hits / len(task.required_findings)


def _ddos_payment_mitigation_score(state: IncidentState) -> float:
    ddos_index = _first_action_index(
        state,
        lambda action_type, params: (
            action_type == "toggle_feature"
            and params.get("feature_flag") == "ddos_challenge_mode"
            and params.get("enabled") is True
        ),
    )
    payment_index = _first_action_index(
        state,
        lambda action_type, params: (
            action_type == "toggle_feature"
            and params.get("feature_flag") == "payment_fallback_braintree"
            and params.get("enabled") is True
        ),
    )
    destructive_touches = _action_indices(
        state,
        lambda action_type, params: (
            action_type in {"restart_pod", "rollback", "scale_service"}
            and params.get("service_name") in {"order-service", "db-primary", "user-service"}
        ),
    )

    score = 0.0
    if ddos_index is not None:
        score += 0.25
    if payment_index is not None:
        score += 0.25
    if ddos_index is not None and payment_index is not None and ddos_index < payment_index:
        score += 0.30
    if not destructive_touches:
        score += 0.20
    return round(score, 4)


def _ddos_payment_communication_score(state: IncidentState) -> float:
    paged_teams = set(state.paged_teams)
    status_messages = [message.strip() for message in state.status_updates if message.strip()]
    substantive_status = any(
        len(message) >= 40
        and _text_contains_any(
            message.lower(),
            ("checkout", "payment", "traffic", "attack", "mitigat", "provider", "status"),
        )
        for message in status_messages
    )

    score = 0.0
    if "security" in paged_teams:
        score += 0.3
    if "payments" in paged_teams:
        score += 0.3
    if substantive_status:
        score += 0.4
    return round(score, 4)


def _rca_score(state: IncidentState, task: ScenarioDefinition) -> float:
    text = state.submitted_rca_text.lower()
    if not text:
        return 0.0
    if task.task_id == "ddos_payment":
        ddos_identified = _text_contains_any(
            text,
            ("ddos", "attack", "volumetric", "challenge mode", "edge traffic"),
        )
        upstream_identified = _text_contains_any(
            text,
            ("stripe", "payment provider", "upstream", "503"),
        )
        fallback_identified = _text_contains_any(
            text,
            ("braintree", "fallback"),
        )
        score = 0.0
        if ddos_identified:
            score += 0.4
        if upstream_identified:
            score += 0.4
        if fallback_identified:
            score += 0.2
        return round(score, 4)
    hits = sum(1 for keyword in task.rca_keywords if keyword.lower() in text)
    return hits / max(len(task.rca_keywords), 1)


def _efficiency_score(state: IncidentState, task: ScenarioDefinition) -> float:
    steps = max(state.step_count, len(state.actions_taken))
    ideal_steps = {
        "cpu_spike": 3,
        "db_cascade": 5,
        "ddos_payment": 7,
    }.get(task.task_id, task.max_steps // 2)
    excess_steps = max(steps - ideal_steps, 0)
    remaining_budget = max(task.max_steps - ideal_steps, 1)
    return _clamp(1.0 - (excess_steps / remaining_budget))


def _resolution_score(state: IncidentState, task: ScenarioDefinition) -> float:
    services = _service_map(state)
    markers = state.resolution_markers

    if task.task_id == "cpu_spike":
        if markers.get("rollback_completed") or state.resolved:
            return 1.0
        gateway = services["api-gateway"]
        cpu_relief = _clamp((98.0 - state.metrics.get("cpu_pct", 98.0)) / 56.0)
        latency_relief = _clamp((4200.0 - gateway.p99_latency_ms) / 3600.0)
        error_relief = _clamp((0.12 - gateway.error_rate) / 0.10)
        return round(min((cpu_relief + latency_relief + error_relief) / 3.0, 0.65), 4)

    if task.task_id == "db_cascade":
        stage_scores = [
            0.34 if markers.get("session_worker_restarted") else 0.0,
            0.33 if state.feature_flags.get("read_replica_routing") else 0.0,
            0.33 if services["db-primary"].replicas >= 2 else 0.0,
        ]
        if state.resolved:
            return 1.0
        return round(min(sum(stage_scores), 0.9), 4)

    if task.task_id == "ddos_payment":
        ddos_enabled = state.feature_flags.get("ddos_challenge_mode")
        payment_enabled = state.feature_flags.get("payment_fallback_braintree")
        ddos_index = _first_action_index(
            state,
            lambda action_type, params: (
                action_type == "toggle_feature"
                and params.get("feature_flag") == "ddos_challenge_mode"
                and params.get("enabled") is True
            ),
        )
        payment_index = _first_action_index(
            state,
            lambda action_type, params: (
                action_type == "toggle_feature"
                and params.get("feature_flag") == "payment_fallback_braintree"
                and params.get("enabled") is True
            ),
        )
        stage_scores = [
            0.35 if ddos_enabled else 0.0,
            0.35 if payment_enabled else 0.0,
            0.20
            if ddos_index is not None and payment_index is not None and ddos_index < payment_index
            else 0.0,
            0.10 if _ddos_payment_communication_score(state) >= 0.6 else 0.0,
        ]
        return round(sum(stage_scores), 4)

    return 0.0


def _penalty_score(state: IncidentState) -> float:
    destructive_penalty = min(len(state.destructive_actions) * 0.12, 0.36)
    invalid_penalty = min(state.invalid_actions * 0.05, 0.15)
    repeated_penalty = min(state.repeated_actions * 0.03, 0.12)
    return destructive_penalty + invalid_penalty + repeated_penalty


def grade_state(state: IncidentState, task: ScenarioDefinition | str | None = None) -> GraderResult:
    """Grade a completed or in-progress episode on a deterministic 0.0-1.0 scale."""

    resolved_task = get_task(task or state.task_id)
    communication_requirements = tuple(
        requirement
        for requirement in resolved_task.required_actions
        if requirement.action_type in {"page_team", "post_status"}
    )
    mitigation_requirements = tuple(
        requirement
        for requirement in resolved_task.required_actions
        if requirement.action_type not in {"page_team", "post_status"}
    )

    investigation = round(_investigation_score(state, resolved_task), 4)
    resolution = round(_resolution_score(state, resolved_task), 4)
    if resolved_task.task_id == "ddos_payment":
        mitigation = _ddos_payment_mitigation_score(state)
        communication = _ddos_payment_communication_score(state)
    else:
        mitigation = round(_coverage_score(state, mitigation_requirements), 4)
        communication = round(_coverage_score(state, communication_requirements), 4)
    efficiency = round(_efficiency_score(state, resolved_task), 4)
    rca = round(_rca_score(state, resolved_task), 4)
    penalties = round(_penalty_score(state), 4)

    weights = {
        "easy": {
            "investigation": 0.20,
            "resolution": 0.45,
            "mitigation": 0.20,
            "communication": 0.00,
            "efficiency": 0.05,
            "rca": 0.10,
        },
        "medium": {
            "investigation": 0.15,
            "resolution": 0.35,
            "mitigation": 0.30,
            "communication": 0.00,
            "efficiency": 0.10,
            "rca": 0.10,
        },
        "hard": {
            "investigation": 0.15,
            "resolution": 0.30,
            "mitigation": 0.15,
            "communication": 0.20,
            "efficiency": 0.05,
            "rca": 0.15,
        },
    }[resolved_task.difficulty]

    raw_score = (
        (weights["investigation"] * investigation)
        + (weights["resolution"] * resolution)
        + (weights["mitigation"] * mitigation)
        + (weights["communication"] * communication)
        + (weights["efficiency"] * efficiency)
        + (weights["rca"] * rca)
        - penalties
    )
    if resolved_task.task_id == "ddos_payment" and not state.resolved:
        raw_score = min(raw_score, 0.65)
    overall = round(_clamp(raw_score), 4)

    breakdown = {
        "investigation": investigation,
        "resolution": resolution,
        "mitigation": mitigation,
        "communication": communication,
        "efficiency": efficiency,
        "rca": rca,
        "penalties": penalties,
    }
    metrics = [
        ProgressMetric(
            name="investigation",
            score=investigation,
            description="Surfaced the decisive technical evidence for the incident.",
        ),
        ProgressMetric(
            name="resolution",
            score=resolution,
            description="How close the live system is to being restored.",
        ),
        ProgressMetric(
            name="mitigation",
            score=mitigation,
            description="Coverage of the required operational mitigation actions.",
        ),
        ProgressMetric(
            name="communication",
            score=communication,
            description="Coverage of required team paging and status communication.",
        ),
        ProgressMetric(
            name="rca",
            score=rca,
            description="Quality of the submitted root-cause analysis.",
        ),
    ]
    return GraderResult(score=overall, breakdown=breakdown, metrics=metrics)


def grade_state_payload(state_payload: dict, task_id: str | None = None) -> GraderResult:
    """Grade a serialized IncidentState payload received over HTTP."""

    state = IncidentState.model_validate(state_payload)
    return grade_state(state, task_id or state.task_id)


def grader_result_to_dict(result: GraderResult) -> dict:
    """Serialize grader output for JSON APIs."""

    return {
        "score": result.score,
        "breakdown": result.breakdown,
        "metrics": [metric.model_dump() for metric in result.metrics],
    }


__all__ = [
    "GraderResult",
    "grade_state",
    "grade_state_payload",
    "grader_result_to_dict",
]
