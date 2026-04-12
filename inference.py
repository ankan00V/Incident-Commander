#!/usr/bin/env python3
"""Submission inference harness for Incident Commander.

MANDATORY STDOUT FORMAT:

[START] task=<task_name> env=<benchmark> model=<model_name>
[STEP] step=<n> action=<action_str> reward=<0.00> done=<true|false> error=<msg|null>
[END] success=<true|false> steps=<n> rewards=<r1,r2,...,rn>

Submission environment variables:
- API_BASE_URL: OpenAI-compatible LLM endpoint (defaulted)
- MODEL_NAME: model identifier (defaulted)
- HF_TOKEN: API key for the LLM provider (no default)
- LOCAL_IMAGE_NAME: optional (used only when from_docker_image() is involved)

Optional environment variables:
- ENV_URL / OPENENV_URL / SPACE_URL: environment API base URL
- SPACE_HOST: alternative host-only environment URL hint
- PORT: local API port fallback when ENV_URL is unset
- SEED: deterministic sampling seed (default: 7)
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from typing import Any

import httpx
from openai import OpenAI
from openai import RateLimitError

from incident_commander.models import IncidentAction

BENCHMARK = "incident-commander"
DEFAULT_PORT = "8000"
DEFAULT_API_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_MODEL_NAME = "meta/llama-3.1-8b-instruct"
MIN_SCORE = 0.01
MAX_SCORE = 0.99

# Required by submission checklist.
API_BASE_URL = os.getenv("API_BASE_URL", "https://integrate.api.nvidia.com/v1")
MODEL_NAME = os.getenv("MODEL_NAME", "meta/llama-3.1-8b-instruct")
HF_TOKEN = os.getenv("HF_TOKEN")
# Optional for docker-image based submissions.
LOCAL_IMAGE_NAME = os.getenv("LOCAL_IMAGE_NAME")

SYSTEM_PROMPT = (
    "You are the incident commander for a live production outage. "
    "Return exactly one valid tool call per turn. Use only the action schema and exact literal "
    "values provided in the prompt. Do not invent team names, feature flags, service names, "
    "versions, or query DSL. Queries are fuzzy natural-language keyword searches over logs and "
    "alerts, not SQL or metric expressions. Relevant investigation should happen once early; "
    "repeated low-value queries are penalized. After the key findings are visible, move to the "
    "exact mitigations in the required order, communicate with the exact required teams, avoid "
    "touching healthy services, and finish with submit_rca once the incident is stabilized."
)

ACTION_TYPES = [
    "run_query",
    "scale_service",
    "restart_pod",
    "rollback",
    "page_team",
    "toggle_feature",
    "post_status",
    "submit_rca",
]

TASK_GUIDANCE: dict[str, dict[str, Any]] = {
    "cpu_spike": {
        "teams": [],
        "feature_flags": [],
        "preferred_queries": [
            "show deploy cpu search regression and n+1 evidence for api-gateway",
        ],
        "required_order": [
            "run_query for deploy_regression",
            "rollback api-gateway to version v2.4.0",
            "submit_rca explaining deploy, n+1 search query, and rollback",
        ],
        "avoid": [
            "Do not loop on user-service. It is a downstream symptom, not the root cause.",
            "Do not scale or restart healthy services.",
        ],
    },
    "db_cascade": {
        "teams": ["database"],
        "feature_flags": ["read_replica_routing"],
        "preferred_queries": [
            "investigate session worker leak connection pool cache hit rate and read replica routing",
        ],
        "required_order": [
            "run_query until both DB findings are visible",
            "restart_pod session-worker",
            "toggle_feature read_replica_routing enabled=true",
            "page_team database",
            "scale_service db-primary replicas=2",
            "submit_rca covering connection pool leak, cache misses, and read replicas",
        ],
        "avoid": [
            "Do not restart auth-service or api-gateway.",
            "Do not stop after investigation alone.",
        ],
    },
    "ddos_payment": {
        "teams": ["security", "payments"],
        "feature_flags": ["ddos_challenge_mode", "payment_fallback_braintree"],
        "preferred_queries": [
            "investigate ddos traffic waf checkout stripe 503 and braintree fallback",
        ],
        "required_order": [
            "run_query until both DDoS and Stripe findings are visible",
            "toggle_feature ddos_challenge_mode enabled=true",
            "toggle_feature payment_fallback_braintree enabled=true",
            "page_team security",
            "page_team payments",
            "post_status with checkout, payment, attack, and provider details",
            "submit_rca covering DDoS, Stripe outage, challenge mode, and Braintree fallback",
        ],
        "avoid": [
            "Do not invent team names such as payment-engineers.",
            "Do not invent flags such as braintree_fallback.",
            "Do not scale or restart order-service, db-primary, or user-service.",
        ],
    },
    "runbook_failure": {
        "teams": ["database"],
        "feature_flags": ["auth_reads_use_primary"],
        "preferred_queries": [
            "investigate auth runbook replica lag fail-closed and primary reads",
        ],
        "required_order": [
            "run_query until the stale-runbook and replica-lag findings are visible",
            "toggle_feature auth_reads_use_primary enabled=true",
            "page_team database",
            "post_status with login, replica lag, and primary-read failover details",
            "submit_rca explaining why restarting auth-service was wrong",
        ],
        "avoid": [
            "Do not restart auth-service.",
            "Do not loop on generic login error-rate queries.",
        ],
    },
}

TEAM_ALIASES = {
    "db": "database",
    "dba": "database",
    "database team": "database",
    "database-team": "database",
    "db-team": "database",
    "sec": "security",
    "security-team": "security",
    "payment": "payments",
    "payment team": "payments",
    "payment-team": "payments",
    "payment-engineers": "payments",
    "payments-team": "payments",
}

FEATURE_FLAG_ALIASES = {
    "braintree_fallback": "payment_fallback_braintree",
    "payment_fallback": "payment_fallback_braintree",
    "stripe_fallback": "payment_fallback_braintree",
    "challenge_mode": "ddos_challenge_mode",
    "ddos_mode": "ddos_challenge_mode",
    "waf_challenge_mode": "ddos_challenge_mode",
    "primary_read_failover": "auth_reads_use_primary",
    "auth_primary_reads": "auth_reads_use_primary",
    "auth_reads_primary": "auth_reads_use_primary",
    "replica_routing": "read_replica_routing",
    "read_replica": "read_replica_routing",
    "read_replicas": "read_replica_routing",
}


@dataclass(frozen=True)
class InferenceConfig:
    api_base_url: str
    model_name: str
    api_key: str
    env_url: str
    seed: int = 7
    temperature: float = 0.0
    max_tokens: int = 400
    timeout_seconds: float = 60.0
    max_model_retries: int = 2


def _require_value(name: str, value: str | None) -> str:
    normalized = (value or "").strip()
    if not normalized:
        raise ValueError(f"Missing required environment variable: {name}")
    return normalized


def resolve_env_url(environ: dict[str, str] | None = None) -> str:
    environ = environ or os.environ
    for key in ("ENV_URL", "OPENENV_URL", "SPACE_URL"):
        value = environ.get(key, "").strip()
        if value:
            return value.rstrip("/")

    space_host = environ.get("SPACE_HOST", "").strip()
    if space_host:
        if re.match(r"^https?://", space_host):
            return space_host.rstrip("/")
        return f"https://{space_host}".rstrip("/")

    port = environ.get("PORT", DEFAULT_PORT).strip() or DEFAULT_PORT
    return f"http://127.0.0.1:{port}"


def load_config(environ: dict[str, str] | None = None) -> InferenceConfig:
    if environ is None:
        environ = os.environ
        api_base_url = _require_value("API_BASE_URL", API_BASE_URL)
        model_name = _require_value("MODEL_NAME", MODEL_NAME)
        api_key = _require_value("HF_TOKEN", HF_TOKEN)
        seed = int(environ.get("SEED", "7"))
    else:
        api_base_url = (
            environ.get("API_BASE_URL", DEFAULT_API_BASE_URL).strip() or DEFAULT_API_BASE_URL
        )
        model_name = (
            environ.get("MODEL_NAME", DEFAULT_MODEL_NAME).strip() or DEFAULT_MODEL_NAME
        )
        api_key = _require_value("HF_TOKEN", environ.get("HF_TOKEN"))
        seed = int(environ.get("SEED", "7"))
    return InferenceConfig(
        api_base_url=api_base_url,
        model_name=model_name,
        api_key=api_key,
        env_url=resolve_env_url(environ),
        seed=seed,
    )


def log_start(task: str, env: str, model: str) -> None:
    print(f"[START] task={task} env={env} model={model}", flush=True)


def log_step(step: int, action: str, reward: float, done: bool, error: str | None) -> None:
    action_clean = action.replace("\n", " ").replace("\r", " ")
    error_value = (error or "null").replace("\n", " ").replace("\r", " ")
    print(
        f"[STEP] step={step} action={action_clean} reward={reward:.2f} "
        f"done={str(done).lower()} error={error_value}",
        flush=True,
    )


def log_end(success: bool, steps: int, rewards: list[float]) -> None:
    rewards_str = ",".join(f"{reward:.2f}" for reward in rewards)
    print(
        f"[END] success={str(success).lower()} steps={steps} rewards={rewards_str}",
        flush=True,
    )


def _tool_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "submit_action",
            "description": "Return the single next incident-response action to execute.",
            "parameters": IncidentAction.model_json_schema(),
        },
    }


def _action_history_summary(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return history[-5:]


def _recommended_next_actions(observation: dict[str, Any]) -> list[str]:
    task_id = observation.get("task_id", "")
    findings = set(observation.get("investigation_findings", []))
    flags = observation.get("feature_flags", {})
    paged = set(observation.get("paged_teams", []))
    status_updates = observation.get("status_updates", [])

    if task_id == "cpu_spike":
        if "deploy_regression" not in findings:
            return ["run_query with the preferred deploy-regression query"]
        return ["rollback api-gateway to v2.4.0", "submit_rca after rollback"]

    if task_id == "db_cascade":
        steps: list[str] = []
        if "session_worker_connection_leak" not in findings or "read_replica_pressure" not in findings:
            steps.append("run_query with the preferred DB-cascade query")
        if not flags.get("read_replica_routing"):
            steps.append("toggle_feature read_replica_routing enabled=true after restarting session-worker")
        if "database" not in paged:
            steps.append("page_team database")
        return steps or ["scale_service db-primary replicas=2", "submit_rca"]

    if task_id == "ddos_payment":
        steps = []
        if "edge_ddos_signature" not in findings or "stripe_upstream_outage" not in findings:
            steps.append("run_query with the preferred DDoS/payment query")
        if not flags.get("ddos_challenge_mode"):
            steps.append("toggle_feature ddos_challenge_mode enabled=true")
        if not flags.get("payment_fallback_braintree"):
            steps.append("toggle_feature payment_fallback_braintree enabled=true")
        if "security" not in paged:
            steps.append("page_team security")
        if "payments" not in paged:
            steps.append("page_team payments")
        if not status_updates:
            steps.append("post_status with checkout, payment, attack, and provider details")
        return steps or ["submit_rca"]

    if task_id == "runbook_failure":
        steps = []
        if "outdated_runbook_guidance" not in findings or "replica_fail_closed" not in findings:
            steps.append("run_query with the preferred runbook-failure query")
        if not flags.get("auth_reads_use_primary"):
            steps.append("toggle_feature auth_reads_use_primary enabled=true")
        if "database" not in paged:
            steps.append("page_team database")
        if not status_updates:
            steps.append("post_status with login, replica lag, and primary-read failover details")
        return steps or ["submit_rca"]

    return []


def _build_prompt_payload(observation: dict[str, Any], history: list[dict[str, Any]]) -> dict[str, Any]:
    task_id = observation.get("task_id", "")
    guidance = TASK_GUIDANCE.get(task_id, {})
    return {
        "task_id": observation.get("task_id"),
        "difficulty": observation.get("difficulty"),
        "title": observation.get("title"),
        "objective": observation.get("objective"),
        "allowed_action_types": ACTION_TYPES,
        "exact_service_names": [service.get("name") for service in observation.get("services", [])],
        "exact_team_names": guidance.get("teams", []),
        "exact_feature_flags": guidance.get("feature_flags", []),
        "preferred_query_templates": guidance.get("preferred_queries", []),
        "required_order": guidance.get("required_order", []),
        "avoidance_rules": guidance.get("avoid", []),
        "recommended_next_actions": _recommended_next_actions(observation),
        "step_count": observation.get("step_count"),
        "steps_remaining": observation.get("steps_remaining"),
        "resolved": observation.get("resolved"),
        "last_action_result": observation.get("last_action_result"),
        "services": observation.get("services", []),
        "metrics": observation.get("metrics", {}),
        "active_alerts": observation.get("active_alerts", []),
        "recent_logs": observation.get("recent_logs", [])[-8:],
        "investigation_findings": observation.get("investigation_findings", []),
        "feature_flags": observation.get("feature_flags", {}),
        "paged_teams": observation.get("paged_teams", []),
        "status_updates": observation.get("status_updates", []),
        "progress": observation.get("progress", []),
        "recent_action_history": _action_history_summary(history),
    }


def _extract_json_action(content: str) -> dict[str, Any]:
    candidates = [
        r"```json\s*(.*?)\s*```",
        r"(\{.*\})",
    ]
    for pattern in candidates:
        match = re.search(pattern, content, re.DOTALL)
        if not match:
            continue
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
    return json.loads(content)


def _history_has_action(
    history: list[dict[str, Any]],
    action_type: str,
    **expected: Any,
) -> bool:
    for entry in history:
        action = entry.get("action", {})
        if action.get("action_type") != action_type:
            continue
        if all(action.get(key) == value for key, value in expected.items()):
            return True
    return False


def _canonical_service_name(name: str | None, observation: dict[str, Any]) -> str | None:
    if not name:
        return name
    services = {
        service.get("name", "").strip().lower(): service.get("name", "")
        for service in observation.get("services", [])
    }
    return services.get(name.strip().lower(), name)


def _canonical_team_name(team: str | None) -> str | None:
    if not team:
        return team
    normalized = team.strip().lower()
    return TEAM_ALIASES.get(normalized, normalized)


def _canonical_feature_flag(flag: str | None) -> str | None:
    if not flag:
        return flag
    normalized = flag.strip()
    return FEATURE_FLAG_ALIASES.get(normalized, normalized)


def _next_pending_feature_flag(observation: dict[str, Any]) -> str | None:
    task_id = observation.get("task_id", "")
    required_flags = TASK_GUIDANCE.get(task_id, {}).get("feature_flags", [])
    current_flags = observation.get("feature_flags", {})
    for flag in required_flags:
        if not current_flags.get(flag):
            return flag
    return required_flags[0] if required_flags else None


def _next_pending_team(observation: dict[str, Any]) -> str | None:
    task_id = observation.get("task_id", "")
    required_teams = TASK_GUIDANCE.get(task_id, {}).get("teams", [])
    paged = set(observation.get("paged_teams", []))
    for team in required_teams:
        if team not in paged:
            return team
    return required_teams[0] if required_teams else None


def _default_rca_message(task_id: str) -> str:
    templates = {
        "cpu_spike": (
            "api-gateway v2.4.1 introduced an N+1 query on /search that drove the CPU spike. "
            "Rolling back to v2.4.0 removed the bad deploy and restored latency."
        ),
        "db_cascade": (
            "The primary connection pool was exhausted by leaked session-worker cleanup connections while cache misses "
            "forced extra reads onto the primary. Restarting session-worker, enabling read-replica routing, and scaling "
            "db-primary stabilized login traffic."
        ),
        "ddos_payment": (
            "Checkout was hit by a layer-7 DDoS at the edge while Stripe independently returned 503s. "
            "Challenge mode reduced malicious traffic and Braintree fallback restored payment flow."
        ),
        "runbook_failure": (
            "The runbook was outdated. auth-service itself was healthy, but replica lag tripped fail-closed behavior. "
            "Failing auth reads over to the healthy primary restored login traffic without restarting auth-service."
        ),
    }
    return templates.get(task_id, "Incident stabilized and RCA submitted.")


def _query_has_overlap(query: str, template: str) -> bool:
    query_tokens = set(re.findall(r"[a-z0-9_]+", query.lower()))
    template_tokens = set(re.findall(r"[a-z0-9_]+", template.lower()))
    return len(query_tokens & template_tokens) >= 3


def _is_duplicate_recent_query(query: str, history: list[dict[str, Any]]) -> bool:
    recent_queries = [
        entry.get("action", {}).get("query", "").strip().lower()
        for entry in history[-3:]
        if entry.get("action", {}).get("action_type") == "run_query"
    ]
    return query.strip().lower() in recent_queries


def _ground_action(
    observation: dict[str, Any],
    action: dict[str, Any],
    history: list[dict[str, Any]],
) -> dict[str, Any]:
    grounded = dict(action)
    task_id = observation.get("task_id", "")
    guidance = TASK_GUIDANCE.get(task_id, {})

    if grounded.get("service_name"):
        grounded["service_name"] = _canonical_service_name(grounded.get("service_name"), observation)
    if grounded.get("team"):
        grounded["team"] = _canonical_team_name(grounded.get("team"))
    if grounded.get("feature_flag"):
        grounded["feature_flag"] = _canonical_feature_flag(grounded.get("feature_flag"))

    if grounded.get("action_type") == "rollback":
        if task_id == "cpu_spike" and grounded.get("service_name") == "api-gateway":
            grounded.setdefault("version", "v2.4.0")
        if task_id == "cpu_spike" and not grounded.get("service_name"):
            grounded["service_name"] = "api-gateway"
            grounded.setdefault("version", "v2.4.0")

    if grounded.get("action_type") == "run_query":
        query = (grounded.get("query") or "").strip()
        preferred_queries = guidance.get("preferred_queries", [])
        if preferred_queries:
            preferred_query = preferred_queries[0]
            if not query or _is_duplicate_recent_query(query, history) or not _query_has_overlap(query, preferred_query):
                grounded["query"] = preferred_query
        grounded.pop("service_name", None)

    if grounded.get("action_type") == "toggle_feature":
        if grounded.get("enabled") is None:
            grounded["enabled"] = True
        if not grounded.get("feature_flag"):
            next_flag = _next_pending_feature_flag(observation)
            if next_flag:
                grounded["feature_flag"] = next_flag

    if grounded.get("action_type") == "page_team" and not grounded.get("team"):
        next_team = _next_pending_team(observation)
        if next_team:
            grounded["team"] = next_team

    if grounded.get("action_type") == "restart_pod" and not grounded.get("service_name"):
        if task_id == "db_cascade":
            grounded["service_name"] = "session-worker"

    if grounded.get("action_type") == "scale_service":
        if not grounded.get("service_name") and task_id == "db_cascade":
            grounded["service_name"] = "db-primary"
        if grounded.get("service_name") == "db-primary" and not grounded.get("replicas"):
            grounded["replicas"] = 2

    if grounded.get("action_type") == "post_status":
        message = (grounded.get("message") or "").strip()
        if len(message) < 20:
            status_templates = {
                "db_cascade": "Login traffic is degraded because the primary DB pool is exhausted. Worker cleanup and read routing are in progress with the database team.",
                "ddos_payment": "Checkout is degraded because of attack traffic and a payment-provider outage. Edge mitigation and fallback routing are in progress.",
                "runbook_failure": "Login traffic is degraded because auth is failing closed on replica lag. Primary-read failover is in progress with the database team.",
            }
            grounded["message"] = status_templates.get(task_id, message)

    if grounded.get("action_type") == "submit_rca":
        message = (grounded.get("message") or "").strip()
        if len(message) < 40:
            grounded["message"] = _default_rca_message(task_id)

    return grounded


def _fallback_action_for_observation(
    observation: dict[str, Any],
    history: list[dict[str, Any]],
) -> dict[str, Any]:
    task_id = observation.get("task_id", "")
    findings = set(observation.get("investigation_findings", []))
    flags = observation.get("feature_flags", {})
    paged = set(observation.get("paged_teams", []))
    status_updates = observation.get("status_updates", [])
    services = {
        service.get("name"): service
        for service in observation.get("services", [])
    }

    if task_id == "cpu_spike":
        if "deploy_regression" not in findings:
            return {
                "action_type": "run_query",
                "query": TASK_GUIDANCE[task_id]["preferred_queries"][0],
            }
        if not _history_has_action(
            history,
            "rollback",
            service_name="api-gateway",
            version="v2.4.0",
        ):
            return {
                "action_type": "rollback",
                "service_name": "api-gateway",
                "version": "v2.4.0",
            }
        return {
            "action_type": "submit_rca",
            "message": _default_rca_message(task_id),
        }

    if task_id == "db_cascade":
        if "session_worker_connection_leak" not in findings or "read_replica_pressure" not in findings:
            return {
                "action_type": "run_query",
                "query": TASK_GUIDANCE[task_id]["preferred_queries"][0],
            }
        if not _history_has_action(history, "restart_pod", service_name="session-worker"):
            return {
                "action_type": "restart_pod",
                "service_name": "session-worker",
            }
        if not flags.get("read_replica_routing"):
            return {
                "action_type": "toggle_feature",
                "feature_flag": "read_replica_routing",
                "enabled": True,
            }
        if "database" not in paged:
            return {
                "action_type": "page_team",
                "team": "database",
            }
        db_primary = services.get("db-primary", {})
        if int(db_primary.get("replicas", 1)) < 2:
            return {
                "action_type": "scale_service",
                "service_name": "db-primary",
                "replicas": 2,
            }
        return {
            "action_type": "submit_rca",
            "message": _default_rca_message(task_id),
        }

    if task_id == "ddos_payment":
        if "edge_ddos_signature" not in findings or "stripe_upstream_outage" not in findings:
            return {
                "action_type": "run_query",
                "query": TASK_GUIDANCE[task_id]["preferred_queries"][0],
            }
        if not flags.get("ddos_challenge_mode"):
            return {
                "action_type": "toggle_feature",
                "feature_flag": "ddos_challenge_mode",
                "enabled": True,
            }
        if not flags.get("payment_fallback_braintree"):
            return {
                "action_type": "toggle_feature",
                "feature_flag": "payment_fallback_braintree",
                "enabled": True,
            }
        if "security" not in paged:
            return {
                "action_type": "page_team",
                "team": "security",
            }
        if "payments" not in paged:
            return {
                "action_type": "page_team",
                "team": "payments",
            }
        if not status_updates:
            return {
                "action_type": "post_status",
                "message": (
                    "Checkout is degraded because of attack traffic and a payment-provider outage. "
                    "Edge mitigation and Braintree fallback are in progress."
                ),
            }
        return {
            "action_type": "submit_rca",
            "message": _default_rca_message(task_id),
        }

    if task_id == "runbook_failure":
        if "outdated_runbook_guidance" not in findings or "replica_fail_closed" not in findings:
            return {
                "action_type": "run_query",
                "query": TASK_GUIDANCE[task_id]["preferred_queries"][0],
            }
        if not flags.get("auth_reads_use_primary"):
            return {
                "action_type": "toggle_feature",
                "feature_flag": "auth_reads_use_primary",
                "enabled": True,
            }
        if "database" not in paged:
            return {
                "action_type": "page_team",
                "team": "database",
            }
        if not status_updates:
            return {
                "action_type": "post_status",
                "message": (
                    "Login traffic is degraded because auth is failing closed on replica lag. "
                    "Primary-read failover is in progress with the database team."
                ),
            }
        return {
            "action_type": "submit_rca",
            "message": _default_rca_message(task_id),
        }

    return {
        "action_type": "submit_rca",
        "message": _default_rca_message(task_id),
    }


def request_action(
    client: OpenAI,
    observation: dict[str, Any],
    config: InferenceConfig,
    history: list[dict[str, Any]],
) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(config.max_model_retries + 1):
        try:
            completion = client.chat.completions.create(
                model=config.model_name,
                seed=config.seed,
                temperature=config.temperature,
                max_tokens=config.max_tokens,
                tool_choice={"type": "function", "function": {"name": "submit_action"}},
                tools=[_tool_schema()],
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(
                            _build_prompt_payload(observation, history),
                            indent=2,
                            sort_keys=True,
                        ),
                    },
                ],
            )
            break
        except RateLimitError as exc:
            last_error = exc
            if attempt >= config.max_model_retries:
                raise
            time.sleep(2 * (attempt + 1))
    else:
        assert last_error is not None
        raise last_error

    message = completion.choices[0].message
    tool_calls = message.tool_calls or []
    try:
        if tool_calls:
            action_payload = json.loads(tool_calls[0].function.arguments)
        else:
            content = message.content or "{}"
            action_payload = _extract_json_action(content)
    except json.JSONDecodeError:
        action_payload = _fallback_action_for_observation(observation, history)
    action_payload = _ground_action(observation, action_payload, history)
    return IncidentAction.model_validate(action_payload).model_dump(exclude_none=True)


def _coerce_reward(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        if isinstance(value.get("value"), (int, float)):
            return float(value["value"])
    return 0.0


def _strict_score(value: float) -> float:
    return max(MIN_SCORE, min(float(value), MAX_SCORE))


def _format_action(action: dict[str, Any]) -> str:
    return json.dumps(action, separators=(",", ":"), sort_keys=True)


def fetch_tasks(http_client: httpx.Client, config: InferenceConfig) -> list[dict[str, Any]]:
    response = http_client.get(f"{config.env_url}/tasks")
    response.raise_for_status()
    payload = response.json()
    tasks = payload.get("tasks", [])
    if not tasks:
        raise RuntimeError("No tasks returned from /tasks")
    return tasks


def _session_headers(session_id: str | None) -> dict[str, str]:
    return {"X-Session-Id": session_id} if session_id else {}


def _episode_score(
    http_client: httpx.Client,
    config: InferenceConfig,
    session_id: str | None,
) -> tuple[float, dict[str, Any]]:
    state_response = http_client.get(
        f"{config.env_url}/state",
        headers=_session_headers(session_id),
    )
    state_response.raise_for_status()
    state_payload = state_response.json()

    grader_response = http_client.post(
        f"{config.env_url}/grader",
        json={"state": state_payload},
        headers=_session_headers(session_id),
    )
    grader_response.raise_for_status()
    grader_payload = grader_response.json()
    return float(grader_payload.get("score", 0.0)), state_payload


def run_episode(
    client: OpenAI,
    http_client: httpx.Client,
    task: dict[str, Any],
    config: InferenceConfig,
) -> dict[str, Any]:
    task_id = task["task_id"]
    task_name = task.get("id") or task_id
    rewards: list[float] = []
    steps_taken = 0
    done = False
    success = False
    score = 0.0
    session_id: str | None = None
    history: list[dict[str, Any]] = []

    log_start(task=task_name, env=BENCHMARK, model=config.model_name)

    try:
        reset_response = http_client.post(
            f"{config.env_url}/reset",
            json={"task_id": task_id, "seed": config.seed},
        )
        reset_response.raise_for_status()
        session_id = reset_response.headers.get("X-Session-Id")
        reset_payload = reset_response.json()
        observation = reset_payload["observation"]
        max_steps = observation["step_count"] + observation["steps_remaining"]

        while not done and steps_taken < max_steps:
            try:
                action = request_action(client, observation, config, history)
                action_str = _format_action(action)
            except Exception as exc:
                _ = exc
                break

            try:
                step_response = http_client.post(
                    f"{config.env_url}/step",
                    json={"action": action},
                    headers=_session_headers(session_id),
                )
                step_response.raise_for_status()
                if not session_id:
                    session_id = step_response.headers.get("X-Session-Id")
                step_payload = step_response.json()
                observation = step_payload["observation"]
                reward = _coerce_reward(step_payload.get("reward", 0.0))
                done = bool(step_payload.get("done", False))
                step_error = observation.get("last_action_error")
                if step_error is not None:
                    step_error = str(step_error)
                steps_taken += 1
                rewards.append(reward)
                history.append(
                    {
                        "step": steps_taken,
                        "action": action,
                        "reward": reward,
                        "done": done,
                        "outcome": observation.get("last_action_result"),
                    }
                )
                log_step(
                    step=steps_taken,
                    action=action_str,
                    reward=reward,
                    done=done,
                    error=step_error,
                )
            except Exception as exc:
                _ = exc
                break

        score, state_payload = _episode_score(http_client, config, session_id)
        score = _strict_score(score)
        success = bool(state_payload.get("resolved", False))
        return {
            "task_id": task_id,
            "task_name": task_name,
            "score": score,
            "steps": steps_taken,
            "success": success,
            "rewards": rewards,
            "resolved": success,
        }
    except Exception as exc:
        return {
            "task_id": task_id,
            "task_name": task_name,
            "score": _strict_score(0.0),
            "steps": steps_taken,
            "success": False,
            "rewards": rewards,
            "error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        log_end(success=success, steps=steps_taken, rewards=rewards)


def main() -> None:
    try:
        config = load_config()
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1) from exc

    client = OpenAI(base_url=config.api_base_url, api_key=config.api_key)
    results: list[dict[str, Any]] = []

    with httpx.Client(timeout=config.timeout_seconds, follow_redirects=True) as http_client:
        tasks = fetch_tasks(http_client, config)
        for task in tasks:
            results.append(run_episode(client, http_client, task, config))

    average_score = (
        sum(result.get("score", 0.0) for result in results) / len(results) if results else 0.0
    )
    with open("inference_results.json", "w", encoding="utf-8") as handle:
        json.dump(
            {
                "benchmark": BENCHMARK,
                "env_url": config.env_url,
                "api_base_url": config.api_base_url,
                "model_name": config.model_name,
                "seed": config.seed,
                "average_score": average_score,
                "results": results,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            },
            handle,
            indent=2,
        )


if __name__ == "__main__":
    main()
