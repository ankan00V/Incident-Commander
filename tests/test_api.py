from fastapi.testclient import TestClient

from incident_commander.models import IncidentAction
from server.app import app
from server.environment import IncidentCommanderEnvironment


def test_tasks_endpoint_lists_three_tasks() -> None:
    client = TestClient(app)
    response = client.get("/tasks")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["tasks"]) == 3
    assert payload["tasks"][0]["id"] == "cpu_spike"
    assert "action_schema" in payload
    assert "state_schema" in payload


def test_http_reset_step_and_grader_flow() -> None:
    client = TestClient(app)

    reset_response = client.post("/reset", json={"task_id": "cpu_spike"})
    assert reset_response.status_code == 200
    reset_payload = reset_response.json()
    assert reset_payload["observation"]["task_id"] == "cpu_spike"

    step_response = client.post(
        "/step",
        json={
            "action": {
                "action_type": "rollback",
                "service_name": "api-gateway",
                "version": "v2.4.0",
            }
        },
    )
    assert step_response.status_code == 200
    step_payload = step_response.json()
    assert step_payload["reward"] > 0

    state_response = client.get("/state")
    assert state_response.status_code == 200
    state_payload = state_response.json()
    assert "episode_id" in state_payload
    assert state_payload["step_count"] == 1

    env = IncidentCommanderEnvironment()
    env.reset(task_id="cpu_spike")
    env.step(
        IncidentAction(
            action_type="run_query",
            query="show deploy cpu search regression and n+1 evidence for api-gateway",
        )
    )
    grader_response = client.post("/grader", json={"state": env.state.model_dump()})
    assert grader_response.status_code == 200
    grader_payload = grader_response.json()
    assert 0.0 <= grader_payload["score"] <= 1.0
    assert "breakdown" in grader_payload
