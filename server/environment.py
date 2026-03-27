"""Server-side environment for Incident Commander tasks."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from openenv.core.env_server import Environment
from openenv.core.env_server.types import EnvironmentMetadata

from incident_commander.grading import grade_state
from incident_commander.models import (
    ActionTrace,
    IncidentAction,
    IncidentObservation,
    IncidentState,
    LogEntry,
    ServiceStatus,
)
from incident_commander.task_bank import QueryFinding, ScenarioDefinition, get_task, list_tasks


@dataclass(frozen=True)
class DispatchResult:
    summary: str
    destructive: bool = False


class IncidentCommanderEnvironment(
    Environment[IncidentAction, IncidentObservation, IncidentState]
):
    """Environment that simulates real-world SRE incident response."""

    SUPPORTS_CONCURRENT_SESSIONS = True

    def __init__(self) -> None:
        super().__init__()
        self._action_signatures_seen: set[str] = set()
        self._task = list_tasks()[0]
        self._state = self._build_state(self._task)
        self._started_at = datetime.now(timezone.utc)
        self._load_task(self._task)

    def reset(
        self,
        seed: int | None = None,
        episode_id: str | None = None,
        task_id: str | None = None,
        **_: object,
    ) -> IncidentObservation:
        """Reset the environment to a fresh deterministic scenario."""

        del seed
        resolved_task = get_task(task_id)
        self._action_signatures_seen.clear()
        self._load_task(resolved_task, episode_id=episode_id)
        return self._build_observation(
            reward=0.0,
            done=False,
            last_action_result="Incident initialized. Investigate quickly, mitigate safely, then submit an RCA.",
        )

    def step(
        self,
        action: IncidentAction,
        timeout_s: float | None = None,
        **_: object,
    ) -> IncidentObservation:
        """Apply one incident-response action."""

        del timeout_s
        self._state.step_count += 1
        before_score = self._state.current_progress_score
        repeated = self._mark_repeated(action)
        invalid = False

        try:
            result = self._dispatch(action)
        except ValueError as exc:
            invalid = True
            self._state.invalid_actions += 1
            result = DispatchResult(summary=f"Invalid action: {exc}")

        self._refresh_incident_state()

        trace = ActionTrace(
            step=self._state.step_count,
            action_type=action.action_type,
            params=action.model_dump(exclude_none=True, exclude={"action_type", "metadata"}, mode="json"),
            outcome=result.summary,
            reward_delta=0.0,
        )
        self._state.actions_taken.append(trace)

        graded = grade_state(self._state, self._task)
        self._state.current_progress_score = graded.score

        reward = graded.score - before_score - 0.01
        if repeated:
            self._state.repeated_actions += 1
            reward -= 0.02
            result = DispatchResult(
                summary=f"{result.summary} Repeating identical actions wastes incident time.",
                destructive=result.destructive,
            )
        if invalid:
            reward -= 0.05
        if result.destructive:
            reward -= 0.05

        done = False
        if action.action_type == "submit_rca" and bool(self._state.submitted_rca_text.strip()):
            done = True
            result = DispatchResult(
                summary=f"{result.summary} Episode finalized with RCA submission.",
                destructive=result.destructive,
            )
        if self._state.step_count >= self._state.max_steps:
            done = True
            result = DispatchResult(
                summary=f"{result.summary} Step budget exhausted.",
                destructive=result.destructive,
            )

        reward = round(reward, 4)
        trace.outcome = result.summary
        trace.reward_delta = reward
        self._state.total_reward = round(self._state.total_reward + reward, 4)
        self._state.last_action_result = result.summary

        return self._build_observation(
            reward=reward,
            done=done,
            last_action_result=result.summary,
        )

    @property
    def state(self) -> IncidentState:
        """Return the current serializable environment state."""

        return self._state

    def get_metadata(self) -> EnvironmentMetadata:
        """Expose metadata for /metadata."""

        return EnvironmentMetadata(
            name="IncidentCommanderEnvironment",
            description=(
                "A deterministic SRE incident-response simulator with production-style "
                "outages across deploy regressions, database cascades, and DDoS recovery."
            ),
            version="1.0.0",
            author="Codex",
        )

    def _load_task(
        self, task: ScenarioDefinition, episode_id: str | None = None
    ) -> None:
        self._task = task
        self._started_at = datetime.now(timezone.utc)
        self._state = self._build_state(task, episode_id=episode_id)
        self._refresh_incident_state()
        initial_grade = grade_state(self._state, self._task)
        self._state.current_progress_score = initial_grade.score

    def _build_state(
        self, task: ScenarioDefinition, episode_id: str | None = None
    ) -> IncidentState:
        started_at = datetime.now(timezone.utc).isoformat()
        return IncidentState(
            episode_id=episode_id or str(uuid4()),
            step_count=0,
            task_id=task.task_id,
            difficulty=task.difficulty,
            title=task.title,
            objective=task.objective,
            description=task.description,
            max_steps=task.max_steps,
            incident_id=f"INC-{task.task_id}-{uuid4().hex[:8]}",
            incident_started_at=started_at,
            affected_users=task.affected_users,
            services=[
                ServiceStatus(
                    name=service.name,
                    status=service.status,
                    error_rate=service.error_rate,
                    p99_latency_ms=service.p99_latency_ms,
                    replicas=service.replicas,
                )
                for service in task.initial_services
            ],
            logs=[
                LogEntry(
                    timestamp=log.timestamp,
                    level=log.level,
                    service=log.service,
                    message=log.message,
                )
                for log in task.initial_logs
            ],
            active_alerts=list(task.initial_alerts),
            metrics=dict(task.initial_metrics),
            feature_flags={},
            paged_teams=[],
            status_updates=[],
            query_history=[],
            investigation_finding_ids=[],
            investigation_findings=[],
            resolution_markers={},
            actions_taken=[],
            submitted_rca_text="",
            resolved=False,
            invalid_actions=0,
            repeated_actions=0,
            destructive_actions=[],
            current_progress_score=0.0,
            total_reward=0.0,
            last_action_result="Episode ready.",
        )

    def _build_observation(
        self, reward: float, done: bool, last_action_result: str
    ) -> IncidentObservation:
        graded = grade_state(self._state, self._task)
        return IncidentObservation(
            task_id=self._task.task_id,
            difficulty=self._task.difficulty,
            title=self._task.title,
            objective=self._task.objective,
            instructions=(
                "Use one atomic action per step. Query for evidence before irreversible changes, "
                "stabilize the right services, coordinate when needed, and end with submit_rca."
            ),
            step_count=self._state.step_count,
            steps_remaining=max(self._state.max_steps - self._state.step_count, 0),
            services=[service.model_copy(deep=True) for service in self._state.services],
            recent_logs=[entry.model_copy(deep=True) for entry in self._state.logs[-20:]],
            active_alerts=list(self._state.active_alerts),
            metrics=dict(self._state.metrics),
            incident_id=self._state.incident_id,
            incident_started_at=self._state.incident_started_at,
            elapsed_seconds=self._elapsed_seconds(),
            affected_users=self._state.affected_users,
            feature_flags=dict(self._state.feature_flags),
            paged_teams=list(self._state.paged_teams),
            status_updates=list(self._state.status_updates),
            investigation_findings=list(self._state.investigation_findings),
            actions_taken=[trace.model_copy(deep=True) for trace in self._state.actions_taken],
            progress=graded.metrics,
            resolved=self._state.resolved,
            last_action_result=last_action_result,
            reward=reward,
            done=done,
            metadata={
                "progress_score": graded.score,
                "score_breakdown": graded.breakdown,
                "resolved": self._state.resolved,
                "destructive_actions": list(self._state.destructive_actions),
                "submitted_rca": bool(self._state.submitted_rca_text.strip()),
            },
        )

    def _dispatch(self, action: IncidentAction) -> DispatchResult:
        if action.action_type == "run_query":
            return self._run_query(action)
        if action.action_type == "scale_service":
            return self._scale_service(action)
        if action.action_type == "restart_pod":
            return self._restart_pod(action)
        if action.action_type == "rollback":
            return self._rollback(action)
        if action.action_type == "page_team":
            return self._page_team(action)
        if action.action_type == "toggle_feature":
            return self._toggle_feature(action)
        if action.action_type == "post_status":
            return self._post_status(action)
        if action.action_type == "submit_rca":
            return self._submit_rca(action)
        raise ValueError(f"Unsupported action_type: {action.action_type}")

    def _run_query(self, action: IncidentAction) -> DispatchResult:
        query = (action.query or "").strip()
        if not query:
            raise ValueError("run_query requires a non-empty query")
        self._state.query_history.append(query)

        findings = self._matching_findings(query)
        if not findings:
            self._append_log("INFO", "query-engine", f"Query '{query[:80]}' returned noisy but non-decisive results")
            return DispatchResult("The query produced telemetry noise but no decisive new signal.")

        new_notes: list[str] = []
        for finding in findings:
            if finding.finding_id not in self._state.investigation_finding_ids:
                self._state.investigation_finding_ids.append(finding.finding_id)
                self._state.investigation_findings.append(finding.note)
                new_notes.append(finding.note)
        self._append_log("INFO", "query-engine", f"Query executed: {query[:80]}")
        if new_notes:
            return DispatchResult(f"Query surfaced new evidence: {' | '.join(new_notes)}")
        return DispatchResult("Query confirmed evidence already visible in the incident timeline.")

    def _scale_service(self, action: IncidentAction) -> DispatchResult:
        service = self._require_service(action.service_name, "scale_service")
        if action.replicas is None:
            raise ValueError("scale_service requires replicas")
        old_replicas = service.replicas
        service.replicas = action.replicas

        destructive = False
        if self._task.task_id == "ddos_payment" and service.name == "db-primary":
            destructive = True
            self._record_destructive_action("scale_service:db-primary")
        if self._task.task_id == "db_cascade" and service.name == "db-primary" and action.replicas >= 2:
            self._append_log("INFO", service.name, f"Scaled db-primary from {old_replicas} to {action.replicas} replicas")
            return DispatchResult("Scaled db-primary capacity to relieve pool exhaustion.")
        if self._task.task_id == "cpu_spike" and service.name == "api-gateway" and action.replicas > old_replicas:
            self._append_log("WARN", service.name, "Scaled api-gateway to buy time, but the bad deploy is still live")
            return DispatchResult("Scaled api-gateway for temporary symptom relief, but the regression remains active.")
        if self._task.task_id == "ddos_payment" and service.name == "cdn-edge" and action.replicas > old_replicas:
            self._append_log("INFO", service.name, f"Added edge capacity from {old_replicas} to {action.replicas} replicas")
            return DispatchResult("Added temporary edge capacity, though the attack signature still needs mitigation.")
        self._append_log("WARN", service.name, f"Scaled {service.name} from {old_replicas} to {action.replicas} with limited impact")
        return DispatchResult("Scaling completed, but it does not address the core failure mode.", destructive=destructive)

    def _restart_pod(self, action: IncidentAction) -> DispatchResult:
        service = self._require_service(action.service_name, "restart_pod")
        if self._task.task_id == "db_cascade" and service.name == "session-worker":
            self._state.resolution_markers["session_worker_restarted"] = True
            self._append_log("INFO", service.name, "Restarted session-worker pods and cleared leaked DB connections")
            return DispatchResult("Restarted session-worker and cleared the leaking connection pool clients.")
        if self._task.task_id == "ddos_payment" and service.name == "order-service":
            self._record_destructive_action("restart_pod:order-service")
            self._append_log("WARN", service.name, "Restarted a healthy order-service during checkout incident")
            return DispatchResult("Restarted order-service even though it was healthy, increasing blast radius.", destructive=True)
        self._append_log("WARN", service.name, f"Restarted {service.name}, but it was not the bottleneck")
        return DispatchResult("The restart completed, but it did not move the incident materially.")

    def _rollback(self, action: IncidentAction) -> DispatchResult:
        service = self._require_service(action.service_name, "rollback")
        version = (action.version or "").strip()
        if not version:
            raise ValueError("rollback requires version")
        if self._task.task_id == "cpu_spike" and service.name == "api-gateway" and version == "v2.4.0":
            self._state.resolution_markers["rollback_completed"] = True
            self._append_log("INFO", service.name, f"Rollback to {version} started; queues draining and latency recovering")
            return DispatchResult("Rolled api-gateway back to v2.4.0. The bad deploy is no longer serving traffic.")
        self._append_log("WARN", service.name, f"Rollback to {version} completed with no measurable incident improvement")
        return DispatchResult("Rollback executed, but it did not target the faulting component.")

    def _page_team(self, action: IncidentAction) -> DispatchResult:
        team = (action.team or "").strip().lower()
        if not team:
            raise ValueError("page_team requires team")
        if team not in self._state.paged_teams:
            self._state.paged_teams.append(team)
        self._append_log("INFO", "pagerduty", f"Paged {team} on-call")
        if self._task.task_id == "ddos_payment" and team in {"security", "payments"}:
            return DispatchResult(f"Paged the {team} team and engaged the right responders.")
        return DispatchResult(f"Paged team '{team}'.")

    def _toggle_feature(self, action: IncidentAction) -> DispatchResult:
        feature_flag = (action.feature_flag or "").strip()
        if not feature_flag:
            raise ValueError("toggle_feature requires feature_flag")
        if action.enabled is None:
            raise ValueError("toggle_feature requires enabled")
        self._state.feature_flags[feature_flag] = action.enabled
        self._append_log("INFO", "feature-flags", f"Set {feature_flag}={action.enabled}")

        if self._task.task_id == "db_cascade" and feature_flag == "read_replica_routing" and action.enabled:
            return DispatchResult("Enabled read-replica routing to pull read pressure off the primary database.")
        if self._task.task_id == "ddos_payment" and feature_flag == "ddos_challenge_mode" and action.enabled:
            return DispatchResult("Enabled DDoS challenge mode at the edge with a lower false-positive profile.")
        if self._task.task_id == "ddos_payment" and feature_flag == "payment_fallback_braintree" and action.enabled:
            return DispatchResult("Enabled Braintree payment fallback to route around the Stripe outage.")
        return DispatchResult("Feature flag updated, but the effect on the incident is limited.")

    def _post_status(self, action: IncidentAction) -> DispatchResult:
        message = (action.message or "").strip()
        if len(message) < 20:
            raise ValueError("post_status requires a message of at least 20 characters")
        self._state.status_updates.append(message)
        self._append_log("INFO", "status-page", f"Posted status update: {message[:120]}")
        return DispatchResult("Published a customer-facing status update.")

    def _submit_rca(self, action: IncidentAction) -> DispatchResult:
        message = (action.message or "").strip()
        if not message:
            raise ValueError("submit_rca requires message")
        self._state.submitted_rca_text = message
        self._append_log("INFO", "incident-docs", "RCA submitted to the incident timeline")
        return DispatchResult("Submitted the incident RCA.")

    def _require_service(self, service_name: str | None, action_name: str) -> ServiceStatus:
        if not service_name:
            raise ValueError(f"{action_name} requires service_name")
        services = {service.name: service for service in self._state.services}
        try:
            return services[service_name]
        except KeyError as exc:
            raise ValueError(f"Unknown service_name: {service_name}") from exc

    def _matching_findings(self, query: str) -> list[QueryFinding]:
        query_tokens = set(_tokenize(query))
        scored: list[tuple[int, QueryFinding]] = []
        for finding in self._task.query_findings:
            score = len(query_tokens & set(_tokenize(" ".join(finding.keywords))))
            if score > 0:
                scored.append((score, finding))
        scored.sort(key=lambda item: (-item[0], item[1].finding_id))
        return [finding for _, finding in scored[:2]]

    def _refresh_incident_state(self) -> None:
        if self._task.task_id == "cpu_spike":
            self._refresh_cpu_spike()
        elif self._task.task_id == "db_cascade":
            self._refresh_db_cascade()
        elif self._task.task_id == "ddos_payment":
            self._refresh_ddos_payment()

    def _refresh_cpu_spike(self) -> None:
        services = _service_map(self._state.services)
        gateway = services["api-gateway"]
        user_service = services["user-service"]
        db = services["db-primary"]
        initial_gateway_replicas = 3
        scale_bonus = max(gateway.replicas - initial_gateway_replicas, 0)

        if self._state.resolution_markers.get("rollback_completed"):
            gateway.status = "healthy"
            gateway.error_rate = 0.01
            gateway.p99_latency_ms = 115
            user_service.status = "healthy"
            user_service.error_rate = 0.00
            user_service.p99_latency_ms = 48
            db.status = "healthy"
            db.error_rate = 0.00
            db.p99_latency_ms = 12
            self._state.metrics = {
                "cpu_pct": 41.0,
                "mem_pct": 58.0,
                "req_per_sec": 1180.0,
                "error_rate": 0.01,
            }
            self._state.active_alerts = ["RECOVERY: api-gateway rollback complete, monitoring burn-down"]
            self._state.resolved = True
            return

        relief = min(scale_bonus * 0.12, 0.28)
        gateway.status = "degraded"
        gateway.error_rate = round(max(0.12 - (0.04 * scale_bonus), 0.07), 3)
        gateway.p99_latency_ms = round(max(4200 - (800 * scale_bonus), 2400), 1)
        user_service.status = "degraded" if gateway.p99_latency_ms > 2800 else "healthy"
        user_service.error_rate = 0.03 if user_service.status == "degraded" else 0.00
        user_service.p99_latency_ms = 900 if user_service.status == "degraded" else 45
        db.status = "healthy"
        db.error_rate = 0.00
        db.p99_latency_ms = 12
        self._state.metrics = {
            "cpu_pct": round(max(98.0 - (16.0 * scale_bonus), 80.0), 1),
            "mem_pct": round(61.0 + (1.0 * relief), 1),
            "req_per_sec": 1200.0,
            "error_rate": gateway.error_rate,
        }
        self._state.active_alerts = [
            "ALERT: api-gateway CPU > 90% for 3m [P1]",
            "ALERT: P99 latency > 2000ms [P1]",
        ]
        if gateway.error_rate >= 0.10:
            self._state.active_alerts.append("ALERT: Error rate > 10% [P1]")
        self._state.resolved = False

    def _refresh_db_cascade(self) -> None:
        services = _service_map(self._state.services)
        api = services["api-gateway"]
        auth = services["auth-service"]
        db = services["db-primary"]
        cache = services["redis-cache"]
        session = services["session-worker"]

        leak_cleared = self._state.resolution_markers.get("session_worker_restarted", False)
        replica_routing = self._state.feature_flags.get("read_replica_routing", False)
        db_scaled = db.replicas >= 2

        if leak_cleared and replica_routing and db_scaled:
            api.status = "healthy"
            api.error_rate = 0.02
            api.p99_latency_ms = 240
            auth.status = "healthy"
            auth.error_rate = 0.01
            auth.p99_latency_ms = 180
            db.status = "healthy"
            db.error_rate = 0.01
            db.p99_latency_ms = 65
            cache.status = "healthy"
            cache.error_rate = 0.00
            cache.p99_latency_ms = 4
            session.status = "healthy"
            session.error_rate = 0.02
            session.p99_latency_ms = 220
            self._state.metrics = {
                "cpu_pct": 39.0,
                "mem_pct": 62.0,
                "req_per_sec": 360.0,
                "error_rate": 0.02,
                "db_connections": 220.0,
                "cache_hit_rate": 0.74,
            }
            self._state.active_alerts = ["RECOVERY: auth-service logins restored and DB pool stable"]
            self._state.resolved = True
            return

        auth.status = "down"
        auth.error_rate = 0.89
        auth.p99_latency_ms = 30000
        api.status = "degraded"
        api.error_rate = 0.34
        api.p99_latency_ms = 8900
        db.status = "degraded"
        db.error_rate = 0.45
        db.p99_latency_ms = 12000
        cache.status = "healthy"
        cache.error_rate = 0.00
        cache.p99_latency_ms = 3
        session.status = "degraded"
        session.error_rate = 0.60
        session.p99_latency_ms = 15000

        error_rate = 0.56
        db_connections = 500.0
        cache_hit_rate = 0.12

        if leak_cleared:
            session.status = "healthy"
            session.error_rate = 0.06
            session.p99_latency_ms = 900
            error_rate -= 0.10
            db_connections -= 90.0

        if replica_routing:
            auth.status = "degraded"
            auth.error_rate = 0.34
            auth.p99_latency_ms = 6500
            api.p99_latency_ms = 4200
            error_rate -= 0.18
            db_connections -= 80.0
            cache_hit_rate = 0.43

        if db_scaled:
            db.error_rate = 0.22
            db.p99_latency_ms = 4200
            error_rate -= 0.12
            db_connections -= 120.0
            if auth.status == "degraded":
                auth.error_rate = 0.18
                auth.p99_latency_ms = 2800
                api.error_rate = 0.12
                api.p99_latency_ms = 2100

        self._state.metrics = {
            "cpu_pct": 45.0,
            "mem_pct": 88.0,
            "req_per_sec": 340.0,
            "error_rate": round(max(error_rate, 0.12), 2),
            "db_connections": round(max(db_connections, 260.0), 1),
            "cache_hit_rate": round(cache_hit_rate, 2),
        }
        self._state.active_alerts = [
            "ALERT: auth-service down [P1] — users cannot log in",
            "ALERT: DB connection pool 100% utilized [P1]",
        ]
        if not replica_routing:
            self._state.active_alerts.append("ALERT: Cache hit rate < 20% [P2]")
        if not leak_cleared:
            self._state.active_alerts.append("ALERT: session-worker memory leak suspected [P2]")
        self._state.resolved = False

    def _refresh_ddos_payment(self) -> None:
        services = _service_map(self._state.services)
        edge = services["cdn-edge"]
        api = services["api-gateway"]
        payment = services["payment-service"]
        checkout = services["checkout-ui"]
        order = services["order-service"]
        user = services["user-service"]
        db = services["db-primary"]

        ddos_mitigated = self._state.feature_flags.get("ddos_challenge_mode", False)
        payment_fallback = self._state.feature_flags.get("payment_fallback_braintree", False)
        edge_capacity_bonus = max(edge.replicas - 8, 0)

        edge.status = "degraded"
        edge.error_rate = 0.22
        edge.p99_latency_ms = 3100
        api.status = "degraded"
        api.error_rate = 0.18
        api.p99_latency_ms = 2400
        payment.status = "down"
        payment.error_rate = 0.98
        payment.p99_latency_ms = 0
        checkout.status = "degraded"
        checkout.error_rate = 0.55
        checkout.p99_latency_ms = 5600
        order.status = "healthy"
        order.error_rate = 0.01
        order.p99_latency_ms = 120
        user.status = "healthy"
        user.error_rate = 0.02
        user.p99_latency_ms = 89
        db.status = "healthy"
        db.error_rate = 0.00
        db.p99_latency_ms = 15

        req_per_sec = max(4200000.0 - (edge_capacity_bonus * 180000.0), 3200000.0)
        error_rate = 0.38
        legitimate_traffic_pct = 0.62
        revenue_impact = 12000.0

        if ddos_mitigated:
            edge.status = "healthy" if payment_fallback else "degraded"
            edge.error_rate = 0.03
            edge.p99_latency_ms = 480
            api.status = "healthy" if payment_fallback else "degraded"
            api.error_rate = 0.05
            api.p99_latency_ms = 520
            req_per_sec = 1650000.0
            legitimate_traffic_pct = 0.92
            error_rate -= 0.16

        if payment_fallback:
            payment.status = "healthy" if ddos_mitigated else "degraded"
            payment.error_rate = 0.03
            payment.p99_latency_ms = 180
            checkout.status = "healthy" if ddos_mitigated else "degraded"
            checkout.error_rate = 0.08 if ddos_mitigated else 0.24
            checkout.p99_latency_ms = 360 if ddos_mitigated else 1900
            revenue_impact = 0.0
            error_rate -= 0.17

        self._state.metrics = {
            "cpu_pct": 78.0 if not ddos_mitigated else 49.0,
            "mem_pct": 71.0 if not ddos_mitigated else 63.0,
            "req_per_sec": req_per_sec,
            "error_rate": round(max(error_rate, 0.04), 2),
            "revenue_impact_per_min": revenue_impact,
            "legitimate_traffic_pct": legitimate_traffic_pct,
        }
        self._state.active_alerts = []
        if not ddos_mitigated:
            self._state.active_alerts.append("ALERT: DDoS detected — 4.2M req/s [P0]")
            self._state.active_alerts.append("ALERT: CDN edge capacity at 89% [P1]")
        if not payment_fallback:
            self._state.active_alerts.append("ALERT: payment-service down — revenue impact $12k/min [P0]")
        if checkout.error_rate >= 0.10:
            self._state.active_alerts.append("ALERT: checkout error rate 55% [P0]")
        if ddos_mitigated and payment_fallback:
            self._state.active_alerts = ["RECOVERY: checkout stabilized on challenge mode + Braintree fallback"]
            self._state.resolved = True
            return
        self._state.resolved = False

    def _elapsed_seconds(self) -> int:
        return int((datetime.now(timezone.utc) - self._started_at).total_seconds())

    def _append_log(self, level: str, service: str, message: str) -> None:
        self._state.logs.append(
            LogEntry(
                timestamp=datetime.now(timezone.utc).strftime("%H:%M:%S"),
                level=level,  # type: ignore[arg-type]
                service=service,
                message=message,
            )
        )

    def _record_destructive_action(self, action_signature: str) -> None:
        self._state.destructive_actions.append(action_signature)

    def _mark_repeated(self, action: IncidentAction) -> bool:
        signature = json.dumps(
            action.model_dump(exclude_none=True, exclude={"metadata"}, mode="json"),
            sort_keys=True,
        )
        if signature in self._action_signatures_seen:
            return True
        self._action_signatures_seen.add(signature)
        return False


def _service_map(services: list[ServiceStatus]) -> dict[str, ServiceStatus]:
    return {service.name: service for service in services}


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_]+", text.lower())


SupportOpsEnvironment = IncidentCommanderEnvironment
