"""FastAPI app for the Incident Commander OpenEnv environment."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

from incident_commander.baseline import run_baseline_sync
from incident_commander.grading import grade_state_payload, grader_result_to_dict
from incident_commander.models import IncidentAction, IncidentObservation, IncidentState
from incident_commander.task_bank import list_tasks
from server.environment import IncidentCommanderEnvironment
from openenv.core.env_server import create_fastapi_app

_ENV = IncidentCommanderEnvironment()


def get_env() -> IncidentCommanderEnvironment:
    """Return the singleton environment used by HTTP and WebSocket sessions."""

    return _ENV


app: FastAPI = create_fastapi_app(
    get_env,
    IncidentAction,
    IncidentObservation,
    max_concurrent_envs=1,
)
app.title = "Incident Commander OpenEnv API"
app.description = "OpenEnv environment for deterministic SRE incident-response tasks."


class GradeRequest(BaseModel):
    task_id: str | None = Field(default=None, description="Optional explicit task id")
    state: dict[str, Any] = Field(..., description="Serialized IncidentState payload")


class BaselineRequest(BaseModel):
    model: str | None = Field(default=None, description="OpenAI model override")
    seed: int = Field(default=7, description="Sampling seed for chat completions")
    max_steps: int | None = Field(
        default=None, description="Optional max steps override for each task"
    )
    use_openai_if_available: bool = Field(
        default=True,
        description="Use OpenAI when OPENAI_API_KEY is set, otherwise use the deterministic heuristic policy.",
    )


@app.get("/")
def root() -> dict[str, str]:
    """Simple root endpoint for container and manual checks."""

    return {"status": "healthy", "env": "incident_commander"}


@app.get("/tasks")
def tasks_endpoint() -> dict[str, Any]:
    """Enumerate deterministic tasks plus the action and state schema."""

    tasks = [
        {
            "id": task.task_id,
            "task_id": task.task_id,
            "difficulty": task.difficulty,
            "title": task.title,
            "objective": task.objective,
            "description": task.description,
            "affected_users": task.affected_users,
            "max_steps": task.max_steps,
        }
        for task in list_tasks()
    ]
    return {
        "tasks": tasks,
        "action_schema": IncidentAction.model_json_schema(),
        "state_schema": IncidentState.model_json_schema(),
    }


@app.post("/grader")
def grader_endpoint(request: GradeRequest) -> dict[str, Any]:
    """Grade a completed episode from its serialized state."""

    result = grade_state_payload(request.state, request.task_id)
    return grader_result_to_dict(result)


@app.post("/baseline")
def baseline_endpoint(request: BaselineRequest) -> dict[str, Any]:
    """Run the baseline policy locally and return a JSON report."""

    return run_baseline_sync(
        model=request.model,
        seed=request.seed,
        max_steps=request.max_steps,
        use_openai_if_available=request.use_openai_if_available,
    )


def main(host: str = "0.0.0.0", port: int = 8000) -> None:
    import uvicorn

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
