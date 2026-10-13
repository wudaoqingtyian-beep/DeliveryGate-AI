"""工具注册入口：白名单查找与统一调用。

对齐 docs/AGENT_DESIGN.md 第 5 节：
- 未知工具名 → UNKNOWN_TOOL
- 无效工具参数（不符合契约）→ INVALID_PARAMS
- 工具执行期异常 → 由工具/本入口兜底为 TOOL_ERROR，绝不抛出中断上层

安全约束（REQUIREMENTS.md 第 2 节）：
- 仅暴露白名单中的四个工具函数，不允许通过参数指定任意函数或导入路径。
- 不执行任意 SQL、Shell、文件系统或生产环境操作——工具本身只读交付资料。

本阶段仅提供注册与调用，不实现模型工具调用循环。
"""

from __future__ import annotations

from app.schemas import contracts
from app.tools import (
    acceptance_check,
    environment_check,
    required_fields,
)

# 白名单：工具名 → 纯函数。键集合即唯一允许被调用的工具。
_REGISTRY = {
    contracts.TOOL_CHECK_REQUIRED_FIELDS: required_fields.check_required_fields,
    contracts.TOOL_COMPARE_ENVIRONMENT: environment_check.compare_environment_config,
    contracts.TOOL_COMPARE_API_ACCEPTANCE: acceptance_check.compare_api_acceptance,
    contracts.TOOL_INSPECT_ACCEPTANCE_COVERAGE: (
        acceptance_check.inspect_acceptance_coverage
    ),
}


def list_tools() -> list[str]:
    """返回允许调用的工具名列表（固定顺序）。"""
    return list(contracts.ALL_TOOLS)


def has_tool(name: str) -> bool:
    return name in _REGISTRY


def call_tool(name, params) -> dict:
    """按名称调用受控工具，返回统一契约结构。

    - 未知工具 → UNKNOWN_TOOL
    - params 不是 dict 或缺 delivery → INVALID_PARAMS
    - 工具执行期异常 → TOOL_ERROR（不抛出）
    """
    if not isinstance(name, str) or name not in _REGISTRY:
        return contracts.error_result(
            name if isinstance(name, str) else "",
            contracts.ERROR_UNKNOWN_TOOL,
            f"未知工具：{name!r}，允许的工具为 {list(contracts.ALL_TOOLS)}",
        )

    if not isinstance(params, dict) or "delivery" not in params:
        return contracts.error_result(
            name,
            contracts.ERROR_INVALID_PARAMS,
            "参数必须是包含 delivery 字段的对象",
        )

    delivery = params.get("delivery")
    fn = _REGISTRY[name]
    try:
        return fn(delivery)
    except Exception as exc:  # noqa: BLE001 - 工具失败必须被兜底，不中断上层
        return contracts.error_result(
            name,
            contracts.ERROR_TOOL_ERROR,
            f"工具执行异常：{exc}",
        )
