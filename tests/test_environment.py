from incident_commander.baseline import run_baseline_sync
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
    assert report["average_score"] >= 0.99
    assert len(report["results"]) == 3
    for result in report["results"]:
        assert result["resolved"] is True
        assert 0.0 <= result["score"] <= 1.0
