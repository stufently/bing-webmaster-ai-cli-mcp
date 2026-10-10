from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_constraints_pin_current_direct_dependencies() -> None:
    text = (ROOT / "constraints.txt").read_text()
    for pin in (
        "httpx==0.28.1",
        "pydantic==2.13.4",
        "pydantic-settings==2.15.0",
        "click==8.4.2",
        "mcp==2.1.0",
        "uvicorn==0.52.4",
        "pytest==9.1.1",
        "pytest-asyncio==1.4.0",
        "ruff==0.16.4",
    ):
        assert pin in text


def test_docker_uses_latest_python_and_nonroot_user() -> None:
    text = (ROOT / "Dockerfile").read_text()
    assert "python:3.14.7-slim" in text
    assert re.search(r"^USER\s+(?!root\b|0\b)\S+", text, re.MULTILINE)


def test_ci_has_required_matrix_jobs_and_sha_pins() -> None:
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    for token in ("3.12", "3.13", "3.14", "lint:", "test:", "smoke:", "container:"):
        assert token in text
    uses = re.findall(r"uses:\s*[^\s@]+@([^\s#]+)", text)
    assert uses and all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in uses)


def test_release_uses_trusted_publishing() -> None:
    text = (ROOT / ".github" / "workflows" / "release.yml").read_text()
    assert "id-token: write" in text
    assert "environment: pypi" in text
    # Trusted Publishing only: no PyPI token or password in secrets.
    assert "secrets.PYPI" not in text
    assert "password:" not in text


def test_pypi_publish_is_gated_on_repository_variable() -> None:
    text = (ROOT / ".github" / "workflows" / "release.yml").read_text()
    job = text[text.index("  publish:") :]
    assert job.index("if: vars.PYPI_PUBLISH == 'true'") < job.index("steps:")


def test_release_runs_quality_gates_before_publishing() -> None:
    text = (ROOT / ".github" / "workflows" / "release.yml").read_text()
    publish = text.index("pypa/gh-action-pypi-publish")
    for command in ("make lint", "make test", "python scripts/smoke_mcp.py"):
        assert command in text
        assert text.index(command) < publish
