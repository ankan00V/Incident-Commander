from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_validate_submission_script_exists() -> None:
    script = ROOT / "validate-submission.sh"
    assert script.exists()
    assert script.is_file()


def test_inference_defaults_are_documented_in_submission() -> None:
    submission = (ROOT / "SUBMISSION.md").read_text()
    assert "NVIDIA-compatible defaults" in submission
