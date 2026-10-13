"""工具 check_required_fields：检查交付必填顶层字段是否完整。

对齐 docs/DATA_CONTRACTS.md 第 3.1 节：
- 每个必填顶层字段缺失、为 None、空字符串或空列表 → blocker / confirmed /
  MISSING_REQUIRED_FIELD。
- 失败：delivery 非对象 → INVALID_INPUT。
确定性纯函数，只读，无副作用。
"""

from __future__ import annotations

from app.schemas import contracts


def _is_missing(value) -> bool:
    """必填字段判定：缺失、None、空字符串或空列表均视为缺失。"""
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    if isinstance(value, (list, dict)) and len(value) == 0:
        return True
    return False


def check_required_fields(delivery) -> dict:
    tool = contracts.TOOL_CHECK_REQUIRED_FIELDS

    if not isinstance(delivery, dict):
        return contracts.error_result(
            tool, contracts.ERROR_INVALID_INPUT, "delivery 不是合法对象"
        )

    findings = []
    for field in contracts.REQUIRED_TOP_FIELDS:
        value = delivery.get(field)
        if _is_missing(value):
            actual = None if value is None else value
            findings.append(
                contracts.make_finding(
                    severity=contracts.SEVERITY_BLOCKER,
                    certainty=contracts.CERTAINTY_CONFIRMED,
                    code="MISSING_REQUIRED_FIELD",
                    message=f"必填字段 {field} 缺失或为空",
                    source=f"delivery.{field}",
                    expected="非空值",
                    actual=actual,
                    remediation=f"补充交付资料中的 {field} 字段",
                )
            )

    return contracts.ok_result(tool, findings)
