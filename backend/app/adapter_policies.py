"""One source of truth for provider wire differences, independent of UI/SDK."""

from dataclasses import asdict, dataclass
from urllib.parse import urlsplit

# Bump whenever wire semantics or validation changes. Old capability checks must
# never silently certify a new adapter implementation.
ADAPTER_VERSION = "2026-10-19.5"


@dataclass(frozen=True)
class AdapterPolicy:
    id: str
    label: str
    thinking_parameter: str | None = None
    preserve_reasoning: bool = False

    def public(self):
        return {
            **asdict(self),
            "version": ADAPTER_VERSION,
            "supports_thinking": self.thinking_parameter is not None,
            "supports_thinking_budget": self.id == "bailian_qwen",
            "tool_choice": "auto",
            "structured_strategy": "validated_result_tool",
        }

    def extra_body(self, mode: str, thinking_budget=None):
        if mode != "provider_default" and (
            mode not in {"enabled", "disabled"} or self.thinking_parameter is None
        ):
            raise ValueError("该适配器不支持显式思考开关，请使用供应商默认")
        body = {}
        if mode != "provider_default":
            body = (
                {"enable_thinking": mode == "enabled"}
                if self.thinking_parameter == "enable_thinking"
                else {"thinking": {"type": mode}}
            )
        if thinking_budget is not None:
            if self.id != "bailian_qwen" or mode == "disabled":
                raise ValueError("当前适配器或关闭思考模式不支持思考 Token 预算")
            body["thinking_budget"] = thinking_budget
        return body or None


GENERIC = AdapterPolicy("openai_compatible", "通用 OpenAI 兼容（以实际检测为准）")
BAILIAN = AdapterPolicy("bailian_qwen", "百炼 Qwen", "enable_thinking", True)
DEEPSEEK = AdapterPolicy("deepseek", "DeepSeek 官方", "thinking", True)


def resolve_adapter(base_url: str, model_id: str) -> AdapterPolicy:
    host = (urlsplit(base_url).hostname or "").lower()
    if host == "api.deepseek.com" and model_id.lower().startswith("deepseek-"):
        return DEEPSEEK
    bailian = host in {
        "dashscope.aliyuncs.com",
        "dashscope-intl.aliyuncs.com",
        "dashscope-us.aliyuncs.com",
        "cn-hongkong.dashscope.aliyuncs.com",
    } or host.endswith(".maas.aliyuncs.com")
    if bailian and model_id.lower().startswith("qwen"):
        return BAILIAN
    return GENERIC
