"""Real PG/API, independent run and approval records, no paid model calls."""

from app.db import Approval, Document, Project, Run


def test_workbench_and_documents_isolate_runs_and_pending_approvals(task_context, storage, client):
    run_id, _, _, _ = task_context
    with storage[0]() as db, db.begin():
        project_id = db.get(Run, run_id).project_id
        other = Run(project_id=project_id, idempotency_key="new-round", status="waiting_approval")
        db.add(other)
        db.flush()
        doc = Document(
            run_id=other.id,
            kind="prd",
            version=1,
            markdown="New round only",
            payload={},
            content_hash="e" * 64,
        )
        db.add(doc)
        db.flush()
        db.add(Approval(run_id=other.id, document_id=doc.id, status="pending"))
        other_id = other.id
        foreign = Project(
            name="Other project", project_type="web_app", requirement="Explicit isolation fixture"
        )
        db.add(foreign)
        db.flush()
        foreign_run = Run(project_id=foreign.id, idempotency_key="foreign")
        db.add(foreign_run)
        db.flush()
        foreign_id = foreign_run.id
        empty = Run(project_id=project_id, idempotency_key="empty-round")
        db.add(empty)
        db.flush()
        empty_id = empty.id
    base = f"/api/v1/projects/{project_id}"
    for chosen, count in [(run_id, 2), (other_id, 1), (empty_id, 0)]:
        response = client.get(base + "/workbench", params={"run_id": chosen})
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["current_run_id"] == chosen
        assert len(data["documents"]) == count
        for group in ["documents", "approvals", "approval_history"]:
            assert all(row["run_id"] == chosen for row in data[group])
        docs = client.get(base + "/documents", params={"run_id": chosen}).json()
        assert len(docs) == count and all(row["run_id"] == chosen for row in docs)
    assert len(client.get(base + "/documents").json()) == 3  # legacy archive API remains available
    for endpoint in ["/documents", "/workbench"]:
        assert client.get(base + endpoint, params={"run_id": foreign_id}).status_code == 404
        assert client.get(base + endpoint, params={"run_id": "not-a-run"}).status_code == 404
