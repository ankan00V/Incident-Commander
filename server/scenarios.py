"""Deterministic production incident scenarios for Incident Commander."""

from __future__ import annotations

from typing import Any


SCENARIOS: dict[str, dict[str, Any]] = {
    "cpu_spike": {
        "id": "cpu_spike",
        "difficulty": "easy",
        "severity": "P1",
        "description": (
            "A deploy of api-gateway v2.4.1 shipped an N+1 SQL query bug that "
            "spiked CPU to 98 percent. P99 latency is 4200ms against a 500ms SLA, "
            "and 8,400 users cannot complete checkout."
        ),
        "root_cause": "bad_deploy_n_plus_1",
        "affected_users": 8_400,
        "revenue_impact_pm": 1_200.0,
        "feature_flags": {},
        "services": [
            {
                "name": "api-gateway",
                "status": "degraded",
                "error_rate": 0.12,
                "p99_latency_ms": 4200.0,
                "replicas": 3,
                "cpu_pct": 98.0,
                "mem_pct": 61.0,
            },
            {
                "name": "user-service",
                "status": "healthy",
                "error_rate": 0.00,
                "p99_latency_ms": 45.0,
                "replicas": 2,
                "cpu_pct": 22.0,
                "mem_pct": 38.0,
            },
            {
                "name": "db-primary",
                "status": "healthy",
                "error_rate": 0.00,
                "p99_latency_ms": 12.0,
                "replicas": 1,
                "cpu_pct": 41.0,
                "mem_pct": 55.0,
            },
            {
                "name": "redis-cache",
                "status": "healthy",
                "error_rate": 0.00,
                "p99_latency_ms": 2.0,
                "replicas": 1,
                "cpu_pct": 8.0,
                "mem_pct": 24.0,
            },
        ],
        "logs": [
            {
                "timestamp": "10:01:03",
                "level": "INFO",
                "service": "deploy-bot",
                "message": "Deployment started: api-gateway v2.4.1 to production",
            },
            {
                "timestamp": "10:01:47",
                "level": "WARN",
                "service": "api-gateway",
                "message": "CPU utilization rising: 72 percent",
            },
            {
                "timestamp": "10:02:11",
                "level": "ERROR",
                "service": "api-gateway",
                "message": (
                    "CPU critical: 98 percent. N+1 query detected in GET /products "
                    "handler: 847 DB calls per request"
                ),
            },
            {
                "timestamp": "10:02:12",
                "level": "FATAL",
                "service": "api-gateway",
                "message": "Request queue depth 10000/10000. Dropping new connections",
            },
            {
                "timestamp": "10:02:14",
                "level": "ERROR",
                "service": "user-service",
                "message": "Upstream api-gateway timeout after 5000ms on /api/checkout",
            },
            {
                "timestamp": "10:02:19",
                "level": "WARN",
                "service": "db-primary",
                "message": "Query throughput spike: 12400 QPS (normal: 1800 QPS)",
            },
        ],
        "alerts": [
            "[P1] api-gateway CPU > 90 percent for 3 minutes",
            "[P1] P99 latency 4200ms - SLA breach",
            "[P1] Checkout error rate: 12 percent",
            "[P2] DB query throughput 6.9x above baseline",
        ],
        "metrics": {
            "cpu_pct": 98.0,
            "mem_pct": 61.0,
            "req_per_sec": 1200.0,
            "global_error_rate": 0.12,
            "db_qps": 12400.0,
            "cache_hit_rate": 0.89,
        },
        "resolution": {
            "required_actions": ["rollback"],
            "target_service": "api-gateway",
            "target_version_prefix": "v2.4.0",
            "rca_keywords": ["deploy", "n+1", "query", "rollback", "cpu", "v2.4.1"],
        },
    },
    "db_cascade": {
        "id": "db_cascade",
        "difficulty": "medium",
        "severity": "P1",
        "description": (
            "The session-worker service has a connection leak, exhausting the DB "
            "connection pool. auth-service is down, users cannot log in, and cache "
            "warming has collapsed."
        ),
        "root_cause": "connection_pool_exhaustion_from_leak",
        "affected_users": 47_000,
        "revenue_impact_pm": 8_500.0,
        "feature_flags": {"read_replica_routing": False},
        "services": [
            {
                "name": "api-gateway",
                "status": "degraded",
                "error_rate": 0.34,
                "p99_latency_ms": 8900.0,
                "replicas": 3,
                "cpu_pct": 55.0,
                "mem_pct": 62.0,
            },
            {
                "name": "auth-service",
                "status": "down",
                "error_rate": 0.89,
                "p99_latency_ms": 30000.0,
                "replicas": 2,
                "cpu_pct": 12.0,
                "mem_pct": 94.0,
            },
            {
                "name": "db-primary",
                "status": "degraded",
                "error_rate": 0.45,
                "p99_latency_ms": 12000.0,
                "replicas": 1,
                "cpu_pct": 88.0,
                "mem_pct": 91.0,
            },
            {
                "name": "redis-cache",
                "status": "healthy",
                "error_rate": 0.00,
                "p99_latency_ms": 3.0,
                "replicas": 1,
                "cpu_pct": 14.0,
                "mem_pct": 45.0,
            },
            {
                "name": "session-worker",
                "status": "degraded",
                "error_rate": 0.60,
                "p99_latency_ms": 15000.0,
                "replicas": 5,
                "cpu_pct": 34.0,
                "mem_pct": 97.0,
            },
        ],
        "logs": [
            {
                "timestamp": "14:30:01",
                "level": "ERROR",
                "service": "db-primary",
                "message": "Connection pool exhausted: 500/500 connections in use",
            },
            {
                "timestamp": "14:30:03",
                "level": "ERROR",
                "service": "auth-service",
                "message": "Cannot acquire DB connection after 30s timeout",
            },
            {
                "timestamp": "14:30:05",
                "level": "WARN",
                "service": "session-worker",
                "message": (
                    "Connection leak detected in session_cleanup_job.py:84. "
                    "Connections not released on exception"
                ),
            },
            {
                "timestamp": "14:30:08",
                "level": "ERROR",
                "service": "api-gateway",
                "message": "auth-service returning 503, login requests failing",
            },
            {
                "timestamp": "14:30:09",
                "level": "WARN",
                "service": "redis-cache",
                "message": "Cache hit rate dropped from 91 percent to 12 percent",
            },
            {
                "timestamp": "14:30:15",
                "level": "FATAL",
                "service": "auth-service",
                "message": "OOMKilled (heap exhausted): heap dump written to /tmp/auth-heap.hprof",
            },
            {
                "timestamp": "14:30:22",
                "level": "WARN",
                "service": "db-primary",
                "message": "Read replica lag: 48s. Read routing would still help auth reads",
            },
        ],
        "alerts": [
            "[P1] auth-service DOWN - users cannot log in",
            "[P1] DB connection pool 100 percent utilized",
            "[P2] session-worker memory leak (97 percent mem)",
            "[P2] Cache hit rate < 20 percent - DB overloaded",
            "[P2] auth-service OOMKilled (restarting)",
        ],
        "metrics": {
            "cpu_pct": 55.0,
            "mem_pct": 88.0,
            "req_per_sec": 340.0,
            "global_error_rate": 0.56,
            "db_connections_used": 500,
            "db_connections_max": 500,
            "cache_hit_rate": 0.12,
            "auth_success_rate": 0.03,
        },
        "resolution": {
            "required_actions": ["restart_pod", "toggle_feature"],
            "target_restart": "session-worker",
            "target_feature_flag": "read_replica_routing",
            "target_enabled": True,
            "rca_keywords": [
                "connection pool",
                "leak",
                "session-worker",
                "exhausted",
                "auth",
            ],
        },
    },
    "ddos_payment": {
        "id": "ddos_payment",
        "difficulty": "hard",
        "severity": "P0",
        "description": (
            "A volumetric DDoS is saturating CDN edge capacity while Stripe is "
            "returning 503s due to its own outage. Checkout is failing and revenue "
            "is burning at $12,000 per minute."
        ),
        "root_cause": "ddos_plus_stripe_upstream_outage",
        "affected_users": 230_000,
        "revenue_impact_pm": 12_000.0,
        "feature_flags": {
            "ddos_challenge_mode": False,
            "payment_fallback_braintree": False,
        },
        "services": [
            {
                "name": "cdn-edge",
                "status": "degraded",
                "error_rate": 0.22,
                "p99_latency_ms": 3100.0,
                "replicas": 8,
                "cpu_pct": 89.0,
                "mem_pct": 72.0,
            },
            {
                "name": "api-gateway",
                "status": "degraded",
                "error_rate": 0.18,
                "p99_latency_ms": 2400.0,
                "replicas": 6,
                "cpu_pct": 78.0,
                "mem_pct": 61.0,
            },
            {
                "name": "payment-service",
                "status": "down",
                "error_rate": 0.98,
                "p99_latency_ms": 0.0,
                "replicas": 3,
                "cpu_pct": 12.0,
                "mem_pct": 45.0,
            },
            {
                "name": "checkout-ui",
                "status": "degraded",
                "error_rate": 0.55,
                "p99_latency_ms": 5600.0,
                "replicas": 4,
                "cpu_pct": 61.0,
                "mem_pct": 58.0,
            },
            {
                "name": "order-service",
                "status": "healthy",
                "error_rate": 0.01,
                "p99_latency_ms": 120.0,
                "replicas": 3,
                "cpu_pct": 18.0,
                "mem_pct": 41.0,
            },
            {
                "name": "user-service",
                "status": "healthy",
                "error_rate": 0.02,
                "p99_latency_ms": 89.0,
                "replicas": 2,
                "cpu_pct": 21.0,
                "mem_pct": 38.0,
            },
            {
                "name": "db-primary",
                "status": "healthy",
                "error_rate": 0.00,
                "p99_latency_ms": 15.0,
                "replicas": 1,
                "cpu_pct": 39.0,
                "mem_pct": 51.0,
            },
        ],
        "logs": [
            {
                "timestamp": "02:14:02",
                "level": "WARN",
                "service": "cdn-edge",
                "message": "Traffic spike: 4.2M req/s from 847 source IPs",
            },
            {
                "timestamp": "02:14:05",
                "level": "ERROR",
                "service": "cdn-edge",
                "message": "WAF signature 4021 matched but challenge mode is OFF",
            },
            {
                "timestamp": "02:14:07",
                "level": "ERROR",
                "service": "payment-service",
                "message": "Stripe API 503. Upstream incident active",
            },
            {
                "timestamp": "02:14:08",
                "level": "ERROR",
                "service": "checkout-ui",
                "message": "Payment failure: 55,000 transactions failed in the last 60s",
            },
            {
                "timestamp": "02:14:10",
                "level": "WARN",
                "service": "api-gateway",
                "message": "WAF partially blocking: 3 percent false positive rate",
            },
            {
                "timestamp": "02:14:12",
                "level": "INFO",
                "service": "payment-service",
                "message": "Braintree fallback is healthy but payment_fallback_braintree is OFF",
            },
            {
                "timestamp": "02:14:15",
                "level": "ERROR",
                "service": "checkout-ui",
                "message": "Customer-facing checkout error rate 55 percent. No status page post yet",
            },
            {
                "timestamp": "02:14:18",
                "level": "WARN",
                "service": "cdn-edge",
                "message": "Edge capacity at 89 percent. Failure expected in roughly 7 minutes",
            },
        ],
        "alerts": [
            "[P0] DDoS attack: 4.2M req/s - CDN edge at 89 percent capacity",
            "[P0] payment-service DOWN - Stripe upstream incident",
            "[P0] Checkout error rate: 55 percent - $12,000/min revenue impact",
            "[P1] WAF challenge mode disabled",
            "[P1] Braintree fallback available but not activated",
            "[P2] No user communication posted",
        ],
        "metrics": {
            "cpu_pct": 78.0,
            "mem_pct": 71.0,
            "req_per_sec": 4_200_000.0,
            "global_error_rate": 0.38,
            "revenue_impact_per_min": 12_000.0,
            "legitimate_traffic_pct": 0.62,
            "ddos_traffic_pct": 0.38,
            "stripe_status": "down",
            "braintree_status": "healthy",
        },
        "resolution": {
            "required_features": [
                {"flag": "ddos_challenge_mode", "enabled": True},
                {"flag": "payment_fallback_braintree", "enabled": True},
            ],
            "required_pages": ["security", "payments"],
            "required_actions": ["post_status", "toggle_feature", "page_team"],
            "penalized_actions": [
                "restart_pod:order-service",
                "restart_pod:db-primary",
                "rollback:cdn-edge",
                "page_team:database",
            ],
            "rca_keywords": [
                "ddos",
                "stripe",
                "braintree",
                "fallback",
                "challenge",
                "payment",
                "attack",
            ],
        },
    },
}
