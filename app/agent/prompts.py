"""Agent 提示与工具 schema 暴露。

对齐 docs/AGENT_DESIGN.md 第 3 节：Agent 通过工具 schema 把四个核验工具暴露给
模型，模型据此选择调用哪个工具。本模块集中管理系统提示、初始任务消息与工具 schema，
便于阶段 3 接入真实 OpenAI 兼容客户端时复用。

注意：schema 仅用于"告诉模型有哪些工具、需要什么参数"，真正的参数校验与执行由
执行器（runner）和工具注册入口（registry）完成，不信任模型提供的参数。
"""

from __future__ import annotations

from app.schemas import contracts

SYSTEM_PROMPT = (
    "你是 DeliveryGate AI 的交付前置核验 Agent。你的职责是依据交付资料，"
    "从可用的核验工具中选择下一个要调用的工具，并在所有必需检查完成后给出解释性总结。"
    "你只能调用白名单内的核验工具；每个工具接收一份交付资料（delivery）。"
    "你不负责决定最终就绪状态——READY/NEEDS_REVIEW/BLOCKED 由确定性规则计算。"
)

# 四个核验工具的参数 schema（最小可用）：每个工具都接收一个 delivery 对象。
_TOOL_DESCRIPTIONS = {
    contracts.TOOL_CHECK_REQUIRED_FIELDS: "检查交付必填信息是否完整",
    contracts.TOOL_COMPARE_ENVIRONMENT: "比较目标环境要求与实际部署配置",
    contracts.TOOL_COMPARE_API_ACCEPTANCE: "比较接口契约与验收检查项的一致性",
    contracts.TOOL_INSPECT_ACCEPTANCE_COVERAGE: "检查关键验收要求是否有对应验证步骤",
}


def build_tool_schemas() -> list[dict]:
    """返回暴露给模型的工具 schema 列表（OpenAI tools 风格的最小结构）。"""
    schemas = []
    for name in contracts.ALL_TOOLS:
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": _TOOL_DESCRIPTIONS[name],
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "delivery": {
                                "type": "object",
                                "description": "交付资料对象（含 target_environment、"
                                "actual_deployment、api_contracts、"
                                "acceptance_criteria、verification_steps 等字段）",
                            }
                        },
                        "required": ["delivery"],
                    },
                },
            }
        )
    return schemas


def build_initial_messages(delivery: dict, required_tools: list[str]) -> list[dict]:
    """构造初始对话：system 提示 + 一个 user 消息。

    user 消息内容为 JSON，内含 delivery 与 required_tools，便于模拟客户端解析；
    阶段 3 真实客户端可将其作为任务上下文。required_tools 由应用层任务规则确定，
    不由模型生成或修改。
    """
    import json

    payload = {"delivery": delivery, "required_tools": list(required_tools)}
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
