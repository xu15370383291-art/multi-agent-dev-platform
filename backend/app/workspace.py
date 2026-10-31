from pathlib import Path


def project_root(base: Path, project_id: str) -> Path:
    if not project_id or project_id in {".", ".."} or any(char in project_id for char in "/\\:"):
        raise ValueError("非法工作区标识")
    candidate = base.resolve() / project_id
    root = candidate.resolve()
    if (
        root.parent != base.resolve()
        or root != candidate
        or candidate.is_symlink()
        or candidate.is_junction()
    ):
        raise ValueError("非法工作区标识")
    return root


def safe_path(root: Path, relative: str) -> Path:
    root = root.resolve()
    path = Path(relative)
    if path.is_absolute() or path.drive or not relative or ":" in relative:
        raise ValueError("仅允许项目内相对路径")
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root) or resolved == root:
        raise ValueError("路径超出项目工作区")
    return resolved


def create_workspace(base: Path, project_id: str) -> Path:
    root = project_root(base, project_id)
    root.mkdir(parents=True, exist_ok=False)
    for name in ["repo", "worktrees", "artifacts", "reports", "executions", "tmp"]:
        (root / name).mkdir()
    return root
