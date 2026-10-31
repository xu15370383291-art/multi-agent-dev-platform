import pytest

from app.execution import ExecutionError
from app.repair_recovery_contract import RecoveryScopeReview, validate_review


@pytest.mark.parametrize(
    "field",
    [
        "scope_and_non_goals_preserved",
        "approved_requirements_and_original_tests_preserved",
        "diagnosis_supported_by_receipts",
        "next_step_matches_approved_build_strategy",
    ],
)
def test_independent_recovery_review_cannot_waive_any_boundary(field):
    values = {
        key: True
        for key in RecoveryScopeReview.model_fields
        if key not in {"explanation", "blocking_findings", "blocker_kinds"}
    }
    values[field] = False
    with pytest.raises(ExecutionError, match="plan_invalid"):
        validate_review(
            RecoveryScopeReview(
                **values,
                blocking_findings=[],
                explanation="Explicit rejection fixture preserves original approval boundaries",
            )
        )


def test_explicit_scope_blocker_still_rejects_all_true_flags():
    with pytest.raises(ExecutionError, match="plan_invalid"):
        validate_review(
            RecoveryScopeReview(
                scope_and_non_goals_preserved=True,
                approved_requirements_and_original_tests_preserved=True,
                diagnosis_supported_by_receipts=True,
                next_step_matches_approved_build_strategy=True,
                blocking_findings=["Cannot change original non-goal"],
                explanation="Explicit contradictory verdict must fail closed",
            )
        )


def test_source_diff_does_not_treat_unread_metadata_as_deleted(tmp_path, monkeypatch):
    import hashlib
    import io
    import tarfile
    from types import SimpleNamespace

    from app import continuation
    from app.repair_recovery_contract import current_source, source_changes

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for path, content in (
            (".gitignore", b"dist\nnode_modules\n"),
            ("index.html", b"<button>Actual</button>"),
            ("large.js", b"x" * 140000),
        ):
            member = tarfile.TarInfo("./" + path)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    raw = buffer.getvalue()
    checksum = hashlib.sha256(raw).hexdigest()
    (tmp_path / (checksum + ".tar")).write_bytes(raw)
    monkeypatch.setattr(continuation, "directory", lambda project, task: tmp_path)
    task = SimpleNamespace(id="fixture", snapshot_hash=checksum)
    original = {
        ".gitignore": "dist\nnode_modules\n",
        "index.html": "<button>Actual</button>",
        "large.js": "original large file",
        "removed.js": "real removed file",
    }
    observed = current_source(task, "fixture", original)
    assert observed["files"][".gitignore"] == original[".gitignore"]
    assert "large.js" in observed["omitted"]
    changes = source_changes(original, observed)
    assert set(changes) == {"removed.js"}, "Unread files cannot be represented as deletions"
    (tmp_path / (checksum + ".tar")).write_bytes(b"tampered")
    with pytest.raises(ExecutionError, match="integrity_failed"):
        current_source(task, "fixture", original)
