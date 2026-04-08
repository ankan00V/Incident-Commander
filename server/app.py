"""FastAPI app for the Incident Commander OpenEnv environment."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from threading import Lock
from time import time
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

from incident_commander.baseline import run_baseline_sync, run_demo_sync
from incident_commander.grading import grade_state_payload, grader_result_to_dict
from incident_commander.models import IncidentAction, IncidentObservation, IncidentState
from incident_commander.task_bank import list_tasks
from openenv.core.env_server import create_fastapi_app
from openenv.core.env_server.serialization import serialize_observation
from openenv.core.env_server.types import ResetRequest, ResetResponse, StepRequest, StepResponse
from server.environment import IncidentCommanderEnvironment

_SESSION_COOKIE = "openenv_session_id"
_MAX_HTTP_SESSIONS = 64


@dataclass
class HTTPSession:
    env: IncidentCommanderEnvironment
    lock: Lock = field(default_factory=Lock)
    last_touched_at: float = field(default_factory=time)


_HTTP_SESSIONS: OrderedDict[str, HTTPSession] = OrderedDict()
_HTTP_SESSION_LOCK = Lock()


def get_env() -> IncidentCommanderEnvironment:
    """Return a fresh environment for each OpenEnv-managed WebSocket session."""

    return IncidentCommanderEnvironment()


def _drop_route(path: str) -> None:
    app.router.routes = [
        route
        for route in app.router.routes
        if not (isinstance(route, APIRoute) and route.path == path)
    ]
    app.openapi_schema = None


def _evict_http_session(session_id: str) -> None:
    session = _HTTP_SESSIONS.pop(session_id, None)
    if session is not None:
        session.env.close()


def _touch_http_session(session_id: str) -> HTTPSession:
    with _HTTP_SESSION_LOCK:
        session = _HTTP_SESSIONS.get(session_id)
        if session is None:
            raise HTTPException(
                status_code=400,
                detail="Unknown or expired session. Call /reset to start a new episode.",
            )
        session.last_touched_at = time()
        _HTTP_SESSIONS.move_to_end(session_id)
        return session


def _extract_session_id(request: Request, x_session_id: str | None) -> str | None:
    return x_session_id or request.cookies.get(_SESSION_COOKIE)


def _create_or_replace_http_session(session_id: str | None = None) -> tuple[str, HTTPSession]:
    with _HTTP_SESSION_LOCK:
        resolved_session_id = session_id or uuid4().hex
        if resolved_session_id in _HTTP_SESSIONS:
            _evict_http_session(resolved_session_id)
        elif len(_HTTP_SESSIONS) >= _MAX_HTTP_SESSIONS:
            oldest_session_id = next(iter(_HTTP_SESSIONS))
            _evict_http_session(oldest_session_id)
        session = HTTPSession(env=IncidentCommanderEnvironment())
        _HTTP_SESSIONS[resolved_session_id] = session
        return resolved_session_id, session


app: FastAPI = create_fastapi_app(
    get_env,
    IncidentAction,
    IncidentObservation,
    max_concurrent_envs=16,
)
app.title = "Incident Commander OpenEnv API"
app.description = "OpenEnv environment for deterministic SRE incident-response tasks."


class GradeRequest(BaseModel):
    task_id: str | None = Field(default=None, description="Optional explicit task id")
    state: dict[str, Any] = Field(..., description="Serialized IncidentState payload")


class BaselineRequest(BaseModel):
    model: str | None = Field(default=None, description="OpenAI model override")
    base_url: str | None = Field(
        default=None,
        description="Optional OpenAI-compatible API base URL override. Falls back to OPENAI_BASE_URL when unset.",
    )
    seed: int = Field(default=7, description="Sampling seed for chat completions")
    max_steps: int | None = Field(
        default=None, description="Optional max steps override for each task"
    )
    use_openai_if_available: bool = Field(
        default=True,
        description="Use OpenAI when OPENAI_API_KEY is set, otherwise use the deterministic heuristic policy.",
    )
    strict_openai: bool = Field(
        default=False,
        description="Fail if any OpenAI step errors instead of silently falling back to the heuristic policy.",
    )


class DemoRequest(BaseModel):
    task_id: str | None = Field(
        default=None,
        description="Optional task id for a single-task replay. Defaults to the featured hard task.",
    )
    model: str | None = Field(default=None, description="OpenAI model override")
    base_url: str | None = Field(
        default=None,
        description="Optional OpenAI-compatible API base URL override. Falls back to OPENAI_BASE_URL when unset.",
    )
    seed: int = Field(default=7, description="Sampling seed for chat completions")
    max_steps: int | None = Field(
        default=None, description="Optional max steps override for each task"
    )
    include_all_tasks: bool = Field(
        default=False,
        description="Include replay timelines for all tasks instead of only the featured task.",
    )
    use_openai_if_available: bool = Field(
        default=True,
        description="Use OpenAI when OPENAI_API_KEY is set, otherwise use the deterministic heuristic policy.",
    )
    strict_openai: bool = Field(
        default=False,
        description="Fail if any OpenAI step errors instead of silently falling back to the heuristic policy.",
    )


_drop_route("/reset")
_drop_route("/step")
_drop_route("/state")


@app.get("/")
def root() -> dict[str, str]:
    """Simple root endpoint for container and manual checks."""

    return {"status": "healthy", "env": "incident_commander"}


@app.get("/app", include_in_schema=False)
@app.get("/app/", include_in_schema=False)
@app.get("/app/{_rest:path}", include_in_schema=False)
def huggingface_app_path(_rest: str = "") -> RedirectResponse:
    """Compatibility route for Spaces App tab path handling."""

    return RedirectResponse(url="/docs", status_code=307)


@app.get("/web", include_in_schema=False)
@app.get("/web/", include_in_schema=False)
@app.get("/web/{_rest:path}", include_in_schema=False)
def huggingface_web_path(_rest: str = "") -> RedirectResponse:
    """Compatibility route for Spaces embed base_path=/web."""

    return RedirectResponse(url="/docs", status_code=307)


def _reset_session(
    request: ResetRequest | None,
    response: Response,
    http_request: Request,
    x_session_id: str | None,
) -> ResetResponse:
    """Shared reset implementation for root and compatibility routes."""

    session_hint = _extract_session_id(http_request, x_session_id)
    session_id, session = _create_or_replace_http_session(session_hint)
    reset_payload = request or ResetRequest()
    reset_kwargs = reset_payload.model_dump(exclude_unset=True)
    with session.lock:
        observation = session.env.reset(**reset_kwargs)
    serialized = serialize_observation(observation)
    response.set_cookie(_SESSION_COOKIE, session_id, httponly=True, samesite="lax")
    response.headers["X-Session-Id"] = session_id
    return ResetResponse(**serialized)


@app.post("/reset", response_model=ResetResponse)
def reset_endpoint(
    response: Response,
    http_request: Request,
    request: ResetRequest | None = None,
    x_session_id: str | None = Header(default=None),
) -> ResetResponse:
    """Reset an isolated HTTP episode and persist its session via cookie or header."""

    return _reset_session(request, response, http_request, x_session_id)


@app.post("/web/reset", include_in_schema=False, response_model=ResetResponse)
@app.post("/app/reset", include_in_schema=False, response_model=ResetResponse)
@app.post("/spaces/{owner}/{space}/reset", include_in_schema=False, response_model=ResetResponse)
def reset_compat_endpoint(
    response: Response,
    http_request: Request,
    request: ResetRequest | None = None,
    owner: str | None = None,
    space: str | None = None,
    x_session_id: str | None = Header(default=None),
) -> ResetResponse:
    """Compatibility aliases for external checkers that append /reset to varied base paths."""

    _ = (owner, space)
    return _reset_session(request, response, http_request, x_session_id)


@app.post("/step", response_model=StepResponse)
def step_endpoint(
    request: StepRequest,
    response: Response,
    http_request: Request,
    x_session_id: str | None = Header(default=None),
) -> StepResponse:
    """Execute one action inside the caller's isolated HTTP session."""

    session_id = _extract_session_id(http_request, x_session_id)
    if not session_id:
        raise HTTPException(status_code=400, detail="Missing session. Call /reset first.")
    session = _touch_http_session(session_id)
    action = IncidentAction.model_validate(request.action)
    step_kwargs = request.model_dump(exclude_unset=True, exclude={"action"})
    with session.lock:
        observation = session.env.step(action, **step_kwargs)
    serialized = serialize_observation(observation)
    response.set_cookie(_SESSION_COOKIE, session_id, httponly=True, samesite="lax")
    response.headers["X-Session-Id"] = session_id
    return StepResponse(**serialized)


@app.get("/state", response_model=IncidentState)
def state_endpoint(
    response: Response,
    http_request: Request,
    x_session_id: str | None = Header(default=None),
) -> IncidentState:
    """Return the current HTTP session state."""

    session_id = _extract_session_id(http_request, x_session_id)
    if not session_id:
        raise HTTPException(status_code=400, detail="Missing session. Call /reset first.")
    session = _touch_http_session(session_id)
    response.headers["X-Session-Id"] = session_id
    with session.lock:
        return session.env.state.model_copy(deep=True)


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
            "business_impact": task.business_impact,
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
        base_url=request.base_url,
        seed=request.seed,
        max_steps=request.max_steps,
        use_openai_if_available=request.use_openai_if_available,
        strict_openai=request.strict_openai,
    )


@app.post("/demo")
def demo_endpoint(request: DemoRequest) -> dict[str, Any]:
    """Run a replay-friendly war-room demo for judges and local walkthroughs."""

    return run_demo_sync(
        task_id=request.task_id,
        model=request.model,
        base_url=request.base_url,
        seed=request.seed,
        max_steps=request.max_steps,
        use_openai_if_available=request.use_openai_if_available,
        include_all_tasks=request.include_all_tasks,
        strict_openai=request.strict_openai,
    )


def main(host: str = "0.0.0.0", port: int = 8000) -> None:
    import uvicorn

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
