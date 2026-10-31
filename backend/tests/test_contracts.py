from pathlib import Path

import pytest
from pydantic import ValidationError

from app.domain import ArchitectureResult, DecisionInput, ProfileInput
from app.workspace import safe_path


@pytest.mark.parametrize(
    "url",
    [
        "file:///c:/private",
        "https://user:secret@example.com/v1",
        "https://example.com/v1?api_key=secret",
        "https://example.com/#secret",
    ],
)
def test_profile_rejects_credential_urls(url):
    with pytest.raises(ValidationError):
        ProfileInput(name="invalid", base_url=url, model_id="test")


def task(name, dependencies):
    return {
        "id": name,
        "title": name,
        "role": "game_developer",
        "goal": "implement feature",
        "depends_on": dependencies,
        "acceptance_ids": ["AC-001"],
        "expected_outputs": ["src/game.ts"],
    }


@pytest.mark.parametrize(
    "tasks", [[task("A", ["B"]), task("B", ["A"])], [task("A", ["missing"])], [task("A", []), task("A", [])]]
)
def test_dag_rejects_invalid_dependencies(tasks):
    with pytest.raises(ValidationError):
        ArchitectureResult(markdown="architecture " * 10, tasks=tasks)


def test_revision_feedback_and_boundary(tmp_path):
    with pytest.raises(ValidationError):
        DecisionInput(decision="request_changes", feedback=" ", document_version=1)
    with pytest.raises(ValueError):
        safe_path(tmp_path, "../private.txt")
    with pytest.raises(ValueError):
        safe_path(tmp_path, str(Path("C:/private.txt")))
    with pytest.raises(ValueError):
        safe_path(tmp_path, "file.txt:secret")
    assert safe_path(tmp_path, "docs/prd.md") == tmp_path / "docs" / "prd.md"
