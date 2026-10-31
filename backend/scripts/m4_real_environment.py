"""Isolated real-model acceptance services using existing protected model configuration.

No API key is read, exported or copied by setup. Credential references remain in PG;
runtime model adapters use Windows Credential Manager in their normal code path.
"""

import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import MetaData, Table, create_engine, select, text
from sqlalchemy.engine import make_url

from app.config import settings

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / ".local/m4-acceptance/real-environment.json"
SCHEMA = "m4_real_20261021"


def configure():
    data = json.loads(STATE.read_text(encoding="utf8"))
    if not re.fullmatch(r"m4_real_[0-9]{8}", data["schema"]):
        raise RuntimeError("dedicated_acceptance_schema_required")
    original = settings()
    url = make_url(original.database_url)
    if (url.host, url.port, url.database) != ("127.0.0.1", 55432, "company"):
        raise RuntimeError("expected_project_database_required")
    options = dict(url.query)
    options["options"] = "-csearch_path=" + data["schema"]
    os.environ["COMPANY_DATABASE_URL"] = url.set(query=options).render_as_string(hide_password=False)
    os.environ["COMPANY_WORKSPACE_ROOT"] = data["workspace_root"]
    os.environ["COMPANY_PREVIEW_BASE_URL"] = f"http://127.0.0.1:{data['preview_port']}"
    os.environ["COMPANY_ALLOWED_ORIGINS"] = json.dumps(["http://127.0.0.1:5174", "http://127.0.0.1:5173"])
    settings.cache_clear()
    return data


def prepare():
    if STATE.exists():
        data = configure()
        print(json.dumps({"schema": data["schema"], "reused": True, "created_new_projects": 0}))
        return
    public = create_engine(settings().database_url)
    with public.begin() as connection:
        if connection.scalar(text("SELECT current_schema()")) != "public":
            raise RuntimeError("public_configuration_source_required")
        if connection.scalar(
            text("SELECT count(*) FROM information_schema.schemata WHERE schema_name=:schema"),
            {"schema": SCHEMA},
        ):
            raise RuntimeError("unregistered_acceptance_schema_requires_inspection")
        profiles = Table("model_profiles", MetaData(), autoload_with=connection)
        bindings = Table("model_bindings", MetaData(), autoload_with=connection)
        profile_rows = [dict(row) for row in connection.execute(select(profiles)).mappings()]
        binding_rows = [
            dict(row)
            for row in connection.execute(select(bindings).where(bindings.c.scope == "global")).mappings()
        ]
        if not profile_rows or not binding_rows:
            raise RuntimeError("existing_verified_configuration_required")
        connection.execute(text(f'CREATE SCHEMA "{SCHEMA}"'))
    data = {
        "schema": SCHEMA,
        "api_port": 8014,
        "preview_port": 8015,
        "workspace_root": str(ROOT / ".local/m4-acceptance/real-projects"),
        "created_at": datetime.now(UTC).isoformat(),
        "max_real_projects": 2,
        "source_configuration": "existing public profiles and global bindings; credential references only",
    }
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf8")
    configure()
    from alembic import command
    from alembic.config import Config

    from app.db import ModelBinding, ModelProfile, engine

    config = Config(str(ROOT / "backend/alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend/migrations"))
    command.upgrade(config, "head")
    with engine.begin() as connection:
        connection.execute(ModelProfile.__table__.insert(), profile_rows)
        connection.execute(ModelBinding.__table__.insert(), binding_rows)
    public.dispose()
    print(
        json.dumps(
            {
                "schema": SCHEMA,
                "copied_profile_revisions": len(profile_rows),
                "copied_global_bindings": len(binding_rows),
                "created_new_projects": 0,
                "api_keys_exported": 0,
            }
        )
    )


def main():
    component = sys.argv[1] if len(sys.argv) > 1 else "inspect"
    if component == "prepare":
        prepare()
        return
    data = configure()
    receipts = STATE.parent / "real-processes"
    receipts.mkdir(exist_ok=True)
    (receipts / f"{component}-{os.getpid()}.json").write_text(
        json.dumps(
            {
                "component": component,
                "pid": os.getpid(),
                "schema": data["schema"],
                "created_at": datetime.now(UTC).isoformat(),
                "worker_mode": "once" if component == "worker" else None,
            }
        ),
        encoding="utf8",
    )
    if component == "api":
        import uvicorn

        uvicorn.run("app.api:app", host="127.0.0.1", port=data["api_port"], access_log=False)
    elif component == "preview":
        import uvicorn

        uvicorn.run("app.preview:app", host="127.0.0.1", port=data["preview_port"], access_log=False)
    elif component == "worker":
        from app.worker import main as run_worker

        sys.argv = [sys.argv[0], "--once"]
        run_worker()
    elif component == "inspect":
        print(
            json.dumps(
                {
                    "schema": data["schema"],
                    "api_port": data["api_port"],
                    "preview_port": data["preview_port"],
                    "max_real_projects": 2,
                }
            )
        )
    else:
        raise RuntimeError("unsupported_acceptance_component")


if __name__ == "__main__":
    main()
