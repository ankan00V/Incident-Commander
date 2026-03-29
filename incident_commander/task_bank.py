"""Deterministic task bank for the Incident Commander environment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


Difficulty = Literal["easy", "medium", "hard"]


@dataclass(frozen=True)
class ServiceSeed:
    name: str
    status: Literal["healthy", "degraded", "down"]
    error_rate: float
    p99_latency_ms: float
    replicas: int


@dataclass(frozen=True)
class LogSeed:
    timestamp: str
    level: Literal["INFO", "WARN", "ERROR", "FATAL"]
    service: str
    message: str


@dataclass(frozen=True)
class QueryFinding:
    finding_id: str
    keywords: tuple[str, ...]
    note: str


@dataclass(frozen=True)
class ActionRequirement:
    requirement_id: str
    action_type: str
    service_name: str | None = None
    version: str | None = None
    team: str | None = None
    feature_flag: str | None = None
    enabled: bool | None = None
    min_replicas: int | None = None
    message_required: bool = False
    description: str = ""


@dataclass(frozen=True)
class ScenarioDefinition:
    task_id: str
    difficulty: Difficulty
    title: str
    objective: str
    description: str
    business_impact: str
    max_steps: int
    affected_users: int
    root_cause: str
    initial_services: tuple[ServiceSeed, ...]
    initial_logs: tuple[LogSeed, ...]
    initial_alerts: tuple[str, ...]
    initial_metrics: dict[str, float]
    query_findings: tuple[QueryFinding, ...]
    required_findings: tuple[str, ...]
    required_actions: tuple[ActionRequirement, ...]
    avoid_actions: tuple[ActionRequirement, ...] = ()
    rca_keywords: tuple[str, ...] = ()


TASKS: dict[str, ScenarioDefinition] = {
    "cpu_spike": ScenarioDefinition(
        task_id="cpu_spike",
        difficulty="easy",
        title="Rollback A Bad API Deploy",
        objective=(
            "Investigate the api-gateway regression, restore the /search path to normal, "
            "and finish with a concise RCA."
        ),
        description=(
            "A fresh api-gateway deploy coincides with a CPU spike to 98 percent and a "
            "P99 latency breach. Users are timing out across the product."
        ),
        business_impact=(
            "Search and navigation are failing for thousands of active users, making the "
            "incident visible across the product within minutes."
        ),
        max_steps=10,
        affected_users=8400,
        root_cause="bad_deploy",
        initial_services=(
            ServiceSeed("api-gateway", "degraded", 0.12, 4200, 3),
            ServiceSeed("user-service", "healthy", 0.00, 45, 2),
            ServiceSeed("db-primary", "healthy", 0.00, 12, 1),
        ),
        initial_logs=(
            LogSeed("10:01:03", "INFO", "api-gateway", "Deploy v2.4.1 started"),
            LogSeed("10:01:47", "WARN", "api-gateway", "CPU utilization rising: 72%"),
            LogSeed(
                "10:02:11",
                "ERROR",
                "api-gateway",
                "CPU utilization critical: 98%, N+1 query detected in /search endpoint",
            ),
            LogSeed(
                "10:02:12",
                "FATAL",
                "api-gateway",
                "Request queue depth exceeded 10000, dropping requests",
            ),
            LogSeed(
                "10:02:15",
                "ERROR",
                "user-service",
                "Upstream api-gateway timeout after 5000ms",
            ),
        ),
        initial_alerts=(
            "ALERT: api-gateway CPU > 90% for 3m [P1]",
            "ALERT: P99 latency > 2000ms [P1]",
            "ALERT: Error rate > 10% [P1]",
        ),
        initial_metrics={
            "cpu_pct": 98.0,
            "mem_pct": 61.0,
            "req_per_sec": 1200.0,
            "error_rate": 0.12,
        },
        query_findings=(
            QueryFinding(
                "deploy_regression",
                ("deploy", "api-gateway", "search", "cpu", "n+1"),
                "api-gateway v2.4.1 introduced an N+1 query on /search immediately before the CPU spike.",
            ),
            QueryFinding(
                "symptom_spread",
                ("timeout", "upstream", "queue", "latency"),
                "The user-service failures are downstream symptoms of the api-gateway saturation, not a separate outage.",
            ),
        ),
        required_findings=("deploy_regression",),
        required_actions=(
            ActionRequirement(
                requirement_id="rollback_gateway",
                action_type="rollback",
                service_name="api-gateway",
                version="v2.4.0",
                description="Rollback api-gateway to the last known good build.",
            ),
        ),
        rca_keywords=("deploy", "rollback", "n+1", "cpu", "search"),
    ),
    "db_cascade": ScenarioDefinition(
        task_id="db_cascade",
        difficulty="medium",
        title="Stop A Database Connection Cascade",
        objective=(
            "Relieve the exhausted DB pool, restore user logins, and explain the cascade in the RCA."
        ),
        description=(
            "Auth logins are failing because the primary database is saturated. A cache hit-rate "
            "collapse and a leaking session-worker are amplifying the incident."
        ),
        business_impact=(
            "Login failure blocks tens of thousands of users from the product and creates a "
            "high-cost support and trust incident if recovery is slow."
        ),
        max_steps=14,
        affected_users=47000,
        root_cause="connection_pool_exhausted",
        initial_services=(
            ServiceSeed("api-gateway", "degraded", 0.34, 8900, 3),
            ServiceSeed("auth-service", "down", 0.89, 30000, 2),
            ServiceSeed("db-primary", "degraded", 0.45, 12000, 1),
            ServiceSeed("redis-cache", "healthy", 0.00, 3, 1),
            ServiceSeed("session-worker", "degraded", 0.60, 15000, 5),
        ),
        initial_logs=(
            LogSeed(
                "14:30:01",
                "ERROR",
                "db-primary",
                "Connection pool exhausted: 500/500 connections in use",
            ),
            LogSeed(
                "14:30:03",
                "ERROR",
                "auth-service",
                "Cannot acquire DB connection, timeout after 30s",
            ),
            LogSeed(
                "14:30:05",
                "WARN",
                "session-worker",
                "Leaking connections detected in session cleanup job",
            ),
            LogSeed(
                "14:30:08",
                "ERROR",
                "api-gateway",
                "Auth service unavailable, user logins failing",
            ),
            LogSeed(
                "14:30:09",
                "WARN",
                "redis-cache",
                "Cache hit rate dropped to 12%, DB load increasing",
            ),
            LogSeed(
                "14:30:15",
                "FATAL",
                "auth-service",
                "OOMKilled: heap dump written to /tmp/auth-20260326.hprof",
            ),
        ),
        initial_alerts=(
            "ALERT: auth-service down [P1] — users cannot log in",
            "ALERT: DB connection pool 100% utilized [P1]",
            "ALERT: Cache hit rate < 20% [P2]",
            "ALERT: session-worker memory leak suspected [P2]",
        ),
        initial_metrics={
            "cpu_pct": 45.0,
            "mem_pct": 88.0,
            "req_per_sec": 340.0,
            "error_rate": 0.56,
            "db_connections": 500.0,
            "cache_hit_rate": 0.12,
        },
        query_findings=(
            QueryFinding(
                "session_worker_connection_leak",
                ("session", "worker", "leak", "connection", "cleanup"),
                "session-worker cleanup jobs are leaking DB connections and keeping the primary pool pinned.",
            ),
            QueryFinding(
                "read_replica_pressure",
                ("cache", "read", "replica", "routing", "hit"),
                "Cache misses are forcing read traffic onto the primary. Enabling read-replica routing will cut pressure quickly.",
            ),
        ),
        required_findings=(
            "session_worker_connection_leak",
            "read_replica_pressure",
        ),
        required_actions=(
            ActionRequirement(
                requirement_id="restart_session_worker",
                action_type="restart_pod",
                service_name="session-worker",
                description="Restart the leaking worker to clear the stuck connections.",
            ),
            ActionRequirement(
                requirement_id="toggle_read_replica",
                action_type="toggle_feature",
                feature_flag="read_replica_routing",
                enabled=True,
                description="Shift safe reads off the primary DB.",
            ),
            ActionRequirement(
                requirement_id="scale_db_primary",
                action_type="scale_service",
                service_name="db-primary",
                min_replicas=2,
                description="Scale database capacity to absorb recovery load.",
            ),
        ),
        rca_keywords=(
            "connection pool",
            "leak",
            "session-worker",
            "read replica",
            "cache",
        ),
    ),
    "ddos_payment": ScenarioDefinition(
        task_id="ddos_payment",
        difficulty="hard",
        title="Mitigate A DDoS And Payment Outage",
        objective=(
            "Stabilize checkout traffic, restore payment flow with minimal blast radius, coordinate the right responders, and finish with an RCA."
        ),
        description=(
            "A DDoS is saturating checkout traffic at the edge while Stripe is independently returning 503s. "
            "Revenue impact is material and the environment penalizes noisy or irrelevant actions."
        ),
        business_impact=(
            "This is an active revenue and trust event: checkout is degraded, payments are failing, "
            "and the agent must coordinate security, payments, and customer communication under pressure."
        ),
        max_steps=16,
        affected_users=230000,
        root_cause="ddos_plus_payment_upstream",
        initial_services=(
            ServiceSeed("cdn-edge", "degraded", 0.22, 3100, 8),
            ServiceSeed("api-gateway", "degraded", 0.18, 2400, 6),
            ServiceSeed("payment-service", "down", 0.98, 0, 3),
            ServiceSeed("checkout-ui", "degraded", 0.55, 5600, 4),
            ServiceSeed("order-service", "healthy", 0.01, 120, 3),
            ServiceSeed("user-service", "healthy", 0.02, 89, 2),
            ServiceSeed("db-primary", "healthy", 0.00, 15, 1),
        ),
        initial_logs=(
            LogSeed(
                "02:14:02",
                "WARN",
                "cdn-edge",
                "Traffic spike: 4.2M req/s from 847 unique IPs, majority from AS7922",
            ),
            LogSeed(
                "02:14:05",
                "ERROR",
                "cdn-edge",
                "Rate limit triggers firing for /api/checkout - pattern matches DDoS signature",
            ),
            LogSeed(
                "02:14:07",
                "ERROR",
                "payment-service",
                "Stripe upstream 503: 'Service Unavailable' — incident ref STRIPE-XYZ",
            ),
            LogSeed(
                "02:14:08",
                "ERROR",
                "checkout-ui",
                "Payment failed for 55,000 transactions in last 60s",
            ),
            LogSeed(
                "02:14:10",
                "WARN",
                "api-gateway",
                "WAF rule 4021 partially blocking attack, false positive rate 3%",
            ),
            LogSeed(
                "02:14:12",
                "INFO",
                "order-service",
                "Fallback payment provider (Braintree) available and healthy",
            ),
            LogSeed(
                "02:14:15",
                "ERROR",
                "checkout-ui",
                "Customer-facing error rate 55% on /checkout",
            ),
        ),
        initial_alerts=(
            "ALERT: DDoS detected — 4.2M req/s [P0]",
            "ALERT: payment-service down — revenue impact $12k/min [P0]",
            "ALERT: checkout error rate 55% [P0]",
            "ALERT: CDN edge capacity at 89% [P1]",
        ),
        initial_metrics={
            "cpu_pct": 78.0,
            "mem_pct": 71.0,
            "req_per_sec": 4200000.0,
            "error_rate": 0.38,
            "revenue_impact_per_min": 12000.0,
            "legitimate_traffic_pct": 0.62,
        },
        query_findings=(
            QueryFinding(
                "edge_ddos_signature",
                ("ddos", "cdn", "waf", "traffic", "ip", "challenge"),
                "The traffic pattern is a volumetric layer-7 DDoS on /api/checkout. Challenge mode is the least disruptive mitigation.",
            ),
            QueryFinding(
                "stripe_upstream_outage",
                ("payment", "stripe", "503", "fallback", "braintree"),
                "Stripe is the separate upstream fault; Braintree fallback is healthy and can restore checkout without touching order-service.",
            ),
        ),
        required_findings=("edge_ddos_signature", "stripe_upstream_outage"),
        required_actions=(
            ActionRequirement(
                requirement_id="enable_challenge_mode",
                action_type="toggle_feature",
                feature_flag="ddos_challenge_mode",
                enabled=True,
                description="Mitigate the DDoS at the edge while preserving good traffic.",
            ),
            ActionRequirement(
                requirement_id="enable_payment_fallback",
                action_type="toggle_feature",
                feature_flag="payment_fallback_braintree",
                enabled=True,
                description="Route payment traffic away from the failing upstream.",
            ),
            ActionRequirement(
                requirement_id="page_security",
                action_type="page_team",
                team="security",
                description="Engage the security team for edge mitigation.",
            ),
            ActionRequirement(
                requirement_id="page_payments",
                action_type="page_team",
                team="payments",
                description="Engage payments specialists for the upstream outage.",
            ),
            ActionRequirement(
                requirement_id="post_status_update",
                action_type="post_status",
                message_required=True,
                description="Communicate impact and mitigation status externally.",
            ),
        ),
        avoid_actions=(
            ActionRequirement(
                requirement_id="avoid_scaling_db",
                action_type="scale_service",
                service_name="db-primary",
                description="Scaling the database does not address either failure domain.",
            ),
            ActionRequirement(
                requirement_id="avoid_restarting_orders",
                action_type="restart_pod",
                service_name="order-service",
                description="Restarting order-service needlessly widens blast radius.",
            ),
        ),
        rca_keywords=("ddos", "stripe", "fallback", "braintree", "challenge"),
    ),
}


def list_tasks() -> tuple[ScenarioDefinition, ...]:
    """Return tasks in deterministic order."""

    return tuple(TASKS[task_id] for task_id in ("cpu_spike", "db_cascade", "ddos_payment"))


def get_task(task: str | ScenarioDefinition | None) -> ScenarioDefinition:
    """Resolve a task definition from an identifier or return the object directly."""

    if isinstance(task, ScenarioDefinition):
        return task
    resolved_task_id = task or "cpu_spike"
    try:
        return TASKS[resolved_task_id]
    except KeyError as exc:
        raise KeyError(f"Unknown task_id: {resolved_task_id}") from exc


__all__ = [
    "ActionRequirement",
    "Difficulty",
    "LogSeed",
    "QueryFinding",
    "ScenarioDefinition",
    "ServiceSeed",
    "TASKS",
    "get_task",
    "list_tasks",
]
