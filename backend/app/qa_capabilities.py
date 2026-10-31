"""An image-inspected sandbox contract, not an assumed full Node environment."""

from copy import deepcopy

from app.execution import ExecutionError

CONTRACT = "qa-sandbox-capabilities-v1"
BINDINGS = {
    "page",
    "context",
    "assert",
    "baseURL",
    "unitEvidence",
    "candidateSHA",
    "canvasMetrics",
    "runtimeWitness",
}


def descriptor(inspected):
    if "qa_capabilities_contract" not in inspected:
        return {
            "contract": "legacy-capabilities-unadvertised",
            "notice": "历史镜像未提供可验证的沙箱能力清单；不能假定宿主拥有完整Node或浏览器全局，按原镜像实际接口核对。",
        }
    value = inspected.get("qa_capabilities")
    version = inspected["qa_capabilities_contract"]
    bindings = BINDINGS | ({"runtimeCheckpoint"} if version == 2 else set())
    if (
        type(version) is not int
        or version not in {1, 2}
        or not isinstance(value, dict)
        or value.get("contract") != f"qa-sandbox-capabilities-v{version}"
        or not isinstance(value.get("entry_bindings"), list)
        or any(not isinstance(v, str) for v in value["entry_bindings"])
        or len(value["entry_bindings"]) != len(bindings)
        or set(value["entry_bindings"]) != bindings
        or not all(
            isinstance(value.get(k), dict)
            and value[k]
            and all(isinstance(a, str) and isinstance(b, str) for a, b in value[k].items())
            for k in ("return_types", "unavailable_host_globals")
        )
    ):
        raise ExecutionError("quality_capability_contract_invalid")
    return deepcopy(value)
