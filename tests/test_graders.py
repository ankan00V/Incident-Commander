"""Deterministic grader tests."""

from server.graders import grade


def test_easy_zero_score_on_no_actions() -> None:
    score = grade("cpu_spike", actions=[], resolved=False, elapsed_seconds=0)
    assert score == 0.0


def test_easy_perfect_score() -> None:
    actions = [
        {
            "action_type": "run_query",
            "params": {"query": "cpu deploy n+1 query"},
            "reward": 0.05,
        },
        {
            "action_type": "rollback",
            "params": {"service_name": "api-gateway", "version": "v2.4.0"},
            "reward": 0.9,
        },
        {
            "action_type": "submit_rca",
            "params": {
                "message": (
                    "bad deploy v2.4.1 introduced n+1 query causing cpu spike, rolled back"
                )
            },
            "reward": 0.85,
        },
    ]
    score = grade(
        "cpu_spike",
        actions=actions,
        resolved=True,
        elapsed_seconds=45,
        rca_text="bad deploy v2.4.1 introduced n+1 query causing cpu spike, rolled back",
    )
    assert score >= 0.85


def test_hard_penalizes_destructive_actions() -> None:
    actions = [
        {
            "action_type": "restart_pod",
            "params": {"service_name": "order-service"},
            "reward": -0.25,
        },
        {
            "action_type": "restart_pod",
            "params": {"service_name": "db-primary"},
            "reward": -0.25,
        },
    ]
    score = grade(
        "ddos_payment",
        actions=actions,
        resolved=False,
        elapsed_seconds=60,
    )
    assert score < 0.2


def test_score_always_in_range() -> None:
    for task_id in ["cpu_spike", "db_cascade", "ddos_payment"]:
        for resolved in [True, False]:
            score = grade(task_id, actions=[], resolved=resolved, elapsed_seconds=999)
            assert 0.0 <= score <= 1.0


def test_medium_feature_flag_rewarded() -> None:
    actions = [
        {
            "action_type": "restart_pod",
            "params": {"service_name": "session-worker"},
            "reward": 0.5,
        },
        {
            "action_type": "toggle_feature",
            "params": {"feature_flag": "read_replica_routing", "enabled": True},
            "reward": 0.4,
        },
        {
            "action_type": "submit_rca",
            "params": {
                "message": "connection pool exhausted due to session-worker leak"
            },
            "reward": 0.8,
        },
    ]
    score = grade(
        "db_cascade",
        actions=actions,
        resolved=True,
        elapsed_seconds=90,
        rca_text="connection pool exhausted due to session-worker leak",
    )
    assert score >= 0.70
