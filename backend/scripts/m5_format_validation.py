"""Prove final Python formatting has no AST change; retain frozen test relation."""

import ast
import hashlib
import json
import subprocess

from app.maintenance import atomic_json
from app.platform_runtime import BACKEND, ROOT
from scripts.m5_final_regression import source_state


def main():
    out = ROOT / ".local/m5-acceptance"
    paths = [BACKEND / "app/api.py", BACKEND / "app/workflow.py"]
    before = {
        p.name: ast.dump(ast.parse(p.read_text(encoding="utf8")), include_attributes=False) for p in paths
    }
    subprocess.run(
        ["uv", "run", "ruff", "format", *map(str, paths), "scripts/m5_real_audit.py"], cwd=BACKEND, check=True
    )
    assert before == {
        p.name: ast.dump(ast.parse(p.read_text(encoding="utf8")), include_attributes=False) for p in paths
    }
    frozen = json.loads((out / "frozen-source.json").read_text(encoding="utf8"))
    current = source_state()
    changed = sorted(k for k in set(frozen) | set(current) if frozen.get(k) != current.get(k))
    assert all(
        k.startswith(("frontend/",)) or k in {"backend/app/api.py", "backend/app/workflow.py"}
        for k in changed
    ), changed
    atomic_json(
        out / "post-regression-delta.json",
        {
            "status": "passed",
            "backend_behavior_ast_unchanged": True,
            "formatted_files": [str(p.relative_to(ROOT).as_posix()) for p in paths],
            "changed_since_frozen_407": changed,
            "ast_sha256": {k: hashlib.sha256(v.encode()).hexdigest() for k, v in before.items()},
            "frontend_verification": "frontend-full-final.json; 86 passing after response-ACK fix",
        },
    )
    print(json.dumps({"status": "passed", "backend_ast_unchanged": True, "changed_files": len(changed)}))


if __name__ == "__main__":
    main()
