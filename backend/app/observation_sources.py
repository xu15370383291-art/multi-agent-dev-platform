"""Source-bound observation warnings, without executing or guessing application state."""

import hashlib
import json
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from app.execution import ExecutionError

INSTRUCTIONS = (
    "测试辅助对象的状态不自动等于实际页面/游戏状态。object_owned_state表示源码getState读取对象自身字段，"
    "并未证明它与输入事件、生产循环或DOM共享状态。纯算法可使用该接口；键盘/暂停/计分UI验收须以真实DOM/Canvas变化为证据，"
    "或提供源码明确接线及实际输入前后变化的证明。不能以辅助对象的空piece判真实游戏无方块，不能以其恒定坐标证明暂停。"
    "场景前置须可达：随机重复操作不保证消行/计分；使用可复现输入路径，不得写DOM/替换生产实现制造通过。"
    "断言不得自比较，不把矩阵空白填充行当实际占用边界。源码摘录仅说明读取来源，不是功能通过或源码缺陷判定。"
)


def contract(files):
    parser = Path(__file__).resolve().parents[1] / "scripts/inspect_observation_sources.cjs"
    node = shutil.which("node")
    if not node:
        raise ExecutionError("quality_observation_parser_unavailable")
    payload = json.dumps(files, sort_keys=True, ensure_ascii=False)
    sources = _inspect(node, str(parser), parser.stat().st_mtime_ns, payload)
    return {
        "contract": "source-bound-observation-v1",
        "source_hash": hashlib.sha256(payload.encode()).hexdigest(),
        "sources": json.loads(sources),
        "instructions": INSTRUCTIONS,
        "limits": "仅识别直接全局对象声明；未识别不代表已证明运行时接线，仍须独立核对真实观察来源。",
    }


def case_sources(files, tests):
    """Conservative source use inventory; never a proof of semantic UI coverage."""
    parser = Path(__file__).resolve().parents[1] / "scripts/inspect_observation_sources.cjs"
    node = shutil.which("node")
    if not node:
        raise ExecutionError("quality_observation_parser_unavailable")
    payload = json.dumps(
        {"mode": "case-observations-v1", "files": files, "tests": tests},
        sort_keys=True, ensure_ascii=False,
    )
    return json.loads(_inspect(node, str(parser), parser.stat().st_mtime_ns, payload))


@lru_cache(maxsize=64)
def _inspect(node, parser, revision, payload):
    try:
        result = subprocess.run(
            [node, parser], input=payload, text=True, capture_output=True, timeout=10, check=True
        )
        json.loads(result.stdout)
        return result.stdout
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise ExecutionError("quality_observation_parser_unavailable") from exc
