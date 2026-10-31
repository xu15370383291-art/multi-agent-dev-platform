import json

from sqlalchemy import select

from app.db import ModelProfile, WorkItem
from app.models import capability_signature

PROFILE = {
    "name": "test configuration",
    "base_url": "https://example.com/v1",
    "model_id": "test-model",
    "api_key": "TEST-SECRET-DO-NOT-EXPOSE",
}


def ready_profile(client, storage):
    response = client.post("/api/v1/model-profiles", json=PROFILE)
    assert response.status_code == 201, response.text
    profile_id = response.json()["id"]
    with storage[0]() as session:
        profile = session.scalar(select(ModelProfile).where(ModelProfile.profile_id == profile_id))
        profile.capabilities = {
            "structured": {"status": "passed", "signature": capability_signature(profile)}
        }
        session.commit()
    client.put("/api/v1/model-bindings", json={"profile_id": profile_id})
    return profile_id


def test_secret_redaction_immutable_revision_and_binding(client, storage):
    profile_id = ready_profile(client, storage)
    assert PROFILE["api_key"] not in client.get("/api/v1/model-profiles").text
    invalid = client.post("/api/v1/model-profiles", json={**PROFILE, "protocol": PROFILE["api_key"]})
    assert invalid.status_code == 422
    assert PROFILE["api_key"] not in invalid.text
    updated = client.patch(
        "/api/v1/model-profiles/" + profile_id,
        json={**PROFILE, "model_id": "new-model", "expected_revision": 1, "api_key": None},
    )
    assert updated.status_code == 200
    assert updated.json()["revision"] == 2
    assert updated.json()["capabilities"] == {}
    stale = client.patch("/api/v1/model-profiles/" + profile_id, json={**PROFILE, "expected_revision": 1})
    assert stale.status_code == 409
    with storage[0]() as session:
        rows = session.scalars(select(ModelProfile).order_by(ModelProfile.revision)).all()
        assert [row.model_id for row in rows] == ["test-model", "new-model"]
        assert PROFILE["api_key"] not in json.dumps([row.__dict__ for row in rows], default=str)


def test_run_requires_model_and_repeated_start_is_idempotent(client, storage):
    project = client.post(
        "/api/v1/projects",
        json={
            "name": "test",
            "project_type": "browser_game",
            "requirement": "Build a small playable browser game.",
        },
    ).json()
    path = f"/api/v1/projects/{project['id']}/runs"
    assert client.post(path, headers={"Idempotency-Key": "first"}).status_code == 409
    ready_profile(client, storage)
    first = client.post(path, headers={"Idempotency-Key": "first"})
    duplicate = client.post(path, headers={"Idempotency-Key": "first"})
    assert first.status_code == duplicate.status_code == 202
    assert first.json()["id"] == duplicate.json()["id"]
    assert client.post(path, headers={"Idempotency-Key": "different"}).status_code == 409
    with storage[0]() as session:
        assert len(session.scalars(select(WorkItem)).all()) == 1
    assert client.post(f"/api/v1/runs/{first.json()['id']}/cancel").json()["status"] == "cancelled"


def test_local_api_rejects_foreign_origins(client):
    assert client.get("/api/v1/agents", headers={"Origin": "https://untrusted.example"}).status_code == 403
    assert client.get("/api/v1/agents", headers={"Host": "untrusted.example"}).status_code == 403
    assert client.get("/api/v1/agents", headers={"Origin": "http://127.0.0.1:5173"}).status_code == 200


def test_runtime_limits_are_immutable_and_invalidate_capability_evidence(client, storage):
    profile_id = ready_profile(client, storage)
    changed = client.patch(
        "/api/v1/model-profiles/" + profile_id,
        json={
            **PROFILE,
            "api_key": None,
            "expected_revision": 1,
            "max_output_tokens": 16384,
            "timeout_seconds": 180,
        },
    )
    assert changed.status_code == 200
    assert changed.json()["max_output_tokens"] == 16384 and changed.json()["timeout_seconds"] == 180
    assert changed.json()["capabilities"] == {}
    with storage[0]() as db:
        rows = db.scalars(select(ModelProfile).order_by(ModelProfile.revision)).all()
        assert [(row.max_output_tokens, row.timeout_seconds) for row in rows] == [(4096, 90), (16384, 180)]
    invalid = client.patch(
        "/api/v1/model-profiles/" + profile_id,
        json={**PROFILE, "expected_revision": 2, "max_output_tokens": 0},
    )
    assert invalid.status_code == 422 and PROFILE["api_key"] not in invalid.text


def test_role_override_can_return_to_inherited_default(client, storage):
    profile_id = ready_profile(client, storage)
    assert (
        client.put("/api/v1/model-bindings", json={"profile_id": profile_id, "role": "product"}).status_code
        == 200
    )
    assert (
        client.put("/api/v1/model-bindings", json={"profile_id": None, "role": "product"}).status_code == 200
    )
    bindings = client.get("/api/v1/model-bindings").json()
    assert not any(row["role"] == "product" for row in bindings)
    assert any(row["role"] == "default" for row in bindings)


def test_thinking_mode_is_immutable_and_invalidates_capability_results(client, storage):
    data = {
        **PROFILE,
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model_id": "qwen3.8-flash",
        "thinking_mode": "disabled",
    }
    first = client.post("/api/v1/model-profiles", json=data).json()
    with storage[0]() as session:
        row = session.scalar(select(ModelProfile).where(ModelProfile.profile_id == first["id"]))
        row.capabilities = {"structured": {"status": "passed"}}
        session.commit()
    changed = client.patch(
        "/api/v1/model-profiles/" + first["id"],
        json={**data, "api_key": None, "thinking_mode": "enabled", "expected_revision": 1},
    )
    assert changed.status_code == 200
    assert changed.json()["thinking_mode"] == "enabled"
    assert changed.json()["revision"] == 2
    assert changed.json()["capabilities"] == {}
    with storage[0]() as session:
        rows = session.scalars(select(ModelProfile).order_by(ModelProfile.revision)).all()
        assert [row.thinking_mode for row in rows] == ["disabled", "enabled"]
        assert rows[0].credential_ref == rows[1].credential_ref
    rejected = client.post("/api/v1/model-profiles", json={**PROFILE, "thinking_mode": "enabled"})
    assert rejected.status_code == 422
    assert PROFILE["api_key"] not in rejected.text


def test_adapter_revision_invalidates_old_success_and_blocks_run(client, storage, monkeypatch):
    from app import models

    profile_id = ready_profile(client, storage)
    assert client.get("/api/v1/model-profiles").json()[0]["capabilities"]["structured"]["status"] == "passed"
    monkeypatch.setattr(models, "ADAPTER_VERSION", "next-test-adapter-version")
    response = client.get("/api/v1/model-profiles").json()[0]
    assert response["id"] == profile_id
    assert response["capabilities"] == {}
    assert response["capabilities_stale"] is True
    project = client.post(
        "/api/v1/projects",
        json={
            "name": "stale check",
            "project_type": "web_app",
            "requirement": "A test application requiring a current capability check.",
        },
    ).json()
    assert (
        client.post(
            f"/api/v1/projects/{project['id']}/runs", headers={"Idempotency-Key": "stale"}
        ).status_code
        == 409
    )


def test_adapter_metadata_and_deepseek_mode_validation_use_same_registry(client):
    response = client.post(
        "/api/v1/model-adapters/resolve",
        json={"base_url": "https://api.deepseek.com", "model_id": "deepseek-flash"},
    )
    assert response.status_code == 200
    assert response.json()["id"] == "deepseek"
    assert response.json()["supports_thinking"] is True
    saved = client.post(
        "/api/v1/model-profiles",
        json={
            **PROFILE,
            "base_url": "https://api.deepseek.com",
            "model_id": "deepseek-flash",
            "thinking_mode": "enabled",
        },
    )
    assert saved.status_code == 201
    assert saved.json()["adapter"] == response.json()
