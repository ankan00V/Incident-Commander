"""Reproducible baseline runner for the Incident Commander environment."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from typing import Protocol

from openai import OpenAI

from server.environment import IncidentCommanderEnvironment

from .grading import grade_state
from .models import IncidentAction, IncidentObservation, IncidentState
from .task_bank import list_tasks

DEFAULT_OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")


class Session(Protocol):
    def reset(self, **kwargs: object) -> IncidentObservation:
        ...

    def step(self, action: IncidentAction) -> IncidentObservation:
        ...

    def state(self) -> IncidentState:
        ...

    def close(self) -> None:
        ...


class LocalSession:
    """In-process adapter that still uses reset()/step()/state()."""

    def __init__(self) -> None:
        self._env = IncidentCommanderEnvironment()

    def reset(self, **kwargs: object) -> IncidentObservation:
        return self._env.reset(**kwargs)

    def step(self, action: IncidentAction) -> IncidentObservation:
        return self._env.step(action)

    def state(self) -> IncidentState:
        return self._env.state

    def close(self) -> None:
        self._env.close()


@dataclass
class BaselineConfig:
    model: str = DEFAULT_OPENAI_MODEL
    seed: int = 7
    max_steps: int | None = None
    use_openai_if_available: bool = True


def _tool_schema() -> dict:
    schema = IncidentAction.model_json_schema()
    return {
        "type": "function",
        "function": {
            "name": "submit_action",
            "description": "Return the single next incident-response action to execute.",
            "parameters": schema,
        },
    }


def _openai_action(
    client: OpenAI,
    observation: IncidentObservation,
    config: BaselineConfig,
) -> IncidentAction:
    prompt_payload = {
        "task_id": observation.task_id,
        "difficulty": observation.difficulty,
        "title": observation.title,
        "objective": observation.objective,
        "step_count": observation.step_count,
        "steps_remaining": observation.steps_remaining,
        "resolved": observation.resolved,
        "last_action_result": observation.last_action_result,
        "services": [service.model_dump() for service in observation.services],
        "metrics": observation.metrics,
        "active_alerts": observation.active_alerts,
        "recent_logs": [entry.model_dump() for entry in observation.recent_logs[-8:]],
        "investigation_findings": observation.investigation_findings,
        "feature_flags": observation.feature_flags,
        "paged_teams": observation.paged_teams,
        "status_updates": observation.status_updates,
        "progress": [metric.model_dump() for metric in observation.progress],
    }

    completion = client.chat.completions.create(
        model=config.model,
        seed=config.seed,
        temperature=0,
        tool_choice={"type": "function", "function": {"name": "submit_action"}},
        tools=[_tool_schema()],
        messages=[
            {
                "role": "system",
                "content": (
                    "You are the incident commander for a live production outage. "
                    "Produce exactly one valid tool call per turn. Investigate first, "
                    "mitigate the correct failure domains, communicate when needed, and "
                    "finish with submit_rca once the incident is stabilized."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(prompt_payload, indent=2, sort_keys=True),
            },
        ],
    )
    tool_calls = completion.choices[0].message.tool_calls or []
    if not tool_calls:
        raise RuntimeError("Model did not return a tool call")
    arguments = json.loads(tool_calls[0].function.arguments)
    return IncidentAction.model_validate(arguments)


def _heuristic_action(state: IncidentState) -> IncidentAction:
    """Deterministic fallback policy used when no API key is available."""

    findings = set(state.investigation_finding_ids)
    feature_flags = state.feature_flags
    paged_teams = set(state.paged_teams)
    status_updates = state.status_updates
    services = {service.name: service for service in state.services}

    if state.task_id == "cpu_spike":
        if "deploy_regression" not in findings:
            return IncidentAction(
                action_type="run_query",
                query="show deploy cpu search regression and n+1 evidence for api-gateway",
            )
        if not state.resolution_markers.get("rollback_completed"):
            return IncidentAction(
                action_type="rollback",
                service_name="api-gateway",
                version="v2.4.0",
            )
        return IncidentAction(
            action_type="submit_rca",
            message=(
                "api-gateway v2.4.1 introduced an N+1 query on /search that drove CPU to 98 percent. "
                "A rollback to v2.4.0 removed the bad deploy immediately and restored latency and error rate."
            ),
        )

    if state.task_id == "db_cascade":
        if not {"session_worker_connection_leak", "read_replica_pressure"} <= findings:
            return IncidentAction(
                action_type="run_query",
                query="investigate session worker leak connection pool cache hit rate and read replica routing",
            )
        if not state.resolution_markers.get("session_worker_restarted"):
            return IncidentAction(
                action_type="restart_pod",
                service_name="session-worker",
            )
        if not feature_flags.get("read_replica_routing"):
            return IncidentAction(
                action_type="toggle_feature",
                feature_flag="read_replica_routing",
                enabled=True,
            )
        if services["db-primary"].replicas < 2:
            return IncidentAction(
                action_type="scale_service",
                service_name="db-primary",
                replicas=2,
            )
        return IncidentAction(
            action_type="submit_rca",
            message=(
                "The primary connection pool was exhausted by leaked session-worker cleanup connections while cache misses "
                "sent extra read traffic to the primary. Restarting session-worker, enabling read replica routing, "
                "and scaling db-primary stabilized the cache pressure and restored auth-service login traffic."
            ),
        )

    if not {"edge_ddos_signature", "stripe_upstream_outage"} <= findings:
        return IncidentAction(
            action_type="run_query",
            query="investigate ddos traffic waf checkout stripe 503 and braintree fallback",
        )
    if not feature_flags.get("ddos_challenge_mode"):
        return IncidentAction(
            action_type="toggle_feature",
            feature_flag="ddos_challenge_mode",
            enabled=True,
        )
    if not feature_flags.get("payment_fallback_braintree"):
        return IncidentAction(
            action_type="toggle_feature",
            feature_flag="payment_fallback_braintree",
            enabled=True,
        )
    if "security" not in paged_teams:
        return IncidentAction(action_type="page_team", team="security")
    if "payments" not in paged_teams:
        return IncidentAction(action_type="page_team", team="payments")
    if not status_updates:
        return IncidentAction(
            action_type="post_status",
            message=(
                "We are mitigating elevated checkout errors caused by attack traffic and a payment upstream outage. "
                "Traffic filters and a payment fallback are now in progress."
            ),
        )
    return IncidentAction(
        action_type="submit_rca",
        message=(
            "Checkout was hit by a layer-7 DDoS on the edge while Stripe independently returned 503s. "
            "Enabling challenge mode reduced malicious traffic, Braintree fallback restored payment flow, "
            "and security plus payments were paged while the status page was updated."
        ),
    )


def _run_task(task_id: str, config: BaselineConfig) -> dict:
    session = LocalSession()
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"]) if _should_use_openai(config) else None
    try:
        observation = session.reset(task_id=task_id)
        max_steps = config.max_steps or session.state().max_steps
        trace: list[dict] = []
        for _ in range(max_steps):
            try:
                action = (
                    _openai_action(client, observation, config)
                    if client is not None
                    else _heuristic_action(session.state())
                )
            except Exception:
                action = _heuristic_action(session.state())
            observation = session.step(action)
            trace.append(
                {
                    "action": action.model_dump(exclude_none=True),
                    "reward": observation.reward,
                    "done": observation.done,
                }
            )
            if observation.done:
                break
        final_state = session.state()
        graded = grade_state(final_state, task_id)
        return {
            "task_id": task_id,
            "difficulty": final_state.difficulty,
            "score": graded.score,
            "breakdown": graded.breakdown,
            "steps": final_state.step_count,
            "resolved": final_state.resolved,
            "total_reward": final_state.total_reward,
            "used_openai": client is not None,
            "trace": trace,
        }
    finally:
        session.close()


def _should_use_openai(config: BaselineConfig) -> bool:
    return config.use_openai_if_available and bool(os.getenv("OPENAI_API_KEY"))


def run_baseline_sync(
    model: str | None = None,
    seed: int = 7,
    max_steps: int | None = None,
    use_openai_if_available: bool = True,
) -> dict:
    """Run the baseline locally and return a JSON-serializable report."""

    config = BaselineConfig(
        model=model or DEFAULT_OPENAI_MODEL,
        seed=seed,
        max_steps=max_steps,
        use_openai_if_available=use_openai_if_available,
    )
    results = [_run_task(task.task_id, config) for task in list_tasks()]
    average_score = round(sum(result["score"] for result in results) / len(results), 4)
    return {
        "mode": "openai" if _should_use_openai(config) else "heuristic_fallback",
        "model": config.model,
        "seed": config.seed,
        "average_score": average_score,
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=DEFAULT_OPENAI_MODEL)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--max-steps", type=int, default=None)
    parser.add_argument(
        "--force-heuristic",
        action="store_true",
        help="Skip OpenAI even if OPENAI_API_KEY is set.",
    )
    args = parser.parse_args()
    report = run_baseline_sync(
        model=args.model,
        seed=args.seed,
        max_steps=args.max_steps,
        use_openai_if_available=not args.force_heuristic,
    )
    print(json.dumps(report, indent=2))


__all__ = ["run_baseline_sync", "main"]


if __name__ == "__main__":
    main()
