from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="COMPANY_", env_file=".env", extra="ignore")
    database_url: str = Field(
        default="postgresql+psycopg://company:company_local@127.0.0.1:55432/company", repr=False
    )
    workspace_root: Path = Path(__file__).resolve().parents[2] / ".local" / "projects"
    allowed_origins: list[str] = ["http://127.0.0.1:5173", "http://localhost:5173"]
    preview_base_url: str = "http://127.0.0.1:8001"
    model_timeout_seconds: int = 90
    max_output_tokens: int = 4096
    run_max_model_calls: int = 60
    run_call_policy: Literal["fixed", "progress_guarded"] = "progress_guarded"
    auto_correction_enabled: bool = True
    auto_resume_paused_runs: bool = True
    run_max_seconds: int = 3600
    max_document_revisions: int = 5
    execution_image: str = "agent-company-executor:m3"
    progress_execution_image: str = "agent-company-executor:m4"
    quality_image: str = "agent-company-quality:m16"
    progress_quality_image: str = "agent-company-quality:m16"
    tool_idle_seconds: int = 180
    execution_memory_mb: int = 768
    execution_workspace_mb: int = 256
    execution_cpus: float = 1.0
    execution_pids: int = 128
    tool_timeout_seconds: int = 60
    task_max_tool_calls: int = 80
    tool_output_bytes: int = 65536
    execution_snapshot_bytes: int = 64 * 1024 * 1024
    execution_archive_bytes: int = 512 * 1024 * 1024
    development_concurrency: int = 2
    execution_total_slots: int = 2
    # Legacy fixed-mode admission has its own wait policy. Command runtime
    # must start after admission; progress-mode waits remain uncapped/cancellable.
    execution_admission_wait_seconds: int = 60
    reviewed_quality_corrections_enabled: bool = True
    # Approved 2026-10-25 (docs/60); deployments must explicitly opt in.
    supplemental_verification_enabled: bool = False

    @property
    def checkpoint_url(self) -> str:
        return self.database_url.replace("postgresql+psycopg://", "postgresql://", 1)


@lru_cache
def settings() -> Settings:
    return Settings()
