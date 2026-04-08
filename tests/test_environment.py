import incident_commander.baseline as baseline_module
from incident_commander.baseline import run_baseline_sync, run_demo_sync
from incident_commander.grading import grade_state
from incident_commander.models import IncidentAction
from server.environment import IncidentCommanderEnvironment


def test_reset_and_partial_progress_signal() -> None:
    env = IncidentCommanderEnvironment()
    observation = env.reset(task_id="cpu_spike")
    initial_score = observation.metadata["progress_score"]

    observation = env.step(
        IncidentAction(
            action_type="run_query",
            query="show deploy cpu search regression and n+1 evidence for api-gateway",
        )
    )
    assert observation.reward is not None
    assert observation.metadata["progress_score"] >= initial_score
    assert "deploy_regression" in env.state.investigation_finding_ids


def test_heuristic_baseline_solves_all_tasks() -> None:
    report = run_baseline_sync(use_openai_if_available=False)
    assert report["average_score"] >= 0.80
    assert len(report["results"]) == 4
    medium_result = next(result for result in report["results"] if result["task_id"] == "db_cascade")
    hard_result = next(result for result in report["results"] if result["task_id"] == "ddos_payment")
    novel_result = next(result for result in report["results"] if result["task_id"] == "runbook_failure")
    assert medium_result["score"] < 1.0
    assert hard_result["score"] < 1.0
    assert novel_result["score"] < 1.0
    for result in report["results"]:
        assert result["resolved"] is True
        assert 0.0 <= result["score"] <= 1.0


def test_demo_replay_captures_hard_task_timeline() -> None:
    report = run_demo_sync(task_id="ddos_payment", use_openai_if_available=False)
    replay = report["replay"]
    assert report["view"] == "war_room_replay"
    assert replay["task_id"] == "ddos_payment"
    assert replay["task"]["difficulty"] == "hard"
    assert replay["task"]["business_impact"]
    assert replay["initial_snapshot"]["critical_services"]
    assert replay["timeline"][-1]["snapshot"]["resolved"] is True


def test_step_budget_creates_time_pressure() -> None:
    env = IncidentCommanderEnvironment()
    env.reset(task_id="ddos_payment")

    for _ in range(4):
        env.step(IncidentAction(action_type="page_team", team="security"))

    services = {service.name: service for service in env.state.services}
    assert env.state.metrics["revenue_impact_per_min"] >= 18000.0
    assert services["cdn-edge"].status == "down"


def test_ddos_payment_rewards_correct_mitigation_order() -> None:
    def score_for(actions: list[IncidentAction]) -> float:
        env = IncidentCommanderEnvironment()
        env.reset(task_id="ddos_payment")
        for action in actions:
            env.step(action)
        assert env.state.resolved is True
        return grade_state(env.state).score

    common_tail = [
        IncidentAction(action_type="page_team", team="security"),
        IncidentAction(action_type="page_team", team="payments"),
        IncidentAction(
            action_type="post_status",
            message=(
                "Checkout is degraded because of attack traffic and a payment provider outage. "
                "We have edge mitigation and fallback work in progress."
            ),
        ),
        IncidentAction(
            action_type="submit_rca",
            message=(
                "Checkout was hit by DDoS traffic at the edge while Stripe returned 503s. "
                "Challenge mode reduced malicious traffic and Braintree fallback restored payment flow."
            ),
        ),
    ]

    correct_score = score_for(
        [
            IncidentAction(
                action_type="run_query",
                query="investigate ddos traffic stripe outage and braintree fallback",
            ),
            IncidentAction(
                action_type="toggle_feature",
                feature_flag="ddos_challenge_mode",
                enabled=True,
            ),
            IncidentAction(
                action_type="toggle_feature",
                feature_flag="payment_fallback_braintree",
                enabled=True,
            ),
            *common_tail,
        ]
    )
    wrong_order_score = score_for(
        [
            IncidentAction(
                action_type="run_query",
                query="investigate ddos traffic stripe outage and braintree fallback",
            ),
            IncidentAction(
                action_type="toggle_feature",
                feature_flag="payment_fallback_braintree",
                enabled=True,
            ),
            IncidentAction(
                action_type="toggle_feature",
                feature_flag="ddos_challenge_mode",
                enabled=True,
            ),
            *common_tail,
        ]
    )

    assert correct_score > wrong_order_score


def test_runbook_failure_rewards_rejecting_bad_runbook() -> None:
    def score_for(actions: list[IncidentAction]) -> float:
        env = IncidentCommanderEnvironment()
        env.reset(task_id="runbook_failure")
        for action in actions:
            env.step(action)
        return grade_state(env.state).score

    correct_score = score_for(
        [
            IncidentAction(
                action_type="run_query",
                query="investigate auth runbook replica lag fail-closed and primary reads",
            ),
            IncidentAction(
                action_type="toggle_feature",
                feature_flag="auth_reads_use_primary",
                enabled=True,
            ),
            IncidentAction(action_type="page_team", team="database"),
            IncidentAction(
                action_type="post_status",
                message=(
                    "Login traffic is degraded because auth is failing closed on replica lag. "
                    "We have enabled primary-read failover and are working replica recovery with the database team."
                ),
            ),
            IncidentAction(
                action_type="submit_rca",
                message=(
                    "The runbook was outdated. Restarting auth-service would have widened the outage because the service itself was healthy. "
                    "Replica lag tripped fail-closed logic, and primary-read failover restored logins safely."
                ),
            ),
        ]
    )
    wrong_score = score_for(
        [
            IncidentAction(action_type="restart_pod", service_name="auth-service"),
            IncidentAction(
                action_type="run_query",
                query="investigate auth runbook replica lag fail-closed and primary reads",
            ),
            IncidentAction(
                action_type="toggle_feature",
                feature_flag="auth_reads_use_primary",
                enabled=True,
            ),
            IncidentAction(
                action_type="submit_rca",
                message=(
                    "Replica lag was involved, but restarting auth-service first made the incident worse before failover was enabled."
                ),
            ),
        ]
    )

    assert correct_score > wrong_score


def test_openai_failures_are_reported_as_fallback(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(baseline_module, "OpenAI", lambda api_key, base_url=None: object())

    def fail_openai(*args, **kwargs):
        raise RuntimeError("synthetic openai failure")

    monkeypatch.setattr(baseline_module, "_openai_action", fail_openai)

    report = baseline_module.run_baseline_sync(strict_openai=False)

    assert report["mode"] == "openai_requested_but_fallback_only"
    for result in report["results"]:
        assert result["openai_requested"] is True
        assert result["used_openai"] is False
        assert result["openai_steps"] == 0
        assert result["fallback_steps"] == result["steps"]
        assert result["openai_errors"]
        assert all(trace["source"] == "heuristic_fallback" for trace in result["trace"])


def test_strict_openai_mode_raises_on_model_failure(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(baseline_module, "OpenAI", lambda api_key, base_url=None: object())

    def fail_openai(*args, **kwargs):
        raise RuntimeError("synthetic openai failure")

    monkeypatch.setattr(baseline_module, "_openai_action", fail_openai)

    try:
        baseline_module.run_baseline_sync(strict_openai=True)
    except RuntimeError as exc:
        assert "OpenAI baseline failed" in str(exc)
    else:
        raise AssertionError("strict_openai=True should fail on the first OpenAI error")


def test_openai_base_url_override_is_forwarded_to_client(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    captured: dict[str, str | None] = {}

    class DummyClient:
        def __init__(self, *, api_key: str, base_url: str | None = None) -> None:
            captured["api_key"] = api_key
            captured["base_url"] = base_url

    monkeypatch.setattr(baseline_module, "OpenAI", DummyClient)

    def fail_openai(*args, **kwargs):
        raise RuntimeError("synthetic openai failure")

    monkeypatch.setattr(baseline_module, "_openai_action", fail_openai)

    report = baseline_module.run_baseline_sync(
        base_url="https://example.test/v1",
        strict_openai=False,
    )

    assert captured["api_key"] == "test-key"
    assert captured["base_url"] == "https://example.test/v1"
    assert report["provider"] == "openai_compatible"
    assert report["base_url"] == "https://example.test/v1"
    assert report["mode"] == "openai_compatible_requested_but_fallback_only"
