"""核验工具的共享数据契约：常量、finding 构造与统一返回结构。

对齐 docs/DATA_CONTRACTS.md 第 3 节（统一成功/失败返回）、第 4 节（状态判定）。
本模块刻意保持为纯函数 + 常量，不引入 Pydantic 类层次：阶段 1 的工具是
确定性的纯 Python 函数，直接产出 dict 即可；Pydantic 模型留给后续 API 层。
"""

from __future__ import annotations

# ---- 工具名（固定，四个核验工具） ----
TOOL_CHECK_REQUIRED_FIELDS = "check_required_fields"
TOOL_COMPARE_ENVIRONMENT = "compare_environment_config"
TOOL_COMPARE_API_ACCEPTANCE = "compare_api_acceptance"
TOOL_INSPECT_ACCEPTANCE_COVERAGE = "inspect_acceptance_coverage"

ALL_TOOLS = (
    TOOL_CHECK_REQUIRED_FIELDS,
    TOOL_COMPARE_ENVIRONMENT,
    TOOL_COMPARE_API_ACCEPTANCE,
    TOOL_INSPECT_ACCEPTANCE_COVERAGE,
)

# ---- severity / certainty ----
SEVERITY_BLOCKER = "blocker"
SEVERITY_WARNING = "warning"
SEVERITY_PASS = "pass"

CERTAINTY_CONFIRMED = "confirmed"
CERTAINTY_UNCERTAIN = "uncertain"

# ---- 最终状态 ----
STATUS_READY = "READY"
STATUS_NEEDS_REVIEW = "NEEDS_REVIEW"
STATUS_BLOCKED = "BLOCKED"

# ---- 错误码 ----
# registry 层（AGENT_DESIGN.md 第 5 节）
ERROR_UNKNOWN_TOOL = "UNKNOWN_TOOL"
ERROR_INVALID_PARAMS = "INVALID_PARAMS"
# 工具层（DATA_CONTRACTS.md 第 3.0 节）
ERROR_INVALID_INPUT = "INVALID_INPUT"
ERROR_TOOL_ERROR = "TOOL_ERROR"

# ---- 必填顶层字段（DATA_CONTRACTS.md 第 1、3.1 节） ----
REQUIRED_TOP_FIELDS = (
    "delivery_id",
    "project_name",
    "version",
    "contact",
    "target_environment",
    "actual_deployment",
    "api_contracts",
    "acceptance_criteria",
    "verification_steps",
)

# ---- 允许的 API auth 取值（DATA_CONTRACTS.md 第 3.3 节） ----
ALLOWED_AUTH = ("none", "api_key", "bearer_token")

# ---- 验收要求严重度 ----
CRITICALITY_CRITICAL = "critical"
CRITICALITY_NORMAL = "normal"


def make_finding(
    *,
    severity: str,
    certainty: str,
    code: str,
    message: str,
    source: str,
    expected=None,
    actual=None,
    remediation: str = "",
) -> dict:
    """构造一条符合 DATA_CONTRACTS.md 第 3.0 节的 finding。

    不含 id：id 由汇总层在持久化/报告时统一编号，避免工具之间 id 冲突。
    """
    return {
        "severity": severity,
        "certainty": certainty,
        "code": code,
        "message": message,
        "evidence": {
            "source": source,
            "expected": expected,
            "actual": actual,
        },
        "remediation": remediation,
    }


def ok_result(tool: str, findings: list[dict], summary: str = "") -> dict:
    """统一成功返回：ok=true。即使发现 blocker 也属成功执行。"""
    return {
        "tool": tool,
        "ok": True,
        "findings": list(findings),
        "summary": summary or _default_summary(findings),
    }


def error_result(tool: str, code: str, message: str) -> dict:
    """统一失败返回：ok=false，findings 必须为空。

    失败绝不产生 finding，也绝不视为通过（DATA_CONTRACTS.md 第 3.0 节）。
    """
    return {
        "tool": tool,
        "ok": False,
        "findings": [],
        "error": {"code": code, "message": message},
        "summary": "工具执行失败",
    }


def _default_summary(findings: list[dict]) -> str:
    blockers = sum(1 for f in findings if f.get("severity") == SEVERITY_BLOCKER)
    warnings = sum(1 for f in findings if f.get("severity") == SEVERITY_WARNING)
    if not findings:
        return "未发现问题"
    parts = []
    if blockers:
        parts.append(f"{blockers} 个阻塞项")
    if warnings:
        parts.append(f"{warnings} 个警告")
    return "发现 " + "，".join(parts)
