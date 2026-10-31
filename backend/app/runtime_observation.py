"""Shared runtime interface and versioned controller-evidence validation."""

from app.execution import ExecutionError

INSTRUCTIONS = (
    "架构/任务interfaces明确权威状态所有者、真实输入和渲染路径、只读getState快照及受控场景准备接口；"
    "算法夹具标记isolated_logic，不冒充页面运行状态。场景准备走同一控制器，不修改生产算法/DOM制造通过。"
    "新环境提供await runtimeWitness(page,spec)：spec={getter:'window.app.getState',state_path:'count',"
    "action:{kind:'click',selector:'#increment'},view:{kind:'number',selector:'#count'}}。"
    "getter必须真实存在且返回同步快照，state_path为标量字段路径；press用key，fill用selector/value。"
    "view支持text/number/value/checked/canvas；前四种要求状态与真实页面投影前后严格相等，"
    "所有见证要求真实输入后状态和页面均改变。canvas仅证明相关变化，不能据此声称像素语义正确。"
    "见证不取代业务断言，须另以assert验证批准期待；暂停等不变行为先用可变操作证明接线，"
    "再按原测试真实验证冻结/恢复。不要为凑见证修改批准行为或引入无关输入。"
    "Reviewer按原AC区分runtime_ui与isolated_logic；辅助对象参与的UI证据必须有同case且匹配getter的可信成功见证。"
    "直接DOM验收不强制新增辅助API。范围标签/同次变化不是全部业务正确的证明，仍须源码与原AC独立核对。"
)


def contract():
    return {"contract": "runtime-observation-v1", "instructions": INSTRUCTIONS}


def validate_execution(inspected, execution, tests):
    from app.runtime_setup import validate_execution as validate_setup

    validate_setup(inspected, execution, tests)
    if inspected.get("runtime_observation_contract") != 1:
        return  # Historical images/reports retain their original evidence contract.
    rows = execution.get("results", [])
    if execution.get("runtime_observation_contract") != 1 or len(rows) != len(tests):
        raise ExecutionError("qa_runtime_observation_mismatch")
    for row in rows:
        proofs = row.get("runtime_witnesses")
        if not isinstance(proofs, list):
            raise ExecutionError("qa_runtime_observation_mismatch")
        for proof in proofs:
            if (
                proof.get("contract") != 1
                or proof.get("status") not in {"passed", "failed"}
                or row.get("status") == "passed"
                and proof["status"] != "passed"
                or proof["status"] == "passed"
                and (
                    not proof.get("before_hash")
                    or not proof.get("after_hash")
                    or proof["before_hash"] == proof["after_hash"]
                )
            ):
                raise ExecutionError("qa_runtime_observation_mismatch")


def coverage_error(branch, row, sources):
    if branch.status != "verified":
        return None
    if branch.observation_scope is None:
        return "observation_scope_required"
    if branch.observation_scope == "isolated_logic":
        return None  # Independent semantic review still rejects relabeling UI requirements.
    return binding_error(row, sources)


def binding_error(row, sources):
    helpers = set(sources.get("helper_globals", []))
    # A checkpoint can observe a helper, but can never establish its UI binding.
    helpers.update(
        c["getter"].replace("globalThis.", "window.", 1).rsplit(".", 1)[0]
        for p in row.get("setup_checkpoints", [])
        for c in p.get("checks", [])
        if c.get("kind") == "state" and isinstance(c.get("getter"), str)
    )
    verified = {
        p.get("getter", "").replace("globalThis.", "window.", 1).rsplit(".", 1)[0]
        for p in row.get("runtime_witnesses", [])
        if p.get("status") == "passed"
    }
    if not set(helpers).issubset(verified):
        return "runtime_binding_witness_required"
    return None


def validate_report(payload):
    inspected = payload.get("tools", {}).get("inspect", {}).get("result", {})
    if inspected.get("runtime_observation_contract") != 1:
        return
    from app.observation_sources import case_sources

    tests = payload.get("tests", [])
    validate_execution(inspected, payload.get("execution", {}), tests)
    if payload.get("runtime_observation_sources") != case_sources(inspected.get("files", {}), tests):
        raise ExecutionError("qa_runtime_observation_catalog_mismatch")
