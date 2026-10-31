"""Fresh unit tests and builds of immutable final business SHAs, only in pinned Docker."""

import json
import re
from pathlib import Path

from app.db import Candidate, Run, Session, new_id, now
from app.execution import DockerSandbox
from app.quality import snapshot_for

OUT = Path(__file__).resolve().parents[2] / ".local/c6-acceptance"


def main():
    audit = json.loads((OUT / "history-audit.json").read_text(encoding="utf-8"))
    records = []
    for label in ("m1_game", "c1_web", "parallel_web"):
        with Session() as db:
            candidate = db.get(Candidate, audit["runs"][label]["candidate_id"])
            _, snapshot = snapshot_for(candidate, db.get(Run, candidate.run_id))
            image = candidate.environment["image_id"]
            sha = candidate.commit_sha
        result, _ = DockerSandbox(new_id(), expected_image_id=image, image_name=image).run(
            "run_command", {"command": "npm test && npm run build && git rev-parse HEAD"}, snapshot
        )
        output = result.get("stdout", "")
        assert result.get("ok") and result.get("exit_code") == 0 and not result.get("truncated"), result
        count = re.search(r"^# tests (\d+)$", output, re.MULTILINE)
        assert count and int(count[1]) > 0
        for key in ("fail", "cancelled", "skipped", "todo"):
            assert re.search(r"^# " + key + r" 0$", output, re.MULTILINE)
        assert output.rstrip().endswith(sha)
        records.append(
            {
                "label": label,
                "candidate_id": candidate.id,
                "commit_sha": sha,
                "image_id": image,
                "tests": int(count[1]),
                "execution": result,
            }
        )
        (OUT / "fresh-business-checks-partial.json").write_text(
            json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    (OUT / "fresh-business-checks.json").write_text(
        json.dumps(
            {"status": "passed", "time": now().isoformat(), "records": records}, ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )
    print(json.dumps({"status": "passed", "tests": {r["label"]: r["tests"] for r in records}}))


if __name__ == "__main__":
    main()
