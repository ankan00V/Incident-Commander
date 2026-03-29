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
from .task_bank import get_task, list_tasks

DEFAULT_OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
DEFAULT_OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")
DEFAULT_DEMO_TASK = "ddos_payment"


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
    base_url: str | None = DEFAULT_OPENAI_BASE_URL
    seed: int = 7
    max_steps: int | None = None
    use_openai_if_available: bool = True
    strict_openai: bool = False


def _service_severity(status: str) -> int:
    return {"down": 2, "degraded": 1, "healthy": 0}.get(status, 0)


def _state_snapshot(state: IncidentState) -> dict:
    highlighted_services = sorted(
        state.services,
        key=lambda service: (
            _service_severity(service.status),
            service.error_rate,
            service.p99_latency_ms,
        ),
        reverse=True,
    )
    return {
        "step": state.step_count,
        "resolved": state.resolved,
        "affected_users": state.affected_users,
        "progress_score": state.current_progress_score,
        "active_alerts": list(state.active_alerts),
        "metrics": dict(state.metrics),
        "critical_services": [
            {
                "name": service.name,
                "status": service.status,
                "error_rate": service.error_rate,
                "p99_latency_ms": service.p99_latency_ms,
                "replicas": service.replicas,
            }
            for service in highlighted_services
            if service.status != "healthy"
        ][:4],
        "feature_flags": dict(state.feature_flags),
        "paged_teams": list(state.paged_teams),
        "status_updates": list(state.status_updates),
        "investigation_findings": list(state.investigation_findings),
        "last_action_result": state.last_action_result,
    }


def _judge_takeaway(task_id: str) -> str:
    return {
        "cpu_spike": (
            "This task rewards fast fault isolation instead of noisy mitigation. "
            "Scaling buys time, but only a correct rollback actually resolves the outage."
        ),
        "db_cascade": (
            "This task forces multi-step systems thinking: the agent must connect leaked workers, "
            "cache misses, and primary saturation rather than treating login failures as a single-service bug."
        ),
        "ddos_payment": (
            "This task is the strongest showcase because it combines security response, payment failover, "
            "cross-team coordination, and customer communication inside one revenue-critical incident."
        ),
    }[task_id]


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
        max_tokens=400,
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
    return IncidentAction(
        action_type="submit_rca",
        message=(
            "Checkout was hit by a layer-7 DDoS on the edge while Stripe independently returned 503s. "
            "Enabling challenge mode reduced malicious traffic, Braintree fallback restored payment flow, "
            "and security plus payments were paged while the status page was updated."
        ),
    )


def _format_openai_error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


def _provider_name(config: BaselineConfig) -> str:
    return "openai_compatible" if config.base_url else "openai"


def _baseline_mode(config: BaselineConfig, results: list[dict] | None = None) -> str:
    if not _should_use_openai(config):
        return "heuristic_fallback"
    if not results:
        return f"{_provider_name(config)}_requested"

    openai_steps = sum(int(result.get("openai_steps", 0)) for result in results)
    fallback_steps = sum(int(result.get("fallback_steps", 0)) for result in results)

    if openai_steps > 0 and fallback_steps == 0:
        return _provider_name(config)
    if openai_steps > 0 and fallback_steps > 0:
        return f"{_provider_name(config)}_with_fallback"
    return f"{_provider_name(config)}_requested_but_fallback_only"


def _create_openai_client(config: BaselineConfig) -> OpenAI | None:
    if not _should_use_openai(config):
        return None

    client_kwargs = {"api_key": os.environ["OPENAI_API_KEY"]}
    if config.base_url:
        client_kwargs["base_url"] = config.base_url
    return OpenAI(**client_kwargs)


def _run_task(task_id: str, config: BaselineConfig, include_replay: bool = False) -> dict:
    session = LocalSession()
    client = _create_openai_client(config)
    task = get_task(task_id)
    try:
        observation = session.reset(task_id=task_id)
        max_steps = config.max_steps or session.state().max_steps
        trace: list[dict] = []
        initial_snapshot = _state_snapshot(session.state()) if include_replay else None
        timeline: list[dict] = []
        openai_steps = 0
        fallback_steps = 0
        openai_errors: list[str] = []
        for _ in range(max_steps):
            action_source = "heuristic"
            try:
                if client is not None:
                    action = _openai_action(client, observation, config)
                    openai_steps += 1
                    action_source = "openai"
                else:
                    action = _heuristic_action(session.state())
                    fallback_steps += 1
            except Exception as exc:
                if config.strict_openai and client is not None:
                    raise RuntimeError(
                        f"OpenAI baseline failed for task '{task_id}' at step {session.state().step_count + 1}: "
                        f"{_format_openai_error(exc)}"
                    ) from exc
                openai_errors.append(_format_openai_error(exc))
                action = _heuristic_action(session.state())
                fallback_steps += 1
                action_source = "heuristic_fallback"
            observation = session.step(action)
            trace.append(
                {
                    "action": action.model_dump(exclude_none=True),
                    "source": action_source,
                    "reward": observation.reward,
                    "done": observation.done,
                }
            )
            if include_replay:
                timeline.append(
                    {
                        "step": session.state().step_count,
                        "action": action.model_dump(exclude_none=True),
                        "outcome": observation.last_action_result,
                        "source": action_source,
                        "reward": observation.reward,
                        "done": observation.done,
                        "progress_score": observation.metadata["progress_score"],
                        "score_breakdown": observation.metadata["score_breakdown"],
                        "snapshot": _state_snapshot(session.state()),
                    }
                )
            if observation.done:
                break
        final_state = session.state()
        graded = grade_state(final_state, task_id)
        result = {
            "task_id": task_id,
            "difficulty": final_state.difficulty,
            "score": graded.score,
            "breakdown": graded.breakdown,
            "steps": final_state.step_count,
            "resolved": final_state.resolved,
            "total_reward": final_state.total_reward,
            "openai_requested": client is not None,
            "used_openai": openai_steps > 0,
            "openai_steps": openai_steps,
            "fallback_steps": fallback_steps,
            "openai_errors": openai_errors,
            "trace": trace,
        }
        if include_replay:
            result.update(
                {
                    "task": {
                        "task_id": task.task_id,
                        "difficulty": task.difficulty,
                        "title": task.title,
                        "objective": task.objective,
                        "business_impact": task.business_impact,
                        "affected_users": task.affected_users,
                    },
                    "judge_takeaway": _judge_takeaway(task.task_id),
                    "initial_snapshot": initial_snapshot,
                    "timeline": timeline,
                    "final_snapshot": _state_snapshot(final_state),
                }
            )
        return result
    finally:
        session.close()


def _should_use_openai(config: BaselineConfig) -> bool:
    return config.use_openai_if_available and bool(os.getenv("OPENAI_API_KEY"))


def run_baseline_sync(
    model: str | None = None,
    base_url: str | None = None,
    seed: int = 7,
    max_steps: int | None = None,
    use_openai_if_available: bool = True,
    strict_openai: bool = False,
) -> dict:
    """Run the baseline locally and return a JSON-serializable report."""

    config = BaselineConfig(
        model=model or DEFAULT_OPENAI_MODEL,
        base_url=base_url if base_url is not None else DEFAULT_OPENAI_BASE_URL,
        seed=seed,
        max_steps=max_steps,
        use_openai_if_available=use_openai_if_available,
        strict_openai=strict_openai,
    )
    results = [_run_task(task.task_id, config) for task in list_tasks()]
    average_score = round(sum(result["score"] for result in results) / len(results), 4)
    return {
        "mode": _baseline_mode(config, results),
        "provider": _provider_name(config),
        "model": config.model,
        "base_url": config.base_url,
        "seed": config.seed,
        "strict_openai": config.strict_openai,
        "average_score": average_score,
        "results": results,
    }


def run_demo_sync(
    task_id: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    seed: int = 7,
    max_steps: int | None = None,
    use_openai_if_available: bool = True,
    include_all_tasks: bool = False,
    strict_openai: bool = False,
) -> dict:
    """Run a replay-friendly incident demo for judges and local walkthroughs."""

    config = BaselineConfig(
        model=model or DEFAULT_OPENAI_MODEL,
        base_url=base_url if base_url is not None else DEFAULT_OPENAI_BASE_URL,
        seed=seed,
        max_steps=max_steps,
        use_openai_if_available=use_openai_if_available,
        strict_openai=strict_openai,
    )
    if include_all_tasks:
        replays = [_run_task(task.task_id, config, include_replay=True) for task in list_tasks()]
        return {
            "mode": _baseline_mode(config, replays),
            "provider": _provider_name(config),
            "model": config.model,
            "base_url": config.base_url,
            "seed": config.seed,
            "strict_openai": config.strict_openai,
            "view": "judge_showcase",
            "scoreboard": [
                {
                    "task_id": replay["task_id"],
                    "difficulty": replay["difficulty"],
                    "score": replay["score"],
                    "resolved": replay["resolved"],
                    "steps": replay["steps"],
                }
                for replay in replays
            ],
            "featured_task": DEFAULT_DEMO_TASK,
            "replays": replays,
        }

    selected_task = get_task(task_id or DEFAULT_DEMO_TASK).task_id
    replay = _run_task(selected_task, config, include_replay=True)
    return {
        "mode": _baseline_mode(config, [replay]),
        "provider": _provider_name(config),
        "model": config.model,
        "base_url": config.base_url,
        "seed": config.seed,
        "strict_openai": config.strict_openai,
        "view": "war_room_replay",
        "replay": replay,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=DEFAULT_OPENAI_MODEL)
    parser.add_argument(
        "--base-url",
        default=DEFAULT_OPENAI_BASE_URL,
        help="Optional OpenAI-compatible API base URL override. Defaults to OPENAI_BASE_URL if set.",
    )
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--max-steps", type=int, default=None)
    parser.add_argument(
        "--task-id",
        default=None,
        choices=[task.task_id for task in list_tasks()],
        help="Optional task to target for demo mode.",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run a replay-friendly demo instead of the compact baseline report.",
    )
    parser.add_argument(
        "--all-tasks",
        action="store_true",
        help="In demo mode, include replays for all tasks instead of only the featured task.",
    )
    parser.add_argument(
        "--force-heuristic",
        action="store_true",
        help="Skip OpenAI even if OPENAI_API_KEY is set.",
    )
    parser.add_argument(
        "--strict-openai",
        action="store_true",
        help="Fail the run if any OpenAI step errors instead of silently falling back.",
    )
    args = parser.parse_args()
    if args.demo:
        report = run_demo_sync(
            task_id=args.task_id,
            model=args.model,
            base_url=args.base_url,
            seed=args.seed,
            max_steps=args.max_steps,
            use_openai_if_available=not args.force_heuristic,
            include_all_tasks=args.all_tasks,
            strict_openai=args.strict_openai,
        )
    else:
        report = run_baseline_sync(
            model=args.model,
            base_url=args.base_url,
            seed=args.seed,
            max_steps=args.max_steps,
            use_openai_if_available=not args.force_heuristic,
            strict_openai=args.strict_openai,
        )
    print(json.dumps(report, indent=2))


__all__ = ["run_baseline_sync", "run_demo_sync", "main"]


if __name__ == "__main__":
    main()
