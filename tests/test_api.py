from fastapi.testclient import TestClient

from incident_commander.models import IncidentAction
import server.app as app_module
from server.environment import IncidentCommanderEnvironment

app = app_module.app


def test_tasks_endpoint_lists_all_tasks() -> None:
    client = TestClient(app)
    response = client.get("/tasks")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["tasks"]) == 4
    assert payload["tasks"][0]["id"] == "cpu_spike"
    assert payload["tasks"][-1]["id"] == "runbook_failure"
    assert "business_impact" in payload["tasks"][0]
    assert "action_schema" in payload
    assert "state_schema" in payload


def test_demo_endpoint_returns_replay_timeline() -> None:
    client = TestClient(app)
    response = client.post(
        "/demo",
        json={
            "task_id": "ddos_payment",
            "use_openai_if_available": False,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["view"] == "war_room_replay"
    replay = payload["replay"]
    assert replay["task_id"] == "ddos_payment"
    assert replay["resolved"] is True
    assert replay["task"]["business_impact"]
    assert replay["timeline"]
    assert replay["timeline"][-1]["done"] is True


def test_http_reset_step_and_grader_flow() -> None:
    client = TestClient(app)

    reset_response = client.post("/reset", json={"task_id": "cpu_spike"})
    assert reset_response.status_code == 200
    assert reset_response.headers["x-session-id"]
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


def test_http_sessions_are_isolated_per_client() -> None:
    client_a = TestClient(app)
    client_b = TestClient(app)

    reset_a = client_a.post("/reset", json={"task_id": "cpu_spike"})
    reset_b = client_b.post("/reset", json={"task_id": "ddos_payment"})

    assert reset_a.status_code == 200
    assert reset_b.status_code == 200
    assert reset_a.headers["x-session-id"] != reset_b.headers["x-session-id"]

    step_a = client_a.post(
        "/step",
        json={
            "action": {
                "action_type": "rollback",
                "service_name": "api-gateway",
                "version": "v2.4.0",
            }
        },
    )
    assert step_a.status_code == 200

    state_a = client_a.get("/state").json()
    state_b = client_b.get("/state").json()

    assert state_a["task_id"] == "cpu_spike"
    assert state_a["step_count"] == 1
    assert state_b["task_id"] == "ddos_payment"
    assert state_b["step_count"] == 0


def test_baseline_endpoint_forwards_base_url(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_baseline_sync(**kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(app_module, "run_baseline_sync", fake_run_baseline_sync)
    client = TestClient(app)

    response = client.post(
        "/baseline",
        json={
            "base_url": "https://example.test/v1",
            "use_openai_if_available": False,
        },
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert captured["base_url"] == "https://example.test/v1"
