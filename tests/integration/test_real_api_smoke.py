"""真实 API 冒烟测试（显式开启，可能产生费用）。

启用方式：
    set DELIVERYGATE_RUN_REAL_API_TEST=1
    set DELIVERYGATE_LLM_BASE_URL=https://api.openai.com/v1
    set DELIVERYGATE_LLM_API_KEY=<你的key>
    set DELIVERYGATE_LLM_MODEL=<模型名>
    python -m pytest tests/integration -v

未设置 DELIVERYGATE_RUN_REAL_API_TEST=1 或缺少配置时，测试跳过而非假装通过。
绝不打印 API Key。失败时会指出发生在配置、连接、协议、工具调用还是模型响应阶段。

注意：真实模型调用可能产生费用；本测试只验证协议正确性与必需检查完整性，
不评价模型准确率，不断言固定调用顺序。
"""

from __future__ import annotations

import os

import pytest

from app.agent import config
from tests.conftest import load_scenario


def _real_enabled() -> bool:
    return os.environ.get("DELIVERYGATE_RUN_REAL_API_TEST") == "1"


requires_real_api = pytest.mark.skipif(
    not _real_enabled(),
    reason="未设置 DELIVERYGATE_RUN_REAL_API_TEST=1，跳过真实 API 冒烟测试",
)


@requires_real_api
def test_real_config_present():
    missing = config.missing_config_fields()
    assert not missing, f"真实 API 配置缺失：{missing}（请设置对应环境变量）"


@requires_real_api
def test_real_api_end_to_end_tool_calling():
    """真实模型完成至少一轮工具调用并产出可解析响应。

    验证：协议正确性、必需检查完整性、最终状态由 compute_final_status 决定。
    不断言具体调用顺序或模型文字。
    """
    from app.agent.llm_client import OpenAICompatibleClient
    from app.agent.runner import AgentRunner

    # 配置缺失 -> 构造即抛 ModelClientError（阶段：配置）
    try:
        client = OpenAICompatibleClient()
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"[配置阶段失败] {exc}")

    delivery = load_scenario("scenario_ok")

    # 连接/请求阶段
    try:
        res = AgentRunner(client).run(delivery)
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"[连接/请求阶段失败] {type(exc).__name__}: {exc}")

    # 协议/工具调用阶段：真实模式，必需检查应完成，最终状态为三值之一
    assert res.model_mode == "real"
    assert res.final_status in ("READY", "NEEDS_REVIEW", "BLOCKED")
    # 必需检查完整性：默认四项应全部执行成功
    assert res.required_checks_incomplete == 0, (
        f"[工具调用阶段] 有必需检查未完成：{res.required_checks_incomplete}"
    )
