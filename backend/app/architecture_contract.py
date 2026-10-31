"""Reject provable build-order contradictions; never infer arbitrary prose dependencies."""

import posixpath
import re
from fnmatch import fnmatchcase
from pathlib import PurePosixPath


def validate_build_order(tasks, available_tasks=()):
    if len(tasks) < 2:
        return
    by_id = {t.id: t for t in tasks}
    available_tasks = set(available_tasks)
    roots = [t for t in tasks if not t.depends_on]
    # Only explicit entry ownership proves this constraint. Legacy documents
    # without declared outputs are not silently assigned a fabricated owner.
    entry_available = any(t.id in available_tasks and "index.html" in t.expected_outputs for t in tasks)
    if not entry_available and any("index.html" in t.expected_outputs for t in tasks):
        missing = [t.id for t in roots if not any(p == "." or fnmatchcase("index.html", p) for p in t.scope)]
        if missing:
            raise ValueError(
                "architecture_entrypoint_order_required: 每次合并后必须可构建；根任务"
                + ",".join(missing)
                + "无权创建index.html。请先提供不引用未实现文件的可构建骨架，再让独立模块依赖骨架，最后集成；不得私自扩大模块文件范围。"
            )
    owners = {}
    for task in tasks:
        for output in task.expected_outputs:
            owners.setdefault(posixpath.normpath(output), set()).add(task.id)

    def ancestors(task):
        result, pending = set(), list(task.depends_on)
        while pending:
            identity = pending.pop()
            if identity not in result:
                result.add(identity)
                pending.extend(by_id[identity].depends_on)
        return result

    for task in tasks:
        refs = re.findall(r"\b(?:from|import)\s+['\"]?(\.{1,2}/[\w./-]+)", "\n".join(task.interfaces))
        for ref in refs:
            # A relative import may be described alongside several output files.
            # Reject only a unique other owner; ambiguous prose stays unproven.
            candidates = set()
            for output in task.expected_outputs:
                path = posixpath.normpath(str(PurePosixPath(output).parent / ref))
                candidates.update(owners.get(path, set()))
            if len(candidates) == 1:
                owner = next(iter(candidates))
                if owner != task.id and owner not in available_tasks and owner not in ancestors(task):
                    raise ValueError(
                        f"architecture_import_dependency_required: {task.id}明确引用{ref}，但未依赖产出该文件的{owner}；请修正结构化depends_on并同步架构正文，不扩大功能范围。"
                    )
