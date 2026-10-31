"""Actionable, allowlisted diagnostics. Never return provider exception text."""

from app.diagnostic_values import error_type
from app.model_adapter import ModelContractError

MESSAGES = {
    "invalid_tool_selection": "模型未返回唯一、有效的指定结果工具调用；未执行工具或尝试降级。",
    "invalid_or_truncated_result": "结果工具名称不匹配或输出被截断，请检查模型能力和输出预算。",
    "schema_validation_failed": "工具参数未通过角色结构校验，不能作为有效结果。",
    "chat_reply_mismatch": "普通对话未按测试要求返回 ready。",
    "tool_arguments_mismatch": "计算工具参数不符合测试要求。",
    "tool_return_invalid": "工具结果回传后未得到正确最终答案，或模型再次调用了工具。",
    "unknown_model_contract": "模型结果合同未被平台识别，未执行降级或自动重试。",
}


def diagnose(exc: Exception) -> dict:
    result = {"error_type": error_type(exc)}
    code = "provider_error"
    message = "供应商调用失败；原始响应未回显，请检查服务状态、模型权限和连接配置。"
    status = getattr(exc, "status_code", None)
    status = status if type(status) is int else None
    # Inspect only to classify, never echo arbitrary provider text, headers or IDs.
    text = str(exc).lower()
    body = getattr(exc, "body", None)
    error = body.get("error", body) if isinstance(body, dict) else None
    provider_code = error.get("code") if isinstance(error, dict) else None
    provider_code = provider_code if isinstance(provider_code, str) else None
    if isinstance(exc, ModelContractError):
        code, message = exc.code, MESSAGES[exc.code]
        if exc.response_summary:
            result["response_summary"] = exc.response_summary
    elif status in {403, 429} and provider_code in {
        "AllocationQuota.FreeTierOnly",
        "BudgetLimitExceeded",
        "PrepaidBillOverdue",
        "PostpaidBillOverdue",
    }:
        code, message = (
            "provider_quota_exhausted",
            "模型服务报告可用额度不足或计费受限，请检查该模型的额度和计费设置。",
        )
    elif status in {401, 403}:
        code, message = (
            "authentication_or_permission",
            "密钥认证或模型权限失败，请核对供应商账户、地域和模型授权。",
        )
    elif status == 429:
        code, message = "quota_or_rate_limit", "供应商额度或速率限制，请检查余额和调用限额。"
        # Only structured, exact provider codes distinguish balance from throttling.
        # Do not infer account state from arbitrary exception prose or save the body.
        if provider_code in {
            "rate_limit_exceeded",
            "Throttling",
            "Throttling.RateQuota",
            "LimitRequests",
            "limit_requests",
            "Throttling.BurstRate",
            "limit_burst_rate",
        }:
            code, message = "provider_rate_limited", "模型服务暂时限制了请求速度，请稍后检查并继续。"
    elif "reasoning_content" in text and status == 400:
        code, message = (
            "reasoning_history_required",
            "供应商要求完整回传推理字段，当前消息链不符合协议，适配未通过。",
        )
    elif "tool_choice" in text and status == 400:
        code, message = (
            "tool_choice_rejected",
            "供应商拒绝工具选择参数，当前适配未通过；不会自动关闭思考重试。",
        )
    elif status == 400:
        code, message = (
            "request_parameters_rejected",
            "供应商拒绝请求参数，请核对模型 ID、接口版本及所选思考模式。",
        )
    elif "timeout" in type(exc).__name__.lower():
        code, message = "timeout", "模型请求超时，未自动重试或切换模式。"
    elif type(exc).__name__ in {"APIConnectionError", "ConnectionError", "ConnectError", "ReadError"}:
        code, message = "connection_error", "模型连接中断，原请求结果未确认；未自动重试。"
    elif status is not None and 500 <= status <= 599:
        code, message = "provider_unavailable", "供应商服务暂时不可用，请稍后重新检测。"
    result.update(error_code=code, message=message)
    if isinstance(status, int):
        result["http_status"] = status
    return result
