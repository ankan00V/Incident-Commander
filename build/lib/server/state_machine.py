"""State transition helpers for incident progression and action effects."""

from __future__ import annotations

import copy
from typing import Any


def apply_time_pressure(
    services: dict[str, dict[str, Any]],
    scenario_id: str,
    elapsed_seconds: int,
) -> dict[str, dict[str, Any]]:
    """Degrade services over time to create urgency."""

    updated = copy.deepcopy(services)

    if scenario_id == "cpu_spike":
        if elapsed_seconds > 120 and updated["db-primary"]["status"] == "healthy":
            updated["db-primary"]["status"] = "degraded"
            updated["db-primary"]["p99_latency_ms"] = min(
                updated["db-primary"]["p99_latency_ms"] * 1.5,
                3000.0,
            )
            updated["db-primary"]["error_rate"] = min(
                updated["db-primary"]["error_rate"] + 0.05,
                0.30,
            )

    elif scenario_id == "db_cascade":
        if elapsed_seconds > 180 and updated["api-gateway"]["status"] == "degraded":
            updated["api-gateway"]["status"] = "down"
            updated["api-gateway"]["error_rate"] = 0.95
        if elapsed_seconds > 300 and updated["db-primary"]["status"] != "down":
            updated["db-primary"]["status"] = "down"
            updated["db-primary"]["error_rate"] = 0.99
            updated["auth-service"]["status"] = "down"
            updated["auth-service"]["error_rate"] = 0.99

    elif scenario_id == "ddos_payment":
        if elapsed_seconds > 420 and updated["cdn-edge"]["status"] == "degraded":
            updated["cdn-edge"]["status"] = "down"
            updated["cdn-edge"]["error_rate"] = 0.99
        if elapsed_seconds > 600 and updated["api-gateway"]["status"] == "degraded":
            updated["api-gateway"]["status"] = "down"
            updated["api-gateway"]["error_rate"] = 0.99

    return updated


def apply_action_effect(
    services: dict[str, dict[str, Any]],
    metrics: dict[str, Any],
    alerts: list[str],
    action_type: str,
    params: dict[str, Any],
    scenario: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any], list[str], float]:
    """Mutate world state based on the chosen action and return dense reward."""

    updated_services = copy.deepcopy(services)
    updated_metrics = copy.deepcopy(metrics)
    updated_alerts = list(alerts)
    reward = 0.0
    resolution = scenario.get("resolution", {})

    if action_type == "run_query":
        query = (params.get("query") or "").lower()
        keywords = {
            "cpu_spike": ["cpu", "deploy", "query", "n+1", "version"],
            "db_cascade": ["connection", "pool", "leak", "session", "memory"],
            "ddos_payment": ["traffic", "ddos", "stripe", "payment", "braintree", "waf"],
        }.get(scenario["id"], [])
        reward = round(0.05 * sum(1 for keyword in keywords if keyword in query), 3)

    elif action_type == "scale_service":
        service_name = params.get("service_name")
        replicas = params.get("replicas")
        if not service_name or service_name not in updated_services or replicas is None:
            reward = -0.2
        else:
            service = updated_services[service_name]
            current = service["replicas"]
            service["replicas"] = replicas
            if replicas > current and scenario["id"] == "cpu_spike" and service_name == "api-gateway":
                service["cpu_pct"] = max(service["cpu_pct"] - 12.0, 70.0)
                service["p99_latency_ms"] = max(service["p99_latency_ms"] * 0.8, 1400.0)
                service["error_rate"] = max(service["error_rate"] - 0.03, 0.06)
                reward = 0.15
            elif replicas > current and scenario["id"] == "ddos_payment" and service_name == "cdn-edge":
                service["cpu_pct"] = max(service["cpu_pct"] - 8.0, 60.0)
                service["error_rate"] = max(service["error_rate"] - 0.05, 0.12)
                reward = 0.1
            elif service["status"] == "healthy":
                reward = -0.05
            else:
                reward = 0.02

    elif action_type == "rollback":
        service_name = params.get("service_name")
        version = params.get("version") or ""
        target_service = resolution.get("target_service")
        version_prefix = resolution.get("target_version_prefix", "")
        if service_name == target_service and version.startswith(version_prefix):
            service = updated_services[service_name]
            service["status"] = "healthy"
            service["error_rate"] = 0.01
            service["p99_latency_ms"] = 95.0
            service["cpu_pct"] = 28.0
            updated_metrics["global_error_rate"] = 0.01
            updated_metrics["cpu_pct"] = 28.0
            updated_metrics["db_qps"] = 2200.0
            updated_alerts = []
            reward = 0.9
        elif service_name in updated_services:
            reward = -0.1
        else:
            reward = -0.2

    elif action_type == "restart_pod":
        service_name = params.get("service_name")
        penalized_actions = resolution.get("penalized_actions", [])
        if service_name and f"restart_pod:{service_name}" in penalized_actions:
            reward = -0.25
        elif service_name == resolution.get("target_restart") and service_name in updated_services:
            worker = updated_services[service_name]
            worker["status"] = "healthy"
            worker["error_rate"] = 0.01
            worker["p99_latency_ms"] = 120.0
            worker["mem_pct"] = 45.0
            updated_services["auth-service"]["status"] = "degraded"
            updated_services["auth-service"]["error_rate"] = 0.20
            updated_services["auth-service"]["p99_latency_ms"] = 1800.0
            updated_metrics["db_connections_used"] = max(
                0,
                int(updated_metrics.get("db_connections_used", 500)) - 350,
            )
            reward = 0.5
        elif service_name in updated_services:
            reward = -0.1 if updated_services[service_name]["status"] == "healthy" else 0.1
        else:
            reward = -0.2

    elif action_type == "toggle_feature":
        feature_flag = params.get("feature_flag")
        enabled = params.get("enabled")
        penalized_actions = resolution.get("penalized_actions", [])
        required_features = resolution.get("required_features", [])
        if feature_flag and f"toggle_feature:{feature_flag}" in penalized_actions:
            reward = -0.2
        elif feature_flag == resolution.get("target_feature_flag") and enabled == resolution.get("target_enabled"):
            db = updated_services["db-primary"]
            auth = updated_services["auth-service"]
            db["status"] = "healthy"
            db["error_rate"] = 0.03
            db["p99_latency_ms"] = 350.0
            db["cpu_pct"] = max(db["cpu_pct"] - 35.0, 22.0)
            auth["status"] = "healthy"
            auth["error_rate"] = 0.02
            auth["p99_latency_ms"] = 140.0
            updated_metrics["db_connections_used"] = max(
                0,
                int(updated_metrics.get("db_connections_used", 500)) - 150,
            )
            updated_metrics["auth_success_rate"] = 0.97
            reward = 0.4
        elif any(
            req["flag"] == feature_flag and req["enabled"] == enabled
            for req in required_features
        ):
            if feature_flag == "ddos_challenge_mode":
                cdn = updated_services["cdn-edge"]
                api_gateway = updated_services["api-gateway"]
                cdn["status"] = "healthy"
                cdn["error_rate"] = max(cdn["error_rate"] - 0.15, 0.04)
                cdn["p99_latency_ms"] = 650.0
                api_gateway["error_rate"] = max(api_gateway["error_rate"] - 0.06, 0.08)
                updated_metrics["ddos_traffic_pct"] = max(
                    0.0,
                    float(updated_metrics.get("ddos_traffic_pct", 0.0)) - 0.25,
                )
                updated_alerts = [alert for alert in updated_alerts if "challenge mode" not in alert.lower()]
            elif feature_flag == "payment_fallback_braintree":
                payment = updated_services["payment-service"]
                checkout = updated_services["checkout-ui"]
                payment["status"] = "healthy"
                payment["error_rate"] = 0.04
                payment["p99_latency_ms"] = 210.0
                checkout["error_rate"] = 0.08
                checkout["p99_latency_ms"] = 720.0
                updated_metrics["braintree_active"] = 1.0
                updated_metrics["global_error_rate"] = 0.12
                updated_alerts = [alert for alert in updated_alerts if "braintree" not in alert.lower()]
            reward = 0.4
        else:
            reward = 0.0

    elif action_type == "page_team":
        team = params.get("team")
        required_pages = resolution.get("required_pages", [])
        penalized_pages = [
            action.split(":", 1)[1]
            for action in resolution.get("penalized_actions", [])
            if action.startswith("page_team:")
        ]
        if team in penalized_pages:
            reward = -0.15
        elif team in required_pages:
            reward = 0.3
        elif team:
            reward = -0.05

    elif action_type == "post_status":
        message = params.get("message") or ""
        if len(message.strip()) >= 30:
            updated_alerts = [
                alert
                for alert in updated_alerts
                if "communication" not in alert.lower() and "status page" not in alert.lower()
            ]
            reward = 0.2

    return (
        updated_services,
        updated_metrics,
        updated_alerts,
        round(reward, 4),
    )
