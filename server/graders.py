"""Deterministic graders for Incident Commander."""

from __future__ import annotations

from server.scenarios import SCENARIOS

_EPSILON = 0.0001


def _clamp_open_interval(score: float) -> float:
    return max(_EPSILON, min(score, 1.0 - _EPSILON))


def _action_signature(action: dict) -> str:
    action_type = action.get("action_type", "")
    params = action.get("params", {})

    if action_type in {"restart_pod", "rollback", "scale_service"}:
        return f"{action_type}:{params.get('service_name', '')}"
    if action_type == "page_team":
        return f"{action_type}:{params.get('team', '')}"
    if action_type == "toggle_feature":
        return f"{action_type}:{params.get('feature_flag', '')}"
    return action_type


def grade(
    task_id: str,
    actions_taken: list[dict] | None = None,
    *,
    actions: list[dict] | None = None,
    resolved: bool,
    elapsed_seconds: int,
    rca_text: str = "",
) -> float:
    """Return a deterministic score in the strict open interval (0, 1)."""

    scenario = SCENARIOS.get(task_id)
    if scenario is None:
        return _EPSILON
    if actions_taken is None:
        actions_taken = actions or []
    if not actions_taken and not resolved:
        return _EPSILON

    difficulty = scenario["difficulty"]
    resolution = scenario["resolution"]

    resolution_score = 1.0 if resolved else 0.0

    required_action_types = set(resolution.get("required_actions", []))
    taken_action_types = {action["action_type"] for action in actions_taken}
    coverage = len(required_action_types & taken_action_types) / max(
        len(required_action_types), 1
    )

    required_features = resolution.get("required_features", [])
    if required_features:
        toggles = [
            action for action in actions_taken if action.get("action_type") == "toggle_feature"
        ]
        feature_hits = 0
        for required in required_features:
            for toggle in toggles:
                params = toggle.get("params", {})
                if (
                    params.get("feature_flag") == required["flag"]
                    and params.get("enabled") == required["enabled"]
                ):
                    feature_hits += 1
                    break
        feature_score = feature_hits / max(len(required_features), 1)
    else:
        feature_score = 1.0

    penalized_actions = set(resolution.get("penalized_actions", []))
    destructive_count = sum(
        1 for action in actions_taken if _action_signature(action) in penalized_actions
    )
    destructive_penalty = min(destructive_count * 0.15, 0.45)

    step_count = len(actions_taken)
    efficiency = max(0.0, 1.0 - (step_count / 15.0))

    rca_keywords = resolution.get("rca_keywords", [])
    rca_hits = sum(1 for keyword in rca_keywords if keyword in (rca_text or "").lower())
    rca_score = rca_hits / max(len(rca_keywords), 1) if rca_keywords else 0.5

    max_time = {"easy": 120, "medium": 300, "hard": 600}[difficulty]
    time_score = max(0.0, 1.0 - (elapsed_seconds / max_time))

    weights = {
        "easy": {
            "resolution": 0.50,
            "coverage": 0.20,
            "feature": 0.00,
            "efficiency": 0.10,
            "rca": 0.10,
            "time": 0.10,
        },
        "medium": {
            "resolution": 0.40,
            "coverage": 0.20,
            "feature": 0.15,
            "efficiency": 0.10,
            "rca": 0.10,
            "time": 0.05,
        },
        "hard": {
            "resolution": 0.30,
            "coverage": 0.15,
            "feature": 0.20,
            "efficiency": 0.10,
            "rca": 0.15,
            "time": 0.10,
        },
    }[difficulty]

    raw = (
        weights["resolution"] * resolution_score
        + weights["coverage"] * coverage
        + weights["feature"] * feature_score
        + weights["efficiency"] * efficiency
        + weights["rca"] * rca_score
        + weights["time"] * time_score
        - destructive_penalty
    )
    return round(_clamp_open_interval(raw), 4)
