"""Validate controller-owned setup observations; never infer business intent."""

import re
from types import SimpleNamespace

from app.execution import ExecutionError

INSTRUCTIONS = (
    "测试准备须声明实际对象和目标状态，不能用发生过变化代替目标已建立。"
    "runtimeWitness支持purpose:'setup'、expect:{before:0,after:1}及"
    "action.target:{attribute:'data-id',equals:'目标ID',ancestor_selector:'li'}；"
    "selector仍须唯一，操作锁定核对过的元素，不猜测筛选后的序号。"
    "每个依赖准备状态的断言前，await runtimeCheckpoint(page,{checks:["
    "{kind:'state',getter:'window.app.getState',state_path:'done',equals:2},"
    "{kind:'dom',view:{kind:'number',selector:'#done'},equals:2}]})，"
    "只读分组核对目标，可验证不变状态；不算业务断言，也不替代runtimeWitness接线见证。"
    "中文文案与英文状态不能直接作相等投影，选择真实同义的checked/value/数字投影；"
    "没有合同支持的投影就分别核对实际状态与DOM，不能伪造辅助状态。"
    "独立审查须将前置检查锚点填入support_links.preconditions，确认同case实际成功且早于所支持断言。"
    "还须核对检查与断言之间是否有再次改变状态的操作，旧检查不能证明后来的状态。"
    "空白独立上下文可无额外前置；实际需要准备却缺证据应not_verified。"
    "准备未达标只说明测试尚未建立所需状态，不直接证明业务源码有错；保留原断言并核对原因。"
)


def _site(value):
    return isinstance(value, dict) and all(
        type(value.get(k)) is int and value[k] > 0 for k in ("line", "column")
    )


def validate_execution(inspected, execution, tests):
    if inspected.get("runtime_setup_contract") != 1:
        return
    if type(execution.get("runtime_setup_contract")) is not int or execution["runtime_setup_contract"] != 1:
        raise ExecutionError("qa_runtime_setup_mismatch")
    rows = execution.get("results")
    if not isinstance(rows, list) or len(rows) != len(tests):
        raise ExecutionError("qa_runtime_setup_mismatch")
    for row, test in zip(rows, tests, strict=True):
        sequence = set()
        for name in ("setup_checkpoints", "assertion_events", "runtime_witnesses"):
            events = row.get(name)
            if not isinstance(events, list):
                raise ExecutionError("qa_runtime_setup_mismatch")
            for event in events:
                if not isinstance(event, dict):
                    raise ExecutionError("qa_runtime_setup_mismatch")
                seq, site = event.get("sequence"), event.get("site")
                lines = test["script"].split("\n")
                if (
                    type(seq) is not int
                    or seq < 1
                    or seq in sequence
                    or event.get("status") not in {"passed", "failed"}
                    or (row.get("status") == "passed" and event["status"] != "passed")
                    or not _site(site)
                    or site["line"] > len(lines)
                    or site["column"] > len(lines[site["line"] - 1].encode("utf-16-le")) // 2 + 1
                ):
                    raise ExecutionError("qa_runtime_setup_mismatch")
                sequence.add(seq)
                if name == "setup_checkpoints":
                    checks = event.get("checks")
                    if event.get("contract") != "runtime-setup-checkpoint-v1" or not isinstance(checks, list):
                        raise ExecutionError("qa_runtime_setup_mismatch")
                    if event["status"] == "passed" and not checks:
                        raise ExecutionError("qa_runtime_setup_mismatch")
                    for index, check in enumerate(checks):
                        if (
                            not isinstance(check, dict)
                            or check.get("index") != index
                            or check.get("kind") not in {"state", "dom"}
                            or type(check.get("passed")) is not bool
                            or any(
                                not re.fullmatch(r"[a-f0-9]{64}", str(check.get(k)))
                                for k in ("expected_hash", "actual_hash")
                            )
                            or check["passed"] != (check["expected_hash"] == check["actual_hash"])
                            or event["status"] == "passed"
                            and not check["passed"]
                        ):
                            raise ExecutionError("qa_runtime_setup_mismatch")


def prepared_before(qa, case_index, anchor, evidence):
    """A static setup anchor alone is not proof that the state was established."""
    from app.requirement_evidence import contains

    row = qa["execution"]["results"][case_index]
    assertions = [
        e["sequence"]
        for e in row.get("assertion_events", [])
        if e.get("status") in {"passed", "failed"}
        and _site(e.get("site"))
        and any(
            r.case_index == case_index and e["site"] == {"line": r.line, "column": r.column} for r in evidence
        )
    ]
    return bool(assertions) and any(
        p.get("status") == "passed"
        and _site(p.get("site"))
        and contains(anchor, SimpleNamespace(**p["site"]))
        and type(p.get("sequence")) is int
        and p["sequence"] < min(assertions)
        for p in row.get("setup_checkpoints", [])
    )
