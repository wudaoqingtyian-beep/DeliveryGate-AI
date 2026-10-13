"""最终就绪状态计算：确定性业务规则。

对齐 docs/DATA_CONTRACTS.md 第 4 节判定表（权威定义）。

compute_final_status(findings, run_context) -> "READY" | "NEEDS_REVIEW" | "BLOCKED"

run_context 约定（阶段 1.1 收尾，权威定义见 docs/DATA_CONTRACTS.md 第 4 节）：
  run_context = {
      "required_tools": ["<tool_name>", ...],   # 可选：本次任务必需完成的检查工具
      "tool_results": {
          "<tool_name>": <该工具的统一返回 dict（含 ok 字段）>,
          ...
      }
  }

语义：
- required_tools 明确"本次任务必需完成哪些检查"。缺省时回落到全部四个工具
  （contracts.ALL_TOOLS）。必需检查范围必须来自任务规则/应用层上下文，
  不得由 LLM 最终回答决定。
- tool_results 是"本次实际执行过的工具及其结果"。一个工具可能被执行但失败
  （ok=false），也可能根本没被调用（键缺失）——两者都算"必需但未完成"。
- required_checks_incomplete = required_tools 中，tool_results 未提供 ok=true
  结果的数量。关键检查未成功完成不得当作通过。
- 只统计 required_tools 内的完成情况：非必需且未执行的工具不影响最终状态。

判定表（优先级从上到下，命中即返回）：
  1. required_checks_incomplete > 0  -> BLOCKED
  2. blockers > 0                    -> BLOCKED
  3. warnings > 0                    -> NEEDS_REVIEW
  4. 否则                            -> READY

纯函数：无随机、无模型参与，相同输入必得相同输出。绝不采用 LLM 生成的状态。
"""

from __future__ import annotations

from app.schemas import contracts


def _required_tools(run_context: dict) -> tuple:
    """本次任务必需完成的检查工具列表。

    优先取 run_context["required_tools"]；缺省或非法时回落到全部四个工具。
    必需范围来自任务规则，不由模型回答决定。
    """
    if isinstance(run_context, dict):
        req = run_context.get("required_tools")
        if isinstance(req, (list, tuple)) and req:
            seen = []
            for t in req:
                if isinstance(t, str) and t in contracts.ALL_TOOLS and t not in seen:
                    seen.append(t)
            if seen:
                return tuple(seen)
    return contracts.ALL_TOOLS


def _tool_completed(run_context: dict, tool: str) -> bool:
    """该必需工具是否成功完成（ok=true 视为完成；失败或缺失均未完成）。"""
    if not isinstance(run_context, dict):
        return False
    tool_results = run_context.get("tool_results")
    if not isinstance(tool_results, dict):
        return False
    result = tool_results.get(tool)
    return isinstance(result, dict) and result.get("ok") is True


def count_required_checks_incomplete(run_context: dict) -> int:
    """required_tools 中未成功完成（失败或未执行）的数量。"""
    return sum(
        1
        for tool in _required_tools(run_context)
        if not _tool_completed(run_context, tool)
    )


def compute_final_status(findings, run_context) -> str:
    findings = findings or []

    # 输入信号
    required_checks_incomplete = count_required_checks_incomplete(run_context)
    blockers = sum(
        1
        for f in findings
        if f.get("severity") == contracts.SEVERITY_BLOCKER
        and f.get("certainty") == contracts.CERTAINTY_CONFIRMED
    )
    warnings = sum(
        1 for f in findings if f.get("severity") == contracts.SEVERITY_WARNING
    )

    # 判定表（优先级从上到下）
    if required_checks_incomplete > 0:
        return contracts.STATUS_BLOCKED
    if blockers > 0:
        return contracts.STATUS_BLOCKED
    if warnings > 0:
        return contracts.STATUS_NEEDS_REVIEW
    return contracts.STATUS_READY
