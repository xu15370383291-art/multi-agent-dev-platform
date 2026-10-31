"""Schema corrections use alternative advertised tools, never argument coercion."""

from app.developer import usable_tools
from app.tool_gateway import TOOLS, ToolGateway
from tests.test_m2_resume import inspect, resume, setup_checkpoint


def test_repeated_batch_shape_rejections_select_single_file_contract_across_resume(task_context, storage):
    run_id, task_id, owner, _ = task_context
    tools = [
        {"type": "function", "function": {"name": name, "parameters": schema.model_json_schema()}}
        for name, schema in TOOLS.items()
    ]
    assert usable_tools(task_id, tools) == (tools, "")
    gateway = ToolGateway(run_id, task_id, owner)
    for index in range(2):
        gateway.reject_arguments(
            f"invalid-batch-{index}",
            "write_files",
            {"path": "index.html", "content": "fixture"},
            [{"loc": ["files"], "type": "missing", "msg": "Field required"}],
        )
    run_id, task_id = setup_checkpoint(storage, task_context)
    result = resume(storage, run_id, inspect(storage, run_id))
    from sqlalchemy import select

    from app.db import TaskExecution

    with storage[0]() as db:
        child = db.scalar(select(TaskExecution).where(TaskExecution.parent_execution_id == task_id))
        child_id = child.id
        assert child.tool_calls == 2
    selected, instruction = usable_tools(child_id, tools)
    assert "write_file" in instruction and "write_files" in instruction
    assert selected == [item for item in tools if item["function"]["name"] != "write_files"]
    assert (
        next(item for item in selected if item["function"]["name"] == "write_file")["function"]["parameters"]
        == TOOLS["write_file"].model_json_schema()
    )
    assert result["work_item_id"]
