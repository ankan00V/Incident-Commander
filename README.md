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

I built `Incident Commander` as a production-style OpenEnv benchmark where an agent must act like a real incident commander during a live outage.

The agent is not rewarded for a single lucky fix. It has to investigate the right evidence, choose safe mitigations in order, escalate to the right teams, communicate clearly, and close with a defensible RCA.

`incident_commander` is built directly against the Round 1 statement:

> Build a complete, real-world OpenEnv environment that an AI agent can learn from through the standard `step()` / `reset()` / `state()` API.

This environment is intentionally operational, not game-like. It models the real work done by SRE/platform responders under pressure, including blast-radius control and business-impact-aware decisions.

## Engineering Direction

I used the official OpenEnv environment servers as structural references (`calendar_env`, `reasoning_gym_env`, `tbench2_env`, `carla_env`, `repl_env`) and kept the same fundamentals:

- typed action/observation/state schemas
- deterministic tasks and reproducible grading
- clean server packaging for Docker + Space deployment
- judge-friendly inspectability through explicit endpoints and replay paths

## Why This Submission Is Strong

- real-world utility: production incident response is a concrete workflow teams already perform
- meaningful learning signal: reward is dense, trajectory-shaped, and aligned to partial progress
- credible difficulty ramp: tasks move from single-root-cause rollback to multi-team, multi-mitigation outage response
- deterministic evaluation: graders return stable scores in `[0.0, 1.0]`
- deployment-ready packaging: validated OpenEnv app plus Dockerized Hugging Face Space runtime
- judge-friendly walkthroughs: a replayable `/demo` endpoint turns a baseline run into a step-by-step war-room timeline

## Environment Overview

- Standard OpenEnv API: `reset()` / `step()` / `state()`
- Typed Pydantic models for action, observation, and state
- 4 deterministic tasks with easy -> medium -> hard progression plus an adversarial runbook scenario
- Dense reward shaping across the trajectory
- Step-based incident escalation so unresolved outages worsen as turns are burned
- Programmatic grader with scores in `[0.0, 1.0]`
- Baseline inference script with reproducible local scores
- Replayable judge demo endpoint for step-by-step incident walkthroughs
- Dockerized runtime for Hugging Face Spaces and OpenEnv validation

## Tasks

| Task ID | Difficulty | Real-world objective |
| --- | --- | --- |
| `cpu_spike` | Easy | Roll back a bad `api-gateway` deploy causing CPU and latency regression |
| `db_cascade` | Medium | Stop a DB connection-pool cascade, restore auth, and relieve primary pressure |
| `ddos_payment` | Hard | Mitigate a DDoS while activating payment fallback and coordinating response |
| `runbook_failure` | Hard | Ignore an outdated auth runbook, fail over reads safely, and restore login traffic |

The hard tasks are intentionally not single-fix puzzles. One combines traffic mitigation, payments failover, correct team escalation, and user-facing communication. The other rewards agents that investigate and deliberately reject stale runbook guidance instead of blindly restarting a healthy service.

Business stakes by task:

- `cpu_spike`: search and navigation are failing for 8,400 users after a bad deploy
- `db_cascade`: 47,000 users are blocked from login while the primary DB pool is exhausted
- `ddos_payment`: 230,000 users are exposed to a live revenue and trust incident across security and payments domains
- `runbook_failure`: 31,000 users are blocked from login because an outdated runbook encourages a harmful mitigation

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
- queries return concrete incident evidence and live telemetry snapshots
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
- hard-task sequencing and safe blast-radius decisions
- RCA quality
- efficiency penalties
- destructive / invalid / repeated-action penalties

Because the grader is tied to concrete state transitions instead of only final text output, it produces useful partial-credit signals and is suitable for both evaluation and learning.

## Baseline

The repository includes two inference entrypoints:

- `baseline.py` for local development, smoke tests, and replay demos
- `inference.py` for the hackathon submission harness

`baseline.py` can run either:

- an OpenAI-compatible model when `OPENAI_API_KEY` is set
- or the deterministic heuristic fallback when credentials are absent

Run the heuristic baseline:

```bash
uv run python baseline.py --force-heuristic
```

Run the OpenAI-backed baseline:

```bash
OPENAI_API_KEY=... uv run python baseline.py --model gpt-4.1-mini --seed 7
```

Submission inference harness:

```bash
HF_TOKEN=<api-key> \
ENV_URL=http://127.0.0.1:8000 \
uv run python inference.py
```

`inference.py` uses the OpenAI client against any OpenAI-compatible endpoint and emits only the required structured stdout lines:

- `[START] task=<task_name> env=<benchmark> model=<model_name>`
- `[STEP] step=<n> action=<action_str> reward=<0.00> done=<true|false> error=<msg|null>`
- `[END] success=<true|false> steps=<n> rewards=<r1,r2,...,rn>`

By default, `inference.py` targets NVIDIA's OpenAI-compatible endpoint (`https://integrate.api.nvidia.com/v1`) with `meta/llama-3.1-8b-instruct`. You can override both with `API_BASE_URL` and `MODEL_NAME`.

If `ENV_URL` is unset, `inference.py` falls back to `OPENENV_URL`, then `SPACE_URL`, then `SPACE_HOST`, then `http://127.0.0.1:$PORT`. `HF_TOKEN` remains required.

Verified local heuristic baseline:

| Task | Score |
| --- | --- |
| `cpu_spike` | `1.0000` |
| `db_cascade` | `0.9000` |
| `ddos_payment` | `0.9200` |
| `runbook_failure` | `0.8800` |
| Average | `0.9250` |

The heuristic baseline is a reproducibility and smoke-test fallback, not the benchmark target. The environment is intentionally no longer a perfect-score oracle for the deterministic policy beyond the easy task. The medium task now rewards better sequencing and live coordination, while both hard tasks reward investigation, safer ordering, communication quality, and avoiding noisy actions on healthy systems.

## Judge Demo

This repository includes a replay-friendly demo path so judges can see the environment behave like a real operational system instead of only reading a final score.

Fastest local demo:

```bash
uv run python baseline.py --demo --task-id ddos_payment --force-heuristic
```

That produces a war-room replay with:

- the initial incident snapshot
- every action the baseline takes
- the outcome of each action
- live progress-score changes
- the final incident state and grader result

HTTP version of the same demo:

- `POST /demo` for a single replayable incident walkthrough
- `POST /demo` with `include_all_tasks=true` for a full judge showcase across all tasks

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

Then open:

- `http://127.0.0.1:8000/docs`
- `GET /tasks`
- `POST /demo`
- `POST /baseline`

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

Submission harness smoke test:

```bash
uv run python -m uvicorn server.app:app --host 127.0.0.1 --port 8000
HF_TOKEN=<api-key> uv run python inference.py
```

Override provider or model when needed:

```bash
API_BASE_URL=https://integrate.api.nvidia.com/v1 \
MODEL_NAME=meta/llama-3.1-70b-instruct \
HF_TOKEN=<api-key> \
uv run python inference.py
```

Pre-submission helper:

```bash
./validate-submission.sh https://your-space.hf.space .
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

For manual HTTP clients, `/reset` sets a session cookie and also returns `X-Session-Id`. If you are not using a stateful client, echo that header back to `/step` and `/state`.
