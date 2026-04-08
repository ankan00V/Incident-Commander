from __future__ import annotations

import inference


def test_load_config_requires_submission_env_vars() -> None:
    try:
        inference.load_config({})
    except ValueError as exc:
        assert "HF_TOKEN" in str(exc)
    else:
        raise AssertionError("load_config should fail when HF_TOKEN is missing")


def test_resolve_env_url_prefers_explicit_env_url() -> None:
    resolved = inference.resolve_env_url(
        {
            "ENV_URL": "https://example.test/custom/",
            "SPACE_HOST": "ignored.example",
            "PORT": "9999",
        }
    )
    assert resolved == "https://example.test/custom"


def test_run_episode_emits_required_log_lines() -> None:
    class FakeToolCall:
        def __init__(self, arguments: str) -> None:
            self.function = type("Function", (), {"arguments": arguments})()

    class FakeMessage:
        def __init__(self, arguments: str) -> None:
            self.tool_calls = [FakeToolCall(arguments)]
            self.content = None

    class FakeChoice:
        def __init__(self, arguments: str) -> None:
            self.message = FakeMessage(arguments)

    class FakeCompletions:
        def create(self, **kwargs):
            return type("Completion", (), {"choices": [FakeChoice('{"action_type":"run_query","query":"show cpu"}')]})()

    class FakeClient:
        def __init__(self) -> None:
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    class FakeResponse:
        def __init__(self, payload, headers=None) -> None:
            self._payload = payload
            self.headers = headers or {}

        def json(self):
            return self._payload

        def raise_for_status(self) -> None:
            return None

    class FakeHttpClient:
        def __init__(self) -> None:
            self.calls = []

        def post(self, url: str, json=None, headers=None):
            self.calls.append(("POST", url, json, headers))
            if url.endswith("/reset"):
                return FakeResponse(
                    {
                        "observation": {
                            "task_id": "cpu_spike",
                            "difficulty": "easy",
                            "title": "CPU spike",
                            "objective": "Rollback safely",
                            "step_count": 0,
                            "steps_remaining": 3,
                            "resolved": False,
                            "last_action_result": "Episode ready.",
                            "services": [],
                            "metrics": {},
                            "active_alerts": [],
                            "recent_logs": [],
                            "investigation_findings": [],
                            "feature_flags": {},
                            "paged_teams": [],
                            "status_updates": [],
                            "progress": [],
                        }
                    },
                    headers={"X-Session-Id": "session-1"},
                )
            if url.endswith("/step"):
                return FakeResponse(
                    {
                        "observation": {
                            "task_id": "cpu_spike",
                            "difficulty": "easy",
                            "title": "CPU spike",
                            "objective": "Rollback safely",
                            "step_count": 1,
                            "steps_remaining": 2,
                            "resolved": True,
                            "last_action_result": "Query confirmed deploy regression.",
                            "services": [],
                            "metrics": {},
                            "active_alerts": [],
                            "recent_logs": [],
                            "investigation_findings": ["deploy_regression"],
                            "feature_flags": {},
                            "paged_teams": [],
                            "status_updates": [],
                            "progress": [],
                        },
                        "reward": 0.42,
                        "done": True,
                    },
                    headers={"X-Session-Id": "session-1"},
                )
            if url.endswith("/grader"):
                return FakeResponse({"score": 0.84})
            raise AssertionError(f"unexpected POST {url}")

        def get(self, url: str, headers=None):
            self.calls.append(("GET", url, None, headers))
            if url.endswith("/state"):
                return FakeResponse({"resolved": True})
            raise AssertionError(f"unexpected GET {url}")

    config = inference.InferenceConfig(
        api_base_url="https://llm.example/v1",
        model_name="example-model",
        api_key="secret",
        env_url="https://env.example",
    )
    result = inference.run_episode(
        client=FakeClient(),
        http_client=FakeHttpClient(),
        task={"task_id": "cpu_spike", "id": "cpu_spike"},
        config=config,
    )

    assert result["score"] == 0.84
    assert result["success"] is True
    assert result["steps"] == 1


def test_ground_action_canonicalizes_near_miss_literals() -> None:
    observation = {
        "task_id": "ddos_payment",
        "services": [
            {"name": "cdn-edge"},
            {"name": "payment-service"},
            {"name": "order-service"},
        ],
    }
    grounded = inference._ground_action(
        observation,
        {
            "action_type": "toggle_feature",
            "feature_flag": "braintree_fallback",
            "team": "payment-engineers",
            "service_name": "Payment-Service",
            "enabled": True,
        },
        history=[],
    )

    assert grounded["feature_flag"] == "payment_fallback_braintree"
    assert grounded["team"] == "payments"
    assert grounded["service_name"] == "payment-service"


def test_ground_action_rewrites_low_signal_query_to_preferred_template() -> None:
    observation = {
        "task_id": "runbook_failure",
        "services": [{"name": "auth-service"}],
    }
    grounded = inference._ground_action(
        observation,
        {
            "action_type": "run_query",
            "query": "login 5xx error rate over the last 5 minutes",
        },
        history=[],
    )

    assert grounded["query"] == "investigate auth runbook replica lag fail-closed and primary reads"


def test_ground_action_fills_missing_toggle_flag_from_pending_task_state() -> None:
    observation = {
        "task_id": "ddos_payment",
        "feature_flags": {"ddos_challenge_mode": True},
        "paged_teams": [],
        "services": [],
    }
    grounded = inference._ground_action(
        observation,
        {
            "action_type": "toggle_feature",
            "enabled": True,
        },
        history=[],
    )

    assert grounded["feature_flag"] == "payment_fallback_braintree"
    assert grounded["enabled"] is True


def test_ground_action_fills_missing_team_and_scale_defaults() -> None:
    observation = {
        "task_id": "db_cascade",
        "feature_flags": {},
        "paged_teams": [],
        "services": [{"name": "db-primary"}],
    }
    page_grounded = inference._ground_action(
        observation,
        {"action_type": "page_team"},
        history=[],
    )
    scale_grounded = inference._ground_action(
        observation,
        {"action_type": "scale_service"},
        history=[],
    )

    assert page_grounded["team"] == "database"
    assert scale_grounded["service_name"] == "db-primary"
    assert scale_grounded["replicas"] == 2


def test_request_action_retries_once_on_rate_limit(monkeypatch) -> None:
    calls = {"count": 0}

    class FakeRateLimitError(Exception):
        pass

    class FakeToolCall:
        def __init__(self, arguments: str) -> None:
            self.function = type("Function", (), {"arguments": arguments})()

    class FakeMessage:
        def __init__(self, arguments: str) -> None:
            self.tool_calls = [FakeToolCall(arguments)]
            self.content = None

    class FakeChoice:
        def __init__(self, arguments: str) -> None:
            self.message = FakeMessage(arguments)

    class FakeCompletions:
        def create(self, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                raise FakeRateLimitError("rate limited")
            return type("Completion", (), {"choices": [FakeChoice('{"action_type":"run_query","query":"show deploy cpu search regression and n+1 evidence for api-gateway"}')]})()

    class FakeClient:
        def __init__(self) -> None:
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr(inference, "RateLimitError", FakeRateLimitError)
    monkeypatch.setattr(inference.time, "sleep", lambda seconds: None)

    action = inference.request_action(
        client=FakeClient(),
        observation={
            "task_id": "cpu_spike",
            "services": [{"name": "api-gateway"}],
            "feature_flags": {},
            "paged_teams": [],
            "status_updates": [],
            "investigation_findings": [],
            "step_count": 0,
            "steps_remaining": 10,
        },
        config=inference.InferenceConfig(
            api_base_url="https://llm.example/v1",
            model_name="example-model",
            api_key="secret",
            env_url="https://env.example",
        ),
        history=[],
    )

    assert calls["count"] == 2
    assert action["action_type"] == "run_query"


def test_request_action_recovers_from_malformed_partial_json() -> None:
    class FakeToolCall:
        def __init__(self, arguments: str) -> None:
            self.function = type("Function", (), {"arguments": arguments})()

    class FakeMessage:
        def __init__(self, arguments: str) -> None:
            self.tool_calls = [FakeToolCall(arguments)]
            self.content = None

    class FakeChoice:
        def __init__(self, arguments: str) -> None:
            self.message = FakeMessage(arguments)

    class FakeCompletions:
        def create(self, **kwargs):
            return type(
                "Completion",
                (),
                {"choices": [FakeChoice('{"action_type":"page_team","team":"pay')]},
            )()

    class FakeClient:
        def __init__(self) -> None:
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    action = inference.request_action(
        client=FakeClient(),
        observation={
            "task_id": "ddos_payment",
            "services": [],
            "feature_flags": {
                "ddos_challenge_mode": True,
                "payment_fallback_braintree": True,
            },
            "paged_teams": ["security"],
            "status_updates": [],
            "investigation_findings": [
                "edge_ddos_signature",
                "stripe_upstream_outage",
            ],
            "step_count": 4,
            "steps_remaining": 12,
        },
        config=inference.InferenceConfig(
            api_base_url="https://llm.example/v1",
            model_name="example-model",
            api_key="secret",
            env_url="https://env.example",
        ),
        history=[],
    )

    assert action["action_type"] == "page_team"
    assert action["team"] == "payments"
