"""Independent checks of the approved C1 sample in its pinned Docker environment."""

import json
import re
import sys
from pathlib import Path

from app.db import Candidate, Run, Session, new_id
from app.execution import DockerSandbox
from app.quality import snapshot_for


def main(candidate_id):
    with Session() as db:
        candidate = db.get(Candidate, candidate_id)
        assert candidate and candidate.status == "build_passed"
        _, snapshot = snapshot_for(candidate, db.get(Run, candidate.run_id))
        image_id = candidate.environment["image_id"]
        sha = candidate.commit_sha
    # No pipe/tail or command separators masking test failures. Business code runs only in Docker.
    result, _ = DockerSandbox(new_id(), expected_image_id=image_id, image_name=image_id).run(
        "run_command",
        {
            "command": "node --experimental-strip-types --test tests/*.test.ts && npm run build && git rev-parse HEAD"
        },
        snapshot,
    )
    output = result.get("stdout", "")
    assert result.get("ok") and result.get("exit_code") == 0 and not result.get("truncated"), result
    count = re.search(r"^# tests (\d+)$", output, re.MULTILINE)
    assert count and int(count[1]) > 0
    for key in ("fail", "cancelled", "skipped", "todo"):
        assert re.search(r"^# " + key + r" 0$", output, re.MULTILINE)
    assert output.rstrip().endswith(sha)
    evidence = {
        "status": "passed",
        "candidate_id": candidate_id,
        "commit_sha": sha,
        "tests": int(count[1]),
        "image_id": image_id,
        "execution": result,
    }
    target = Path(__file__).resolve().parents[2] / ".local" / "c1-acceptance" / "independent-checks.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in evidence.items() if k != "execution"}, indent=2))


if __name__ == "__main__":
    main(sys.argv[1])
