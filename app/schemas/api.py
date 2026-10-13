"""API 请求与响应模型（Pydantic v2）。

对齐 docs/DATA_CONTRACTS.md 第 5、7 节：
- 请求：POST /api/runs 最小结构 {scenario_id, mode}；mode 仅允许 simulation/real。
- 响应：核验报告对齐第 5 节结构；任务详情对齐 7.4 节并附工具事件与 findings。
- 错误：统一 {"error": {"code", "message"}}（7.7 节）。
不返回内部异常对象、API Key 或环境变量值。
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


class HealthResponse(BaseModel):
    status: str
    service: str


class ScenarioInfo(BaseModel):
    id: str
    name: str
    expected_status: str


class ScenarioListResponse(BaseModel):
    scenarios: list[ScenarioInfo]


class CreateRunRequest(BaseModel):
    """POST /api/runs 请求体：只允许选择预置场景与运行模式。

    不开放任意文件路径、任意工具列表或任意执行命令；
    required_tools 由服务端固定为全部四项必需检查。
    """

    scenario_id: str = Field(min_length=1, max_length=200)
    mode: Literal["simulation", "real"] = "simulation"


class EvidenceModel(BaseModel):
    source: Optional[str] = None
    expected: Optional[str] = None
    actual: Optional[str] = None


class FindingModel(BaseModel):
    """一条核验问题（id 为任务内编号，如 F-0001）。

    tool_event_id 为来源工具执行事件的任务内编号（如 TE-0001），
    保留 finding -> 工具事件 -> evidence.source 的追溯链。
    """

    id: Optional[str] = None
    tool: Optional[str] = None
    tool_event_id: Optional[str] = None
    severity: str
    certainty: str
    code: str
    message: str
    evidence: EvidenceModel
    remediation: Optional[str] = None


class ToolEventModel(BaseModel):
    """一次工具执行事件（tool_event_id 为任务内编号，如 TE-0001）。"""

    tool_event_id: str
    tool: str
    status: str
    arguments: dict = Field(default_factory=dict)
    error_code: Optional[str] = None


class ReportResponse(BaseModel):
    """核验报告（DATA_CONTRACTS.md 第 5 节结构 + run_id + 时间信息）。"""

    run_id: int
    model_mode: str
    model_name: Optional[str] = None
    delivery_id: Optional[str] = None
    final_status: str
    run_status: str
    summary: Optional[str] = None
    counts: dict
    required_checks_incomplete: int
    findings: list[FindingModel]
    tool_events: list[ToolEventModel]
    model_explanation: str = ""
    steps_used: int
    tool_calls_used: int
    duration_ms: Optional[int] = None
    created_at: str
    finished_at: Optional[str] = None


class RunDetailResponse(BaseModel):
    """任务详情（7.4 节元信息 + 工具事件与 findings，不含交付资料快照）。"""

    run_id: int
    status: str
    final_status: Optional[str] = None
    model_mode: str
    model_name: Optional[str] = None
    delivery_id: Optional[str] = None
    summary: Optional[str] = None
    counts: dict
    required_checks_incomplete: int
    steps_used: int
    tool_calls_used: int
    duration_ms: Optional[int] = None
    model_explanation: str = ""
    created_at: str
    finished_at: Optional[str] = None
    findings: list[FindingModel]
    tool_events: list[ToolEventModel]
