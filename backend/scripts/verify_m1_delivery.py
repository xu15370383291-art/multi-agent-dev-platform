"""Explicit independent verification of a real delivered ZIP; no business code execution."""

import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path, PurePosixPath

import httpx


def main(run_id, api="http://127.0.0.1:8000/api/v1", output=None):
    with httpx.Client(timeout=15, trust_env=False) as client:

        def get(path):
            response = client.get(api + path)
            response.raise_for_status()
            return response.json()

        deliveries = get(f"/runs/{run_id}/deliveries")
        assert len(deliveries) == 1, "Exactly one ready delivery required"
        delivery = deliveries[0]
        assert delivery["status"] == "ready"
        response = client.get(api + f"/deliveries/{delivery['id']}/download")
        response.raise_for_status()
        content = response.content
        assert hashlib.sha256(content).hexdigest() == delivery["archive_hash"]
        repeated = client.get(api + f"/deliveries/{delivery['id']}/download")
        repeated.raise_for_status()
        assert repeated.content == content, "Repeated download changed immutable delivery"
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = archive.namelist()
            assert len(names) == len(set(names)), "Duplicate ZIP paths"
            assert sum(info.file_size for info in archive.infolist()) < 64 * 1024 * 1024
            for info in archive.infolist():
                path = PurePosixPath(info.filename)
                assert not path.is_absolute() and ".." not in path.parts
                assert "\\" not in info.filename and ":" not in info.filename
                assert not info.is_dir() and info.file_size <= 4 * 1024 * 1024
                assert (info.external_attr >> 16) & 0o170000 != 0o120000
                assert not any(
                    p.lower().startswith(".env") or p in (".git", "node_modules") for p in path.parts
                )
            manifest = json.loads(archive.read("manifest.json"))
            assert manifest == delivery["manifest"]
            assert set(names) == set(manifest["files"]) | {"manifest.json"}
            for name, digest in manifest["files"].items():
                assert hashlib.sha256(archive.read(name)).hexdigest() == digest, name
            assert manifest["run_id"] == run_id
            candidates = get(f"/runs/{run_id}/candidates")
            candidate = next(c for c in candidates if c["id"] == manifest["candidate_id"])
            assert candidate["status"] == "build_passed" and candidate["commit_sha"] == manifest["commit_sha"]
            assert candidate["build_result"]["commit_sha"] == manifest["commit_sha"]
            integration = candidate["build_result"]["integration"]
            assert integration["status"] == "merged" and integration["target_sha"] == manifest["commit_sha"]
            tasks = get(f"/runs/{run_id}/tasks")
            task = next(t for t in tasks if t["id"] == candidate["execution_id"])
            assert task["status"] == "succeeded"
            if candidate["build_result"].get("integration_id"):
                collaboration = get(f"/runs/{run_id}/collaboration")
                journal = next(
                    j
                    for j in collaboration["integrations"]
                    if j["id"] == candidate["build_result"]["integration_id"]
                )
                assert journal["status"] == "merged" and journal["target_sha"] == manifest["commit_sha"]
                assert journal["result"]["integration"]["source_sha"] == task["result"]["commit_sha"]
                assert journal["base_sha"] == integration["base_sha"]
                assert manifest["plan_id"] == collaboration["current_plan_id"]
                plan = json.loads(archive.read("documents/PLAN.json"))
                assert plan["id"] == manifest["plan_id"] and plan["content_hash"] == manifest["plan_hash"]
                assert (
                    hashlib.sha256(
                        json.dumps(plan["payload"], ensure_ascii=False, sort_keys=True).encode()
                    ).hexdigest()
                    == plan["content_hash"]
                )
                assert not any(collaboration["partition"][key] for key in ("ready", "waiting", "blocked"))
                preliminary = json.loads(archive.read("reports/reviewer_initial.json"))
                assert (
                    preliminary["commit_sha"] == manifest["commit_sha"]
                    and preliminary["payload"]["preliminary_only"]
                )
            else:
                assert task["result"]["commit_sha"] == manifest["commit_sha"]
                assert integration["base_sha"] == task["base_commit_sha"]
            assert manifest["prd"]["id"] == candidate["prd_id"] == task["prd_id"]
            assert manifest["architecture"]["id"] == candidate["architecture_id"] == task["architecture_id"]
            assert hashlib.sha256(archive.read("documents/PRD.md")).hexdigest() == manifest["prd"]["hash"]
            assert (
                hashlib.sha256(archive.read("documents/ARCHITECTURE.md")).hexdigest()
                == manifest["architecture"]["hash"]
            )
            if candidate["build_result"].get("strategy") == "vite_typescript":
                assert (
                    hashlib.sha256(archive.read("source/package-lock.json")).hexdigest()
                    == manifest["lock_hash"]
                )
                assert "source/package.json" in names
            else:
                assert (
                    candidate["build_result"]["strategy"] == "static_html" and manifest["lock_hash"] is None
                )
                assert "source/index.html" in names
            documents = get(f"/projects/{manifest['project_id']}/documents")
            approved_prd = next(d for d in documents if d["id"] == manifest["prd"]["id"])
            assert approved_prd["content_hash"] == manifest["prd"]["hash"]
            assert {r["criterion_id"] for r in manifest["acceptance"]} == {
                c["id"] for c in approved_prd["payload"]["criteria"]
            }
            assert all(r["status"] == "passed" and r["assertions"] > 0 for r in manifest["acceptance"])
            for required in (
                "RUNNING.md",
                "source/.gitignore",
                "web/index.html",
            ):
                assert required in names, required
            # The delivery contract requires a packaged running guide; approved
            # static projects need not also contain a source file named README.md.
            guide = archive.read("RUNNING.md").decode("utf8")
            assert "python -m http.server" in guide and "--directory web" in guide
            assert any(n.startswith("reports/") and n.endswith(".png") for n in names)
            assert any(n.startswith("tests/") for n in names)
            all_profiles = get(f"/runs/{run_id}/quality-reports")
            profiles = [p for p in all_profiles if p["id"] in (manifest["qa_id"], manifest["reviewer_id"])]
            assert len(profiles) == 2
            assert {p["role"] for p in profiles} == {"qa", "reviewer"}
            assert all(
                p["status"] == "passed" and p["commit_sha"] == manifest["commit_sha"] for p in profiles
            )
            assert not any(
                i["severity"] in ("critical", "major")
                for p in profiles
                for i in p["payload"].get("issues", [])
            )
            qa = next(p for p in profiles if p["role"] == "qa")
            reviewer = next(p for p in profiles if p["role"] == "reviewer")
            assert (
                reviewer["payload"]["environment"]
                == qa["payload"]["environment"]
                == qa["payload"]["execution"]["environment"]
            )
            assert {p["attempt"] for p in profiles} == {manifest["quality_attempt"]}
            for historical in manifest["quality_history"]:
                original = next(p for p in all_profiles if p["id"] == historical["id"])
                packaged_history = json.loads(archive.read(f"reports/history/{original['id']}.json"))
                assert packaged_history["status"] == original["status"] == historical["status"]
                assert packaged_history["asset_hash"] == original["asset_hash"]
            assert json.loads(archive.read("tests/browser-tests.json")) == qa["payload"]["tests"]
            tests_hash = hashlib.sha256(
                json.dumps(qa["payload"]["tests"], ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest()
            assert tests_hash == qa["asset_hash"]
            for profile in profiles:
                packaged = json.loads(archive.read(f"reports/{profile['role']}.json"))
                assert packaged["id"] == profile["id"]
                assert packaged["commit_sha"] == manifest["commit_sha"]
            assert {n[7:] for n in names if n.startswith("source/")} == set(qa["payload"]["source_names"])
            directory = (
                Path(output) if output else Path(__file__).resolve().parents[2] / ".local" / "acceptance"
            ) / delivery["id"]
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "delivery.zip").write_bytes(content)
            (directory / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            # Only validated static bytes are written. Never run or extract source code on host.
            root = (directory / "web").resolve()
            for name in names:
                if name.startswith("web/"):
                    target = (root / PurePosixPath(name).relative_to("web")).resolve()
                    assert target.is_relative_to(root)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(name))
            summary = {
                "run_id": run_id,
                "delivery_id": delivery["id"],
                "commit_sha": manifest["commit_sha"],
                "archive_hash": delivery["archive_hash"],
                "verified_files": len(names),
                "preview_url": manifest["preview_url"],
                "static_directory": str(root),
                "qa_id": manifest["qa_id"],
                "reviewer_id": manifest["reviewer_id"],
                "browser_version": manifest["browser_version"],
                "status": "passed",
            }
            (directory / "verification.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
            legacy = directory.parent / "delivery-verification.json"
            if not legacy.exists() or json.loads(legacy.read_text())["run_id"] == run_id:
                legacy.write_text(json.dumps(summary, indent=2), encoding="utf-8")
            print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main(*sys.argv[1:])
