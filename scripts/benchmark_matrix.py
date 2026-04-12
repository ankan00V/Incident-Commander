#!/usr/bin/env python3
"""Run a compact benchmark matrix for heuristic vs multiple LLM policies."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from incident_commander.baseline import run_baseline_sync
from incident_commander.task_bank import list_tasks


DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_MODELS = (
    "meta/llama-3.1-8b-instruct",
    "meta/llama-3.1-70b-instruct",
    "mistralai/mistral-7b-instruct-v0.3",
)


def _scores_by_task(report: dict[str, Any]) -> dict[str, float]:
    return {result["task_id"]: float(result["score"]) for result in report["results"]}


def _run_heuristic() -> dict[str, Any]:
    return run_baseline_sync(use_openai_if_available=False)


def _run_model(model: str, base_url: str) -> dict[str, Any]:
    return run_baseline_sync(
        model=model,
        base_url=base_url,
        use_openai_if_available=True,
        strict_openai=False,
    )


def _require_api_key() -> None:
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("HF_TOKEN")
    if not api_key:
        raise SystemExit(
            "Missing credentials: set OPENAI_API_KEY or HF_TOKEN before running benchmark_matrix.py"
        )
    os.environ["OPENAI_API_KEY"] = api_key


def _format_markdown_table(matrix: list[dict[str, Any]]) -> str:
    task_ids = [task.task_id for task in list_tasks()]
    header = "| Policy | " + " | ".join(task_ids) + " | avg |"
    divider = "| --- | " + " | ".join("---" for _ in task_ids) + " | --- |"
    rows = [header, divider]
    for row in matrix:
        score_cells = [f"{row['scores'][task_id]:.4f}" for task_id in task_ids]
        rows.append(
            f"| {row['policy']} | "
            + " | ".join(score_cells)
            + f" | {row['average_score']:.4f} |"
        )
    return "\n".join(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="OpenAI-compatible base URL",
    )
    parser.add_argument(
        "--models",
        nargs="*",
        default=list(DEFAULT_MODELS),
        help="Model IDs for the matrix (2-3 recommended)",
    )
    parser.add_argument(
        "--out",
        default="benchmark_results.json",
        help="Output JSON path",
    )
    args = parser.parse_args()

    _require_api_key()

    matrix: list[dict[str, Any]] = []

    heuristic_report = _run_heuristic()
    matrix.append(
        {
            "policy": "heuristic",
            "mode": heuristic_report["mode"],
            "scores": _scores_by_task(heuristic_report),
            "average_score": float(heuristic_report["average_score"]),
        }
    )

    for model in args.models:
        report = _run_model(model, args.base_url)
        matrix.append(
            {
                "policy": model,
                "mode": report["mode"],
                "scores": _scores_by_task(report),
                "average_score": float(report["average_score"]),
            }
        )

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": args.base_url,
        "matrix": matrix,
        "markdown_table": _format_markdown_table(matrix),
    }

    out_path = Path(args.out)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(payload["markdown_table"])
    print(f"\nSaved benchmark matrix to {out_path}")


if __name__ == "__main__":
    main()
