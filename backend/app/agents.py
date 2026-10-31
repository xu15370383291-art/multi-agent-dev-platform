from dataclasses import dataclass

from app.domain import ArchitectureResult, PRDResult, Role, SupervisorResult


@dataclass(frozen=True)
class AgentSpec:
    role: Role
    purpose: str
    tools: tuple[str, ...]
    connected: bool
    output_schema: type | None = None


REGISTRY = {
    Role.SUPERVISOR: AgentSpec(Role.SUPERVISOR, "规划阶段与协调任务", (), True, SupervisorResult),
    Role.PRODUCT: AgentSpec(Role.PRODUCT, "生成需求与可追踪验收标准", (), True, PRDResult),
    Role.ARCHITECT: AgentSpec(Role.ARCHITECT, "生成架构和有效任务 DAG", (), True, ArchitectureResult),
    Role.WEB: AgentSpec(
        Role.WEB,
        "执行 Web 开发任务",
        ("read_file", "write_file", "write_files", "run_command", "git", "prepare_dependencies"),
        True,
    ),
    Role.GAME: AgentSpec(
        Role.GAME,
        "执行浏览器游戏开发任务",
        ("read_file", "write_file", "write_files", "run_command", "git", "prepare_dependencies"),
        True,
    ),
    Role.QA: AgentSpec(
        Role.QA,
        "固定候选的浏览器验收与版本化测试资产",
        ("inspect_candidate", "validate_tests", "run_tests"),
        True,
    ),
    Role.REVIEWER: AgentSpec(
        Role.REVIEWER, "固定候选的独立只读审查（含源码与基线Diff）", ("inspect_candidate",), True
    ),
}
