"""Explicit read-only diagnostic; never writes acceptance reports or invokes a model."""

import json
import re
import sys
from pathlib import Path

from app.config import settings
from app.db import Candidate, QualityReport, Run, Session, new_id
from app.execution import DockerSandbox
from app.quality import snapshot_for

with Session() as db:
    report = db.get(QualityReport, sys.argv[1])
    candidate = db.get(Candidate, report.candidate_id)
    _, snapshot = snapshot_for(candidate, db.get(Run, candidate.run_id))
    tests = json.loads(json.dumps(report.payload["tests"]))
    if "--trace" in sys.argv:
        script = tests[3]["script"]
        script = re.sub(
            r"await (browserObj\.newContext\([^\n]+|context\w+\.(?:newPage|close)\([^\n]+|page\w+\.(?:goto|waitForFunction)\([^\n]+);",
            lambda match: "await trace(" + json.dumps(match.group(1)[:100]) + ", " + match.group(1) + ");",
            script,
        )
        tests[3]["script"] = (
            "async function trace(label,promise){let timer;try{return await Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error(label)),2000)})])}finally{clearTimeout(timer)}}\n"
            + script
        )
    image = next(
        (arg.split("=", 1)[1] for arg in sys.argv if arg.startswith("--image=")), settings().quality_image
    )
    result, _ = DockerSandbox(new_id(), image_name=image).run(
        "run_tests", {"expected_sha": candidate.commit_sha, "tests": tests}, snapshot
    )
    path = Path("../.local/quality-diagnostic.json")
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": result.get("ok"),
                "results": result.get("results"),
                "environment": result.get("environment"),
            },
            ensure_ascii=False,
        )
    )
