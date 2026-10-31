"""Read-only export of verified built bytes for independent failure diagnosis."""

import argparse
import hashlib
import io
import re
import tarfile
import uuid
from pathlib import Path

from scripts.m5_real_environment import configure

configure()

from app.db import Candidate, Run, Session
from app.maintenance import atomic_json, validate_relative
from app.quality import snapshot_for


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", default="6dc293dd-6c19-4d28-8393-ff217e838dff")
    parser.add_argument("--label", default="candidate-probe")
    args = parser.parse_args()
    identity = str(uuid.UUID(args.candidate))
    assert re.fullmatch(r"[a-z][a-z0-9-]{0,50}", args.label)
    target = Path(__file__).resolve().parents[2] / ".local/m5-acceptance" / args.label
    with Session() as db:
        candidate = db.get(Candidate, identity)
        run = db.get(Run, candidate.run_id)
        _, snapshot = snapshot_for(candidate, run)
        assert hashlib.sha256(snapshot).hexdigest() == candidate.build_snapshot_hash
        sha = candidate.commit_sha
    files = {}
    with tarfile.open(fileobj=io.BytesIO(snapshot)) as archive:
        for member in archive.getmembers():
            name = member.name.removeprefix("./")
            if not name.startswith("dist/"):
                continue
            if member.isdir():
                continue
            assert member.isfile() and member.size <= 2 * 1024 * 1024
            name = name[5:]
            validate_relative(name)
            assert name not in files
            files[name] = archive.extractfile(member).read()
    assert "index.html" in files and sum(map(len, files.values())) < 64 * 1024 * 1024
    target.mkdir(exist_ok=True)
    for name, data in files.items():
        path = target / "web" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            assert path.read_bytes() == data
        else:
            path.write_bytes(data)
    atomic_json(
        target / "manifest.json",
        {
            "candidate_id": identity,
            "commit_sha": sha,
            "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
        },
    )
    print({"candidate": identity, "files": len(files), "read_only": True})


if __name__ == "__main__":
    main()
