"""Read projections and evidence fixtures; no provider calls or business execution."""

import hashlib
import io
import tarfile

import pytest
from sqlalchemy import select

from app.config import settings
from app.db import Candidate, Delivery, QualityReport, Run, TaskExecution
from app.workspace import project_root
from tests.test_m4_events import project


@pytest.fixture
def evidence(task_context, storage):
    run_id, task_id, _, docs = task_context
    with storage[0]() as db, db.begin():
        task = db.get(TaskExecution, task_id)
        candidate = Candidate(
            execution_id=task_id,
            run_id=run_id,
            prd_id=docs[0],
            architecture_id=docs[1],
            plan_version=1,
            commit_sha="a" * 40,
            source_snapshot_hash="b" * 64,
            environment={},
        )
        db.add(candidate)
        db.flush()
        report = QualityReport(
            candidate_id=candidate.id,
            commit_sha=candidate.commit_sha,
            role="qa",
            profile_revision_id=task.profile_revision_id,
            status="failed",
            payload={
                "execution": {"commit_sha": candidate.commit_sha, "results": [{"screenshot": "case-0.png"}]}
            },
        )
        db.add(report)
        db.flush()
        delivery = Delivery(candidate_id=candidate.id, archive_hash="c" * 64, status="ready", manifest={})
        db.add(delivery)
        db.flush()
        delivery.manifest = {
            "preview_url": f"{settings().preview_base_url}/preview/{delivery.id}/",
            "files": {"web/index.html": hashlib.sha256(b"actual page").hexdigest()},
        }
        return db.get(Run, run_id).project_id, report.id, delivery.id


def archive(storage, identity, report_id, *, variant="valid"):
    image = b"\x89PNG\r\n\x1a\nexplicit fixture bytes"
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w") as bundle:
        entry = tarfile.TarInfo("quality-artifacts/case-0.png")
        if variant == "link":
            entry.type = tarfile.SYMTYPE
            entry.linkname = "/private"
        else:
            entry.size = len(image)
        bundle.addfile(entry, io.BytesIO(image) if entry.isfile() else None)
        if variant == "duplicate":
            bundle.addfile(entry, io.BytesIO(image))
    content = data.getvalue()
    digest = hashlib.sha256(content).hexdigest()
    path = project_root(settings().workspace_root, identity) / "reports" / report_id / (digest + ".tar")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if variant != "corrupt" else b"corrupt")
    with storage[0]() as db, db.begin():
        db.get(QualityReport, report_id).evidence_hash = digest
    return path, image


@pytest.mark.parametrize("variant", ["valid", "missing", "corrupt", "link", "duplicate", "wrong-sha"])
def test_screenshot_verifies_exact_archive_and_version(evidence, storage, client, variant):
    identity, report_id, _ = evidence
    path, image = archive(storage, identity, report_id, variant=variant)
    if variant == "missing":
        path.unlink()
    if variant == "wrong-sha":
        with storage[0]() as db, db.begin():
            db.get(QualityReport, report_id).commit_sha = "d" * 40
    response = client.get(f"/api/v1/projects/{identity}/quality-reports/{report_id}/screenshots/0")
    if variant == "valid":
        assert response.status_code == 200 and response.content == image
        assert response.headers["x-content-type-options"] == "nosniff"
    else:
        assert response.status_code == 409
    foreign = project(client)
    assert (
        client.get(f"/api/v1/projects/{foreign}/quality-reports/{report_id}/screenshots/0").status_code == 404
    )
    assert (
        client.get(f"/api/v1/projects/{identity}/quality-reports/{report_id}/screenshots/-1").status_code
        == 422
    )


@pytest.mark.parametrize("variant", ["valid", "down", "wrong-sha", "redirect", "foreign", "large"])
def test_preview_status_is_read_only_bounded_and_hash_bound(evidence, storage, client, monkeypatch, variant):
    import httpx

    from app import api

    _, _, delivery_id = evidence
    if variant == "foreign":
        with storage[0]() as db, db.begin():
            row = db.get(Delivery, delivery_id)
            row.manifest = {**row.manifest, "preview_url": "https://foreign.invalid/"}
    original = httpx.Client
    observed = []

    def transport(request):
        observed.append(str(request.url))
        if variant == "down":
            raise httpx.ConnectError("explicit fixture", request=request)
        return httpx.Response(
            302 if variant == "redirect" else 200,
            content=b"x" * (4 * 1024**2 + 1)
            if variant == "large"
            else b"wrong"
            if variant == "wrong-sha"
            else b"actual page",
        )

    monkeypatch.setattr(
        api.httpx, "Client", lambda **kw: original(**kw, transport=httpx.MockTransport(transport))
    )
    result = client.get(f"/api/v1/deliveries/{delivery_id}/preview-status")
    assert result.status_code == 200 and result.json()["available"] == (variant == "valid")
    assert len(observed) == (0 if variant == "foreign" else 1)
    with storage[0]() as db:
        assert db.get(Delivery, delivery_id).status == "ready"


def test_events_and_tool_read_projection_redact_without_changing_evidence(task_context, storage, client):
    from app.db import Event, event
    from app.event_stream import encode

    run_id, _, _, _ = task_context
    token = "sk-" + "X" * 32
    with storage[0]() as db, db.begin():
        identity = db.get(Run, run_id).project_id
        event(
            db,
            identity,
            "diagnostic.fixture",
            {"api_key": token, "stdout": f"inert <img> {token}", "raw_response": "private"},
            run_id,
        )
    path = f"/api/v1/projects/{identity}"
    for endpoint in [
        "/events",
        "/workbench",
        "/events/history",
        f"/events/changes?cursor={encode(identity, None, 0)}",
    ]:
        response = client.get(path + endpoint)
        assert response.status_code == 200 and token not in response.text and "private" not in response.text
    with storage[0]() as db:
        assert token in str(db.scalar(select(Event).where(Event.kind == "diagnostic.fixture")).data)


def test_planning_action_contract_uses_same_baseline_gate(client, storage):
    from app.db import Run, WorkItem
    from tests.test_planning_recovery import seed

    run_id, docs = seed(storage)
    action = client.get(f"/api/v1/runs/{run_id}/actions").json()["planning_revision"]
    assert action["enabled"] and action["architecture_id"] == docs[1] and action["calls_model"]
    with storage[0]() as db, db.begin():
        db.add(WorkItem(run_id=run_id, action_key="unsettled", status="unknown", payload={}))
    assert not client.get(f"/api/v1/runs/{run_id}/actions").json()["planning_revision"]["enabled"]
    with storage[0]() as db:
        assert db.get(Run, run_id).model_calls == 3


@pytest.mark.parametrize("failure", [False, True])
def test_quality_action_view_cannot_bypass_product_failures(evidence, storage, client, failure):
    from sqlalchemy import delete

    identity, qa_id, delivery_id = evidence
    with storage[0]() as db, db.begin():
        db.execute(delete(Delivery).where(Delivery.id == delivery_id))
        qa = db.get(QualityReport, qa_id)
        candidate = db.get(Candidate, qa.candidate_id)
        task = db.get(TaskExecution, candidate.execution_id)
        run = db.get(Run, candidate.run_id)
        task.status = "succeeded"
        task.result = {"commit_sha": candidate.commit_sha}
        candidate.status = "build_passed"
        run.owner = None
        run.status = "paused"
        run.phase = "delivery_gate"
        qa.payload = {
            "tools": {"browser": {"status": "succeeded"}},
            "tests": [{"script": "assert.equal(1,1)"}],
            "execution": {
                "ok": True,
                "results": [
                    {
                        "status": "failed",
                        "error": "assertion failed" if failure else "browser is not defined",
                        "assertions": 1 if failure else 0,
                    }
                ],
            },
        }
        review = QualityReport(
            candidate_id=candidate.id,
            commit_sha=candidate.commit_sha,
            role="reviewer",
            profile_revision_id=qa.profile_revision_id,
            status="failed",
            payload={
                "tools": {"read": {"status": "succeeded"}},
                "issues": [{"severity": "major", "path": "src/main.ts" if failure else "quality-tests.json"}],
            },
        )
        db.add(review)
    actions = client.get(f"/api/v1/runs/{run.id}/actions").json()
    assert actions["quality_revision"]["enabled"] == (not failure)
    assert actions["quality_revision"]["candidate_id"] == candidate.id
    assert not actions["quality_recheck"]["enabled"]
    if failure:
        response = client.post(
            f"/api/v1/candidates/{candidate.id}/quality-revisions",
            json={
                "qa_report_id": qa_id,
                "reviewer_report_id": review.id,
                "feedback": "明确的测试资产反馈但不能放行产品失败",
            },
        )
        assert response.status_code == 409
    resolution = client.get(f"/api/v1/projects/{identity}/model-resolution", params={"run_id": run.id}).json()
    assert (
        next(row for row in resolution if row["role"] == "qa")["execution_snapshots"][0]["execution_id"]
        == qa_id
    )
