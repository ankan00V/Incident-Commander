"""Deterministic task bank for the Incident Commander environment."""

from __future__ import annotations

from dataclasses import dataclass, replace
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
                requirement_id="page_database_team",
                action_type="page_team",
                team="database",
                description="Engage the database team while the auth outage is live.",
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
    "runbook_failure": ScenarioDefinition(
        task_id="runbook_failure",
        difficulty="hard",
        title="Ignore An Outdated Login Runbook",
        objective=(
            "Restore login traffic, identify why the documented runbook is wrong, coordinate the database responders, and finish with an RCA."
        ),
        description=(
            "The incident runbook says to restart auth-service first, but that guidance is stale. "
            "auth-service is healthy internally and is instead failing closed on replica lag. "
            "Blindly following the runbook widens the outage."
        ),
        business_impact=(
            "Login and session-refresh failures block tens of thousands of users. "
            "This task rewards agents that investigate and deliberately deviate from bad operational guidance."
        ),
        max_steps=14,
        affected_users=31000,
        root_cause="outdated_runbook_and_replica_fail_closed",
        initial_services=(
            ServiceSeed("api-gateway", "degraded", 0.18, 2400, 4),
            ServiceSeed("auth-service", "down", 0.72, 12000, 4),
            ServiceSeed("db-replica", "degraded", 0.08, 1800, 2),
            ServiceSeed("db-primary", "healthy", 0.01, 70, 2),
            ServiceSeed("session-store", "healthy", 0.00, 18, 2),
        ),
        initial_logs=(
            LogSeed(
                "09:41:02",
                "WARN",
                "auth-service",
                "Runbook step 1 still says: restart auth-service when login 5xx exceeds 5%",
            ),
            LogSeed(
                "09:41:05",
                "ERROR",
                "auth-service",
                "Dependency gate tripped: replica lag 11.8s exceeds 10s threshold, fail-closed mode enabled",
            ),
            LogSeed(
                "09:41:07",
                "INFO",
                "auth-service",
                "Process healthy, readiness 100%, no local crash loops detected",
            ),
            LogSeed(
                "09:41:09",
                "ERROR",
                "api-gateway",
                "POST /auth/token upstream 503 from auth-service",
            ),
            LogSeed(
                "09:41:11",
                "WARN",
                "db-replica",
                "Replica lag 11.8s after vacuum storm; primary remains healthy",
            ),
            LogSeed(
                "09:41:14",
                "INFO",
                "platform-config",
                "Feature flag auth_reads_use_primary available for incident fail-open",
            ),
        ),
        initial_alerts=(
            "ALERT: login success rate < 30% [P1]",
            "ALERT: auth-service returning 503s [P1]",
            "ALERT: db-replica lag > 10s [P2]",
            "ALERT: auth runbook execution may be stale [P2]",
        ),
        initial_metrics={
            "cpu_pct": 31.0,
            "mem_pct": 54.0,
            "req_per_sec": 950.0,
            "error_rate": 0.31,
            "login_success_rate": 0.24,
            "replica_lag_s": 11.8,
        },
        query_findings=(
            QueryFinding(
                "outdated_runbook_guidance",
                ("runbook", "restart", "outdated", "healthy", "readiness", "auth"),
                "auth-service itself is healthy. Restarting it follows stale runbook guidance and only adds avoidable downtime.",
            ),
            QueryFinding(
                "replica_fail_closed",
                ("replica", "lag", "circuit", "fail-closed", "primary", "reads"),
                "The real fix is to fail open onto primary reads while the replica catches up. This restores login traffic without restarting auth-service.",
            ),
        ),
        required_findings=("outdated_runbook_guidance", "replica_fail_closed"),
        required_actions=(
            ActionRequirement(
                requirement_id="enable_primary_read_failover",
                action_type="toggle_feature",
                feature_flag="auth_reads_use_primary",
                enabled=True,
                description="Route auth reads to the healthy primary while replica lag drains.",
            ),
            ActionRequirement(
                requirement_id="page_database_team",
                action_type="page_team",
                team="database",
                description="Engage the database team to drain replica lag and validate recovery.",
            ),
            ActionRequirement(
                requirement_id="post_login_status",
                action_type="post_status",
                message_required=True,
                description="Communicate the login impact and workaround externally.",
            ),
        ),
        avoid_actions=(
            ActionRequirement(
                requirement_id="avoid_restarting_auth",
                action_type="restart_pod",
                service_name="auth-service",
                description="Restarting auth-service widens the outage without fixing replica lag.",
            ),
        ),
        rca_keywords=(
            "runbook",
            "outdated",
            "replica lag",
            "primary reads",
            "auth-service",
            "restart",
        ),
    ),
}

VARIANT_LABELS: tuple[str, ...] = (
    "canonical",
    "template_a",
    "template_b",
    "template_c",
)

LOG_TEMPLATE_VARIANTS: dict[str, dict[str, dict[str, str]]] = {
    "cpu_spike": {
        "template_a": {
            "Deploy v2.4.1 started": "Rollout v2.4.1 reached 100% pods",
            "CPU utilization rising: 72%": "Autoscaler signal: api-gateway CPU has crossed 72%",
            "CPU utilization critical: 98%, N+1 query detected in /search endpoint": (
                "Critical saturation at 98% CPU; profiler shows N+1 path in /search"
            ),
        },
        "template_b": {
            "Deploy v2.4.1 started": "Release train pushed api-gateway v2.4.1 live",
            "Request queue depth exceeded 10000, dropping requests": (
                "Ingress queue exceeded 10k entries, requests are being dropped"
            ),
            "Upstream api-gateway timeout after 5000ms": "api-gateway dependency timed out after 5000ms",
        },
        "template_c": {
            "Deploy v2.4.1 started": "Argo rollout applied api-gateway v2.4.1",
            "CPU utilization rising: 72%": "Compute pressure warning: gateway CPU is trending above 70%",
            "Request queue depth exceeded 10000, dropping requests": (
                "Queue depth breached 10k and shed load is now active"
            ),
        },
    },
    "db_cascade": {
        "template_a": {
            "Connection pool exhausted: 500/500 connections in use": (
                "Primary pool exhaustion: 500 of 500 DB connections consumed"
            ),
            "Leaking connections detected in session cleanup job": (
                "session-worker cleanup job is leaking persistent DB handles"
            ),
            "Cache hit rate dropped to 12%, DB load increasing": (
                "Cache hit rate collapsed to 12%; read pressure on primary is spiking"
            ),
        },
        "template_b": {
            "Cannot acquire DB connection, timeout after 30s": (
                "auth-service waited 30s for a DB slot and then timed out"
            ),
            "Leaking connections detected in session cleanup job": (
                "session cleanup workers show leaked connection handles"
            ),
            "OOMKilled: heap dump written to /tmp/auth-20260326.hprof": (
                "auth-service OOMKilled; heap snapshot written to /tmp/auth-20260326.hprof"
            ),
        },
        "template_c": {
            "Connection pool exhausted: 500/500 connections in use": (
                "db-primary pool hard-capped at 500 active connections"
            ),
            "Auth service unavailable, user logins failing": (
                "Login traffic is failing because auth-service cannot establish DB sessions"
            ),
            "Cache hit rate dropped to 12%, DB load increasing": (
                "redis hit rate dropped to 12%; fallback reads are hammering the primary"
            ),
        },
    },
    "ddos_payment": {
        "template_a": {
            "Traffic spike: 4.2M req/s from 847 unique IPs, majority from AS7922": (
                "Volumetric spike at 4.2M req/s from 847 IPs, dominated by AS7922"
            ),
            "Stripe upstream 503: 'Service Unavailable' — incident ref STRIPE-XYZ": (
                "Stripe upstream returning HTTP 503 (incident ref STRIPE-XYZ)"
            ),
            "Fallback payment provider (Braintree) available and healthy": (
                "Braintree fallback rail is healthy and ready for traffic shift"
            ),
        },
        "template_b": {
            "Rate limit triggers firing for /api/checkout - pattern matches DDoS signature": (
                "Edge rate-limit triggers are firing on /api/checkout; signature matches active L7 DDoS"
            ),
            "Stripe upstream 503: 'Service Unavailable' — incident ref STRIPE-XYZ": (
                "Payment upstream failure: Stripe 503 with open vendor incident STRIPE-XYZ"
            ),
            "Customer-facing error rate 55% on /checkout": (
                "Public checkout failure rate has reached 55%"
            ),
        },
        "template_c": {
            "Traffic spike: 4.2M req/s from 847 unique IPs, majority from AS7922": (
                "Attack traffic surged to 4.2M req/s across 847 source IPs (AS7922 heavy)"
            ),
            "Payment failed for 55,000 transactions in last 60s": (
                "55,000 payment attempts failed in the last minute"
            ),
            "WAF rule 4021 partially blocking attack, false positive rate 3%": (
                "WAF-4021 is partially effective; current false-positive rate is 3%"
            ),
        },
    },
    "runbook_failure": {
        "template_a": {
            "Runbook step 1 still says: restart auth-service when login 5xx exceeds 5%": (
                "Runbook v1 step 1 still recommends restarting auth-service on login 5xx > 5%"
            ),
            "Dependency gate tripped: replica lag 11.8s exceeds 10s threshold, fail-closed mode enabled": (
                "Dependency guard tripped: replica lag 11.8s > 10s, auth switched to fail-closed"
            ),
            "Feature flag auth_reads_use_primary available for incident fail-open": (
                "Incident flag auth_reads_use_primary is available for emergency read fail-open"
            ),
        },
        "template_b": {
            "Runbook step 1 still says: restart auth-service when login 5xx exceeds 5%": (
                "Legacy runbook advises auth-service restart for elevated login 5xx"
            ),
            "Process healthy, readiness 100%, no local crash loops detected": (
                "auth-service process healthy; readiness 100%; crash-loop signal absent"
            ),
            "Replica lag 11.8s after vacuum storm; primary remains healthy": (
                "Replica lag is 11.8s after a vacuum burst while primary stays healthy"
            ),
        },
        "template_c": {
            "Dependency gate tripped: replica lag 11.8s exceeds 10s threshold, fail-closed mode enabled": (
                "Replica lag breached auth fail-closed threshold (11.8s vs 10s)"
            ),
            "POST /auth/token upstream 503 from auth-service": (
                "api-gateway is receiving upstream 503 responses from /auth/token"
            ),
            "Feature flag auth_reads_use_primary available for incident fail-open": (
                "Fail-open flag auth_reads_use_primary is ready for controlled activation"
            ),
        },
    },
}


def list_task_variants() -> tuple[str, ...]:
    """Return supported deterministic variant labels."""

    return VARIANT_LABELS


def variant_for_seed(seed: int | None) -> str:
    """Map a reset seed to a deterministic task-variant label."""

    if seed is None:
        return "canonical"
    return VARIANT_LABELS[abs(seed) % len(VARIANT_LABELS)]


def get_task_variant(
    task: str | ScenarioDefinition | None,
    seed: int | None = None,
) -> tuple[ScenarioDefinition, str]:
    """Resolve a task and apply deterministic log-template permutations based on seed."""

    resolved_task = get_task(task)
    variant_label = variant_for_seed(seed)
    if variant_label == "canonical":
        return resolved_task, variant_label

    task_variants = LOG_TEMPLATE_VARIANTS.get(resolved_task.task_id, {})
    template_mapping = task_variants.get(variant_label, {})
    if not template_mapping:
        return resolved_task, variant_label

    variant_logs = tuple(
        replace(log, message=template_mapping.get(log.message, log.message))
        for log in resolved_task.initial_logs
    )
    variant_task = replace(resolved_task, initial_logs=variant_logs)
    return variant_task, variant_label


def list_tasks() -> tuple[ScenarioDefinition, ...]:
    """Return tasks in deterministic order."""

    return tuple(
        TASKS[task_id]
        for task_id in ("cpu_spike", "db_cascade", "ddos_payment", "runbook_failure")
    )


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
    "LOG_TEMPLATE_VARIANTS",
    "LogSeed",
    "QueryFinding",
    "ScenarioDefinition",
    "ServiceSeed",
    "TASKS",
    "get_task",
    "get_task_variant",
    "list_tasks",
    "list_task_variants",
    "variant_for_seed",
]
