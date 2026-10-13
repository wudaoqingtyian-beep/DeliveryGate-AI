"""DeliveryGate AI Agent 包：单 Agent 执行器 + 模型客户端接口 + 模拟模式。

阶段 2 只实现模拟模式与可注入的客户端接口；真实 OpenAI 兼容客户端在阶段 3 接入。
Agent 负责选工具、决定下一步、解释结果；最终就绪状态始终由确定性
compute_final_status 计算，不由模型决定。
"""

from app.agent.llm_client import (
    BaseModelClient,
    ModelClientError,
    ModelResponse,
    OpenAICompatibleClient,
    SimulationClient,
    ToolCall,
)
from app.agent.runner import AgentRunner, RunResult, ToolEvent, run_verification

__all__ = [
    "AgentRunner",
    "RunResult",
    "ToolEvent",
    "run_verification",
    "BaseModelClient",
    "ModelClientError",
    "ModelResponse",
    "OpenAICompatibleClient",
    "SimulationClient",
    "ToolCall",
]

