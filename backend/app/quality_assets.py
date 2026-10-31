"""Bounded read-only screenshots from the exact published QA evidence archive."""

import hashlib
import io
import tarfile

from fastapi import HTTPException

from app.config import settings
from app.db import Candidate, QualityReport, Run
from app.workspace import project_root, safe_path


def screenshot(db, project_id, report_id, index):
    report = db.get(QualityReport, report_id)
    candidate = db.get(Candidate, report.candidate_id) if report else None
    run = db.get(Run, candidate.run_id) if candidate else None
    if not run or run.project_id != project_id or report.role != "qa" or not report.evidence_hash:
        raise HTTPException(404, "未找到此项目已保存的QA截图")
    execution = report.payload.get("execution", {})
    results = execution.get("results", [])
    expected = f"case-{index}.png"
    if (
        index >= len(results)
        or results[index].get("screenshot") != expected
        or execution.get("commit_sha") != report.commit_sha
        or candidate.commit_sha != report.commit_sha
    ):
        raise HTTPException(409, "截图未关联当前报告版本")
    path = safe_path(
        project_root(settings().workspace_root, project_id), f"reports/{report.id}/{report.evidence_hash}.tar"
    )
    try:
        if path.stat().st_size > settings().execution_snapshot_bytes:
            raise ValueError
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != report.evidence_hash:
            raise ValueError
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            matches = [
                item for item in archive if item.name.removeprefix("./") == "quality-artifacts/" + expected
            ]
            if len(matches) != 1 or not matches[0].isfile() or matches[0].size > 2 * 1024**2:
                raise ValueError
            content = archive.extractfile(matches[0]).read()
            if not content.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError
            return content
    except (OSError, ValueError, tarfile.TarError):
        raise HTTPException(409, "截图证据缺失或摘要不匹配，请核对报告") from None
