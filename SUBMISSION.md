# Incident Commander Submission

## Problem Statement

Build a complete, real-world OpenEnv environment that an AI agent can learn from through the standard `step()` / `reset()` / `state()` API.

## One-Sentence Pitch

`Incident Commander` is a production incident-response environment where an agent must restore a live service outage by combining diagnosis, mitigation, escalation, communication, and final RCA through the standard OpenEnv API.

## Submission Summary

This environment targets a genuine human workflow rather than a toy control problem. The agent acts as the incident commander during a real-style outage and is evaluated not only on whether the system is restored, but also on how it gets there: investigating the right signals, choosing safe mitigations, escalating correctly, protecting business-critical flows, avoiding harmful actions, and communicating clearly when the incident demands it.

I intentionally structured the server and packaging using the same design direction as strong official OpenEnv examples (`calendar_env`, `reasoning_gym_env`, `tbench2_env`, `carla_env`, `repl_env`): typed schemas, deterministic task logic, reproducible grading, and deployment-ready Docker layout.

## Why This Fits The Statement

- real-world domain: SRE and platform incident response
- standard OpenEnv interaction model: `reset()` / `step()` / `state()`
- typed action, observation, and state models
- deterministic tasks with reproducible graders
- dense reward shaping across the full trajectory
- root-level `inference.py` submission harness with structured stdout logs
- deployable as a Dockerized Hugging Face Space
- replayable demo timeline for judge walkthroughs

## Task Set

| Task | Difficulty | What the agent must do |
| --- | --- | --- |
| `cpu_spike` | Easy | Identify a bad deploy and roll back safely |
| `db_cascade` | Medium | Relieve pool exhaustion, restore auth, and reduce DB pressure |
| `ddos_payment` | Hard | Mitigate edge traffic, activate payment fallback, coordinate teams, and communicate externally |
| `runbook_failure` | Hard | Reject stale runbook guidance, fail over auth reads safely, and restore login traffic |

The difficulty progression is deliberate. The easy task is a mostly single-root-cause rollback. The medium task introduces cascading failure and multiple operational levers. The two hard tasks add coordination and communication pressure on top of technical mitigation, with the final task explicitly testing whether the agent can reason against bad instructions.

## Seeded Task Variants (Anti-Overfitting)

The environment now supports deterministic seeded task variants:

- `POST /reset` accepts `seed`
- each seed maps to one of `canonical`, `template_a`, `template_b`, `template_c`
- variants keep root-cause and mitigation requirements unchanged, but permute incident log templates
- this reduces policy overfitting to one exact wording while preserving deterministic grading

Variant metadata is visible in `observation.task_variant` / `state.task_variant`, and `/tasks` exposes the variant strategy block.

## Mini Benchmark Matrix

`benchmark_results.json` includes a compact matrix on all tasks:

| Policy | cpu_spike | db_cascade | ddos_payment | runbook_failure | avg |
| --- | --- | --- | --- | --- | --- |
| `heuristic` | `1.0000` | `0.9000` | `0.9200` | `0.8800` | `0.9250` |
| `meta/llama-3.1-8b-instruct` | `0.3100` | `0.0000` | `0.2150` | `0.1700` | `0.1738` |
| `meta/llama-3.1-70b-instruct` | `0.9329` | `0.7300` | `0.9200` | `0.8800` | `0.8657` |
| `meta/llama-3.1-405b-instruct` | `0.9400` | `0.0000` | `0.0000` | `0.7675` | `0.4269` |

## Why This Environment Should Score Well

### Real-world utility

This environment models a workflow that reliability engineers and incident commanders perform in production systems. It is useful for both evaluation and training because success depends on sequencing, judgment, and avoiding destructive actions under pressure. It is immediately more practical than a game, puzzle, or office-toy environment.

### Task and grader quality

- 4 deterministic tasks with clear escalation in difficulty
- graders return continuous scores in `[0.0, 1.0]`
- grading is tied to concrete operational outcomes, not vague free-text matching
- the medium task rewards correct mitigation ordering and live database-team escalation instead of giving an easy perfect score
- one hard task requires ordered mitigation, multi-team coordination, and substantive communication
- the other hard task punishes blindly following an outdated runbook and rewards independent investigation
- wrong actions such as touching healthy systems or paging the wrong team are explicitly penalized

### Environment design

- dense reward comes from grader-aligned progress deltas
- unresolved incidents escalate as the agent burns steps, creating real time pressure without breaking determinism
- repeated and invalid actions are penalized
- destructive actions are penalized separately
- HTTP episodes are isolated per client session, while OpenEnv WebSocket sessions use fresh env instances
- episode boundaries are explicit and deterministic
- state changes reflect the operational consequences of the agent's choices, which creates a learnable sequential decision problem instead of a one-shot quiz

### Code quality and spec compliance

- OpenEnv validation passes
- Docker build and container runtime were verified locally
- additional `/tasks`, `/grader`, and `/baseline` endpoints are implemented
- typed schemas are exposed and serializable
- root `inference.py` requires `HF_TOKEN` and supports `API_BASE_URL` / `MODEL_NAME` overrides, with NVIDIA-compatible defaults baked in
- `inference.py` emits the required `[START]`, `[STEP]`, and `[END]` lines to stdout
- the environment is already prepared for Docker-based Hugging Face Space deployment

### Creativity and novelty

Incident response is less common than game-like or office-toy environments, and the hard scenario combines mitigation, communications, escalation, and business continuity into one episode. The key novelty is that the agent is not just solving a technical root cause. It is running the war room: protecting revenue, coordinating the right teams, and communicating externally while recovery is still in progress.

The newest task adds a second kind of novelty: the environment includes adversarial operational guidance. A strong agent must recognize that the documented runbook is stale and intentionally deviate from it.

## Why Judges Can Evaluate It Quickly

This project is designed to be inspectable in minutes:

- `/about` exposes quick judge metadata (task count, action types, endpoint map)
- `/tasks` exposes the task set, difficulty ramp, and typed action/observation/state schemas
- `/baseline` returns a reproducible score report across all tasks
- `/demo` returns a step-by-step replay timeline for a single incident or the whole showcase
- the hard tasks (`ddos_payment` and `runbook_failure`) are strong live demos because they show different kinds of reasoning failure: noisy mitigation versus blind runbook-following

Judge quick eval (copy-paste):

1. `curl -s http://127.0.0.1:8000/about`
Expected: task count `4`, action types, variant labels.

2. `curl -s -X POST http://127.0.0.1:8000/reset -H 'Content-Type: application/json' -d '{"task_id":"ddos_payment","seed":7}'`
Expected: `observation.task_variant` present (`template_c` for seed `7`), `done=false`.

3. `uv run openenv validate --url http://127.0.0.1:8000`
Expected: successful OpenEnv validation.

## Verification Results

Verified locally:

- `uv run pytest -q` -> `33 passed`
- `uv run openenv validate` -> passed
- `uv run python baseline.py --force-heuristic` -> average score `0.9250` (`db_cascade` = `0.90`, `ddos_payment` = `0.92`, `runbook_failure` = `0.88`)
- `docker build -t incident-commander:local .` -> passed
- `uv run openenv validate --url http://127.0.0.1:8001` against the running server -> passed
- full local HTTP flow verified for `runbook_failure`: `/reset` -> `/step` -> `/state` -> `/grader`
- `inference.py` defaults to NVIDIA's OpenAI-compatible endpoint and can be redirected with `API_BASE_URL` / `MODEL_NAME`
- `./validate-submission.sh <space-url> .` is included for local preflight checks

Common bad policies and why the grader catches them:

- restarting healthy services: recorded as destructive actions and penalized in hard tasks
- skipping investigation: required findings are missing so mitigation sequencing cannot score full credit
- single-mitigation behavior: partial mitigation leaves resolution and communication coverage incomplete
- blindly following stale runbooks: `runbook_failure` explicitly penalizes auth restart-first behavior

Submission inference contract:

- `inference.py` is placed in the repository root
- it uses the OpenAI Python client against an OpenAI-compatible endpoint
- it requires `HF_TOKEN` and supports `API_BASE_URL` / `MODEL_NAME` overrides
- it writes `inference_results.json` for post-run inspection while keeping stdout in the required structured format

Recommended live judge demo:

- `uv run python baseline.py --demo --task-id ddos_payment --force-heuristic`
- open `/docs`, then run `GET /tasks` and `POST /demo`

## Deployment Readiness

This repository is already packaged as a Docker-based OpenEnv app and can be pushed to Hugging Face Spaces without structural changes. The app exposes the standard OpenEnv endpoints plus `/tasks`, `/grader`, and `/baseline` for external evaluation workflows.

## Deployment

Recommended push command:

```bash
uv run openenv push --repo-id <hf-username>/incident-commander --interface
```

If OpenAI-backed remote baseline runs are desired, add `OPENAI_API_KEY` as a Hugging Face Space secret. The heuristic baseline does not require any secret.
