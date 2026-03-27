---
title: Incident Commander
emoji: 🚨
colorFrom: red
colorTo: yellow
sdk: docker
pinned: false
app_port: 8000
tags:
  - openenv
  - incident-response
  - sre
  - agents
  - reinforcement-learning
---

# Incident Commander

Production incident response for OpenEnv. An agent operates as the incident commander during live outages, balancing diagnosis, mitigation, coordination, customer communication, and post-incident RCA.

`incident_commander` is built directly against the Round 1 statement:

> Build a complete, real-world OpenEnv environment that an AI agent can learn from through the standard `step()` / `reset()` / `state()` API.

The agent must interpret telemetry, choose operational mitigations, page the correct teams, communicate externally when needed, and submit an RCA at the end of the episode.

This is not a toy workflow. It models a genuine human job performed by SRE, platform, and incident-management teams under time pressure, with explicit penalties for invalid or destructive actions.

## Why This Submission Is Strong

- real-world utility: production incident response is a concrete workflow teams already perform
- meaningful learning signal: reward is dense, trajectory-shaped, and aligned to partial progress
- credible difficulty ramp: tasks move from single-root-cause rollback to multi-team, multi-mitigation outage response
- deterministic evaluation: graders return stable scores in `[0.0, 1.0]`
- deployment-ready packaging: validated OpenEnv app plus Dockerized Hugging Face Space runtime

## Environment Overview

- Standard OpenEnv API: `reset()` / `step()` / `state()`
- Typed Pydantic models for action, observation, and state
- 3 deterministic tasks with easy -> medium -> hard progression
- Dense reward shaping across the trajectory
- Programmatic grader with scores in `[0.0, 1.0]`
- Baseline inference script with reproducible local scores
- Dockerized runtime for Hugging Face Spaces and OpenEnv validation

## Tasks

| Task ID | Difficulty | Real-world objective |
| --- | --- | --- |
| `cpu_spike` | Easy | Roll back a bad `api-gateway` deploy causing CPU and latency regression |
| `db_cascade` | Medium | Stop a DB connection-pool cascade, restore auth, and relieve primary pressure |
| `ddos_payment` | Hard | Mitigate a DDoS while activating payment fallback and coordinating response |

The hard task is intentionally not a single-fix puzzle. It requires traffic mitigation, payments failover, correct team escalation, and user-facing communication, while penalizing the kinds of wrong actions a real incident commander should avoid.

## Action Space

The environment accepts a typed `IncidentAction` model.

Supported `action_type` values:

- `run_query`
- `scale_service`
- `restart_pod`
- `rollback`
- `page_team`
- `toggle_feature`
- `post_status`
- `submit_rca`

Important fields:

| Field | Type | Purpose |
| --- | --- | --- |
| `action_type` | enum | Operation to execute |
| `service_name` | `str | None` | Target service for restart, rollback, or scale |
| `replicas` | `int | None` | Replica target for scale operations |
| `version` | `str | None` | Rollback version |
| `team` | `str | None` | On-call team to page |
| `query` | `str | None` | Investigation query |
| `feature_flag` | `str | None` | Feature toggle name |
| `enabled` | `bool | None` | Desired feature flag state |
| `message` | `str | None` | Status-page update or RCA text |

## Observation Space

The environment returns a typed `IncidentObservation`.

Key contents:

| Field group | Included signals |
| --- | --- |
| Incident metadata | `task_id`, `difficulty`, `title`, `objective`, `incident_id` |
| Live system state | `services`, `metrics`, `active_alerts`, `recent_logs` |
| Progress context | `actions_taken`, `progress`, `resolved`, `last_action_result` |
| Stakes | `elapsed_seconds`, `affected_users` |
| Standard OpenEnv fields | `reward`, `done` |

## State Space

`IncidentState` is the full serialized episode state used by the grader and `/state`.

It includes:

- mutable service state
- full log history
- feature flags
- paged teams
- status updates
- surfaced investigation findings
- resolution markers
- full action trace
- accumulated reward and progress score

## Reward Function

Reward is dense, not sparse.

- relevant investigation improves progress
- correct mitigation actions create larger positive deltas
- repeated or invalid actions reduce reward
- destructive actions on healthy systems are penalized
- submitted RCA quality contributes to the final trajectory score

Reward is computed as progress-score delta minus a small per-step cost, which gives the agent useful intermediate learning signal instead of only terminal success/failure.

## Grader

The grader is deterministic and returns a score in `[0.0, 1.0]`.

Scored components:

- investigation quality
- operational resolution
- mitigation coverage
- communication coverage
- RCA quality
- efficiency penalties
- destructive / invalid / repeated-action penalties

Because the grader is tied to concrete state transitions instead of only final text output, it produces useful partial-credit signals and is suitable for both evaluation and learning.

## Baseline

The repository includes `baseline.py`.

- If `OPENAI_API_KEY` is set, it can use an OpenAI model through tool calling.
- Without credentials, it uses a deterministic heuristic baseline.

Run the heuristic baseline:

```bash
uv run python baseline.py --force-heuristic
```

Run the OpenAI-backed baseline:

```bash
OPENAI_API_KEY=... uv run python baseline.py --model gpt-4.1-mini --seed 7
```

Verified local heuristic baseline:

| Task | Score |
| --- | --- |
| `cpu_spike` | `1.0000` |
| `db_cascade` | `1.0000` |
| `ddos_payment` | `1.0000` |
| Average | `1.0000` |

The heuristic baseline is a reproducibility and sanity-check baseline, not a claim that the environment is saturated. The hard task still requires coordinated action selection and is intended to be sensitive to weaker policies, invalid actions, and poor sequencing.

## Local Setup

```bash
uv venv --python 3.11 .venv
uv sync --extra dev
uv run pytest -q
```

Run the server locally:

```bash
uv run python -m uvicorn server.app:app --host 127.0.0.1 --port 8000
```

## OpenEnv Validation

Static validation:

```bash
uv run openenv validate
```

Runtime validation:

```bash
uv run python -m uvicorn server.app:app --host 127.0.0.1 --port 8000
uv run openenv validate --url http://127.0.0.1:8000
```

## Hugging Face Space Deployment

This repository is prepared for a Docker-based Hugging Face Space.

### Option 1: Push with OpenEnv

```bash
uv run openenv push --repo-id <hf-username>/incident-commander --interface
```

### Option 2: Push to an existing Docker Space

1. Create a new Hugging Face Space with SDK set to `Docker`.
2. Push this repository to the Space.
3. Keep the Space `README.md` front matter and `app_port: 8000`.
4. Optional: add `OPENAI_API_KEY` as a Space secret if you want the remote baseline to use an OpenAI model.

### Post-deploy checks

Replace `<space-url>` with your deployed Space URL.

```bash
curl https://<space-url>/health
curl https://<space-url>/tasks
uv run openenv validate --url https://<space-url>
```

## Hackathon Checklist

- real-world environment: yes
- typed OpenEnv models and standard API: yes
- 3 tasks with deterministic graders: yes
- dense reward with partial progress: yes
- baseline inference script: yes
- Docker and HF Space packaging: yes
- local validation completed: yes

## Docker Verification

Verified locally:

```bash
docker build -t incident-commander:local .
docker run -d --rm -p 8000:8000 --name incident-commander-test incident-commander:local
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/tasks
curl -X POST http://127.0.0.1:8000/baseline -H 'Content-Type: application/json' -d '{"use_openai_if_available": false}'
uv run openenv validate --url http://127.0.0.1:8000
docker rm -f incident-commander-test
```

## API Surface

Standard OpenEnv endpoints:

- `POST /reset`
- `POST /step`
- `GET /state`
- `GET /schema`
- `GET /metadata`
- `GET /health`

Additional evaluation endpoints:

- `GET /tasks`
- `POST /grader`
- `POST /baseline`
