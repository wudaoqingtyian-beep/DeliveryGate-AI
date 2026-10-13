"""Agent 执行器：单 Agent Tool Calling Loop。

对齐 docs/AGENT_DESIGN.md 第 2、4、5、7 节。职责边界严格遵守：
- Agent（经注入的模型客户端）决定"下一步调用哪个工具"与"何时结束"。
- 工具始终经 app.tools.registry 白名单执行，真实、只读、无副作用。
- 最终就绪状态由 app.services.final_status.compute_final_status 确定性计算，
  绝不采用模型自称的 READY/NEEDS_REVIEW/BLOCKED。

执行循环（run）：
  准备任务与上下文 -> 请求模型决定下一步 -> 解析工具调用或最终文本
  -> 校验工具名与参数 -> 经 registry 执行工具 -> 记录 tool_event
  -> 把工具结果回传对话 -> 模型继续决策 -> 汇总 findings 与证据
  -> compute_final_status -> 返回结构化执行结果。

本阶段不实现真实模型请求、FastAPI、SQLite 持久化或前端。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from app.agent import prompts
from app.agent.llm_client import (
    RESPONSE_FINAL,
    RESPONSE_TOOL_CALLS,
    BaseModelClient,
    ModelClientError,
    ModelResponse,
    ToolCall,
)
from app.schemas import contracts
from app.services.final_status import compute_final_status
from app.tools import registry

# ---- 停止上限（AGENT_DESIGN.md 第 4.2 节初始建议值，非压测验证的最佳值） ----
MAX_STEPS = 12           # 单次任务"模型调用+工具执行"总回合数上限
MAX_TOOL_CALLS = 8       # 单次任务工具调用累计次数上限
DEFAULT_REQUEST_TIMEOUT = 30.0   # 单次模型请求超时（秒，接口保留）
DEFAULT_TASK_TIMEOUT = 120.0     # 单次任务总时长（秒，接口保留）


@dataclass
class ToolEvent:
    """一次工具执行事件（内存记录，对应未来 tool_events 行）。

    tool_event_id 由执行器在任务内唯一分配（如 TE-0001）。
    """

    tool_event_id: str
    tool: str
    arguments: dict
    status: str            # succeeded / failed
    result: Optional[dict] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None


@dataclass
class RunResult:
    """一次核验任务的结构化执行结果（内存，对应未来 runs + findings 报告）。"""

    model_mode: str
    delivery_id: Optional[str]
    final_status: str
    summary: str
    counts: dict
    required_checks_incomplete: int
    findings: list[dict]
    tool_events: list[ToolEvent]
    model_explanation: str
    run_status: str        # succeeded / stopped_max_steps / stopped_max_tool_calls /
    #                       stopped_repeat / model_error / task_timeout
    steps_used: int
    tool_calls_used: int
    duration_ms: int
    required_tools: list[str]

    def to_report(self) -> dict:
        """产出对齐 DATA_CONTRACTS.md 第 5 节的报告结构（内存版，无 run_id/时间戳持久化）。"""
        return {
            "model_mode": self.model_mode,
            "delivery_id": self.delivery_id,
            "final_status": self.final_status,
            "summary": self.summary,
            "counts": self.counts,
            "required_checks_incomplete": self.required_checks_incomplete,
            "findings": list(self.findings),
            "tool_events": [
                {
                    "tool_event_id": e.tool_event_id,
                    "tool": e.tool,
                    "arguments": e.arguments,
                    "status": e.status,
                    "error_code": e.error_code,
                }
                for e in self.tool_events
            ],
            "model_explanation": self.model_explanation,
            "run_status": self.run_status,
            "steps_used": self.steps_used,
            "tool_calls_used": self.tool_calls_used,
            "duration_ms": self.duration_ms,
        }


class AgentRunner:
    """单 Agent 执行器。

    通过注入模型客户端（client）实现可替换：模拟客户端用于本地/测试，阶段 3
    可注入真实 OpenAI 兼容客户端。执行器不感知供应商差异。
    """

    def __init__(
        self,
        client: BaseModelClient,
        required_tools: Optional[list[str]] = None,
        max_steps: int = MAX_STEPS,
        max_tool_calls: int = MAX_TOOL_CALLS,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
        task_timeout: float = DEFAULT_TASK_TIMEOUT,
    ) -> None:
        self.client = client
        self.required_tools = self._validate_required_tools(required_tools)
        self.max_steps = max_steps
        self.max_tool_calls = max_tool_calls
        self.request_timeout = request_timeout
        self.task_timeout = task_timeout

    # ---- required_tools 白名单校验 ----
    @staticmethod
    def _validate_required_tools(required_tools: Optional[list[str]]) -> list[str]:
        """校验并规范化必需检查清单。

        - 缺省 None -> 全部四个工具（第一版默认任务要求完成全部四项）。
        - 显式提供 -> 必须是白名单内工具的子集；出现白名单外工具名则抛 ValueError。
        必需检查范围由应用层任务规则确定，不由模型生成或修改。
        """
        if required_tools is None:
            return list(contracts.ALL_TOOLS)
        if not isinstance(required_tools, (list, tuple)):
            raise ValueError("required_tools 必须是工具名列表")
        seen: list[str] = []
        for t in required_tools:
            if not isinstance(t, str) or t not in contracts.ALL_TOOLS:
                raise ValueError(f"required_tools 含非法工具名：{t!r}")
            if t not in seen:
                seen.append(t)
        if not seen:
            raise ValueError("required_tools 不能为空")
        return seen

    # ---- 参数校验：模型提供的工具参数必须实际校验，不直接信任 ----
    @staticmethod
    def _validate_arguments(name: str, arguments: Any) -> Optional[str]:
        """返回 None 表示参数合法；否则返回 INVALID_PARAMS 说明。

        契约：每个工具的参数必须是含 delivery 字段的对象。
        """
        if not isinstance(arguments, dict) or "delivery" not in arguments:
            return "参数必须是包含 delivery 字段的对象"
        return None

    # ---- 单个工具调用：经 registry 执行并记录事件 ----
    def _execute_tool_call(
        self, call: ToolCall, events: list[ToolEvent], event_counter: list[int]
    ) -> dict:
        event_counter[0] += 1
        tool_event_id = f"TE-{event_counter[0]:04d}"

        # 1) 未知工具：不执行
        if not isinstance(call.name, str) or not registry.has_tool(call.name):
            result = contracts.error_result(
                call.name if isinstance(call.name, str) else "",
                contracts.ERROR_UNKNOWN_TOOL,
                f"未知工具：{call.name!r}",
            )
            events.append(
                ToolEvent(
                    tool_event_id=tool_event_id,
                    tool=call.name if isinstance(call.name, str) else "",
                    arguments=call.arguments if isinstance(call.arguments, dict) else {},
                    status="failed",
                    result=result,
                    error_code=contracts.ERROR_UNKNOWN_TOOL,
                    error_message=result["error"]["message"],
                )
            )
            return result

        # 2) 参数非法：不进入工具业务逻辑
        invalid = self._validate_arguments(call.name, call.arguments)
        if invalid is not None:
            result = contracts.error_result(
                call.name, contracts.ERROR_INVALID_PARAMS, invalid
            )
            events.append(
                ToolEvent(
                    tool_event_id=tool_event_id,
                    tool=call.name,
                    arguments=call.arguments if isinstance(call.arguments, dict) else {},
                    status="failed",
                    result=result,
                    error_code=contracts.ERROR_INVALID_PARAMS,
                    error_message=invalid,
                )
            )
            return result

        # 3) 经 registry 白名单执行（工具内部异常已被 registry 兜底为 TOOL_ERROR）
        result = registry.call_tool(call.name, {"delivery": call.arguments.get("delivery")})
        ok = bool(result.get("ok"))
        events.append(
            ToolEvent(
                tool_event_id=tool_event_id,
                tool=call.name,
                arguments={"delivery": call.arguments.get("delivery")},
                status="succeeded" if ok else "failed",
                result=result,
                error_code=None if ok else (result.get("error") or {}).get("code"),
                error_message=None if ok else (result.get("error") or {}).get("message"),
            )
        )
        return result


    # ---- 主循环 ----
    def run(self, delivery: dict) -> RunResult:
        start = time.perf_counter()
        messages = prompts.build_initial_messages(delivery, self.required_tools)
        tools = prompts.build_tool_schemas()

        events: list[ToolEvent] = []
        event_counter = [0]
        # tool_results：本次实际执行过的工具及其结果（键为工具名，后者为准）
        tool_results: dict[str, dict] = {}
        # 重复调用检测：(tool, 规范化参数) -> 上次结果
        repeat_cache: dict[str, dict] = {}

        steps_used = 0
        tool_calls_used = 0
        run_status = "succeeded"
        model_explanation = ""

        while True:
            # 停止条件：步数上限
            if steps_used >= self.max_steps:
                run_status = "stopped_max_steps"
                break
            # 停止条件：任务总时长
            if (time.perf_counter() - start) > self.task_timeout:
                run_status = "task_timeout"
                break

            # 请求模型决定下一步
            try:
                response = self.client.decide(
                    messages, tools, timeout=self.request_timeout
                )
            except ModelClientError as exc:
                # 模型客户端异常：保留已有证据，标记 model_error，不伪造成功
                run_status = "model_error"
                model_explanation = f"[模型调用异常] {exc}"
                break
            except Exception as exc:  # noqa: BLE001 - 客户端任何异常都不得伪造成功
                run_status = "model_error"
                model_explanation = f"[模型调用异常] {exc}"
                break

            steps_used += 1

            # 响应类型未知：按 model_error 处理
            if response.kind not in (RESPONSE_TOOL_CALLS, RESPONSE_FINAL):
                run_status = "model_error"
                model_explanation = "[模型返回未知响应类型]"
                break

            # 最终文本：模型请求结束
            if response.kind == RESPONSE_FINAL:
                model_explanation = response.text or ""
                break

            # 工具调用：可能一次多个；逐个校验、执行、回传
            if not response.tool_calls:
                run_status = "model_error"
                model_explanation = "[模型返回空工具调用]"
                break

            stop_after_batch = False
            for call in response.tool_calls:
                # 停止条件：工具调用次数上限
                if tool_calls_used >= self.max_tool_calls:
                    run_status = "stopped_max_tool_calls"
                    stop_after_batch = True
                    break

                # 重复调用检测：同工具 + 完全相同参数 -> 返回缓存，不重复执行
                cache_key = self._repeat_key(call)
                if cache_key is not None and cache_key in repeat_cache:
                    cached = repeat_cache[cache_key]
                    self._append_tool_message(messages, call, cached)
                    continue

                result = self._execute_tool_call(call, events, event_counter)
                tool_calls_used += 1

                # 记录实际执行结果（成功才算完成必需检查；失败也记录 ok=false）
                if isinstance(call.name, str) and registry.has_tool(call.name):
                    tool_results[call.name] = result
                    if cache_key is not None:
                        repeat_cache[cache_key] = result

                # 把工具结果回传给模型，供下一轮决策
                self._append_tool_message(messages, call, result)

            if stop_after_batch:
                break

        return self._finalize(
            delivery=delivery,
            tool_results=tool_results,
            events=events,
            model_explanation=model_explanation,
            run_status=run_status,
            steps_used=steps_used,
            tool_calls_used=tool_calls_used,
            duration_ms=int((time.perf_counter() - start) * 1000),
        )


    # ---- 汇总 findings、计算最终状态、产出 RunResult ----
    def _finalize(
        self,
        delivery: dict,
        tool_results: dict,
        events: list[ToolEvent],
        model_explanation: str,
        run_status: str,
        steps_used: int,
        tool_calls_used: int,
        duration_ms: int,
    ) -> RunResult:
        # 1) 汇总 findings，分配任务内唯一 id，并关联来源 tool_event_id
        findings: list[dict] = []
        finding_seq = 0
        for event in events:
            result = event.result or {}
            for f in result.get("findings", []) or []:
                finding_seq += 1
                merged = dict(f)
                merged["id"] = f"F-{finding_seq:04d}"
                merged["tool"] = event.tool
                merged["tool_event_id"] = event.tool_event_id
                findings.append(merged)

        # 2) 构造 run_context（required_tools + 实际执行结果）
        run_context = {
            "required_tools": list(self.required_tools),
            "tool_results": tool_results,
        }

        # 3) 最终状态：唯一权威入口 compute_final_status，不由模型决定
        final_status = compute_final_status(findings, run_context)

        # 4) 计数
        blockers = sum(
            1
            for f in findings
            if f.get("severity") == contracts.SEVERITY_BLOCKER
            and f.get("certainty") == contracts.CERTAINTY_CONFIRMED
        )
        warnings = sum(
            1 for f in findings if f.get("severity") == contracts.SEVERITY_WARNING
        )
        passes = sum(
            1 for f in findings if f.get("severity") == contracts.SEVERITY_PASS
        )
        counts = {"blockers": blockers, "warnings": warnings, "pass": passes}

        from app.services.final_status import count_required_checks_incomplete

        required_incomplete = count_required_checks_incomplete(run_context)

        delivery_id = None
        if isinstance(delivery, dict):
            delivery_id = delivery.get("delivery_id")

        return RunResult(
            model_mode=getattr(self.client, "mode", "unknown"),
            delivery_id=delivery_id,
            final_status=final_status,
            summary=self._summary(counts, required_incomplete),
            counts=counts,
            required_checks_incomplete=required_incomplete,
            findings=findings,
            tool_events=events,
            model_explanation=model_explanation,
            run_status=run_status,
            steps_used=steps_used,
            tool_calls_used=tool_calls_used,
            duration_ms=duration_ms,
            required_tools=list(self.required_tools),
        )

    # ---- 辅助 ----
    @staticmethod
    def _repeat_key(call: ToolCall) -> Optional[str]:
        if not isinstance(call.name, str):
            return None
        try:
            args = json.dumps(call.arguments, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            return None
        return f"{call.name}::{args}"

    @staticmethod
    def _append_tool_message(messages: list[dict], call: ToolCall, result: dict) -> None:
        # assistant 消息：符合 OpenAI Chat Completions 工具调用协议。
        # tool_calls[].function.arguments 必须是 JSON 字符串（而非 dict）。
        # 注意：call.id 是模型侧工具调用 ID，用于维持模型协议的调用关联；
        # 它与系统内部的 tool_event_id 是两回事，分别保存、分别使用。
        messages.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(
                                call.arguments, ensure_ascii=False
                            ),
                        },
                    }
                ],
            }
        )
        # tool 消息：按模型工具调用 ID 关联结果（tool_call_id 对应上面的 id）。
        messages.append(
            {
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(result, ensure_ascii=False),
            }
        )

    @staticmethod
    def _summary(counts: dict, required_incomplete: int) -> str:
        parts = []
        if counts["blockers"]:
            parts.append(f"{counts['blockers']} 个阻塞项")
        if counts["warnings"]:
            parts.append(f"{counts['warnings']} 个警告")
        if required_incomplete:
            parts.append(f"{required_incomplete} 项必需检查未完成")
        return "发现 " + "，".join(parts) if parts else "未发现问题"


def run_verification(
    delivery: dict,
    client: Optional[BaseModelClient] = None,
    required_tools: Optional[list[str]] = None,
) -> RunResult:
    """便捷入口：用模拟客户端（默认）或注入的客户端执行一次核验任务。"""
    from app.agent.llm_client import SimulationClient

    runner = AgentRunner(
        client=client or SimulationClient(), required_tools=required_tools
    )
    return runner.run(delivery)

