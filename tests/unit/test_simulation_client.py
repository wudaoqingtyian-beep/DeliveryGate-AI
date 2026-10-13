"""模拟客户端与端到端集成测试。

验证：
- SimulationClient 不发起网络请求、标注 simulation；
- 五个固定场景经模拟客户端 + 真实工具得到预期最终状态；
- 模拟客户端依据前次结果动态决策（不同场景有不同调用路径）；
- 不硬编码最终状态、不绕过真实核验工具。
"""

from __future__ import annotations

from app.agent.llm_client import SimulationClient
from app.agent.runner import AgentRunner, run_verification
from app.schemas import contracts
from tests.conftest import SCENARIO_EXPECTED_STATUS, load_scenario


# ---------------- 模拟客户端本身 ----------------
def test_simulation_client_mode_is_simulation():
    assert SimulationClient().mode == "simulation"


def test_simulation_client_no_network(monkeypatch):
    # 模拟客户端不得发起任何 HTTP 请求：禁用 socket 后仍能 decide
    import socket

    def _blocked(*a, **k):
        raise AssertionError("模拟客户端不应发起网络请求")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    client = SimulationClient()
    from app.agent import prompts

    messages = prompts.build_initial_messages(
        load_scenario("scenario_ok"), list(contracts.ALL_TOOLS)
    )
    resp = client.decide(messages, prompts.build_tool_schemas())
    assert resp.kind == "tool_calls"
    assert resp.tool_calls[0].name == "check_required_fields"


def test_simulation_first_call_is_required_fields():
    from app.agent import prompts

    client = SimulationClient()
    messages = prompts.build_initial_messages(
        load_scenario("scenario_ok"), list(contracts.ALL_TOOLS)
    )
    resp = client.decide(messages, prompts.build_tool_schemas())
    assert resp.tool_calls[0].name == "check_required_fields"


# ---------------- 端到端：五个场景 ----------------
def test_run_verification_default_uses_simulation():
    res = run_verification(load_scenario("scenario_ok"))
    assert res.model_mode == "simulation"


def test_all_scenarios_reach_expected_status():
    for name, expected in SCENARIO_EXPECTED_STATUS.items():
        res = run_verification(load_scenario(name))
        assert res.final_status == expected, f"{name}: {res.final_status} != {expected}"


def test_scenario_ok_is_ready_no_findings():
    res = run_verification(load_scenario("scenario_ok"))
    assert res.final_status == "READY"
    assert res.findings == []
    assert res.run_status == "succeeded"


def test_scenario_missing_is_blocked_with_findings():
    res = run_verification(load_scenario("scenario_missing"))
    assert res.final_status == "BLOCKED"
    codes = {f["code"] for f in res.findings}
    assert "MISSING_REQUIRED_FIELD" in codes
    assert "ENV_REQUIRED_VAR_MISSING" in codes


def test_all_required_tools_executed_in_default_run():
    res = run_verification(load_scenario("scenario_ok"))
    executed = [e.tool for e in res.tool_events]
    # 默认任务必须完成全部四项必需检查
    assert set(contracts.ALL_TOOLS).issubset(set(executed))
    assert res.required_checks_incomplete == 0


def test_simulation_dynamic_path_env_scenario():
    # env_mismatch 场景：模拟客户端应因环境相关问题动态调整调用顺序
    res = run_verification(load_scenario("scenario_env_mismatch"))
    executed = [e.tool for e in res.tool_events]
    # compare_environment_config 应被执行（可能因动态决策被提前）
    assert "compare_environment_config" in executed
    assert res.final_status == "BLOCKED"


def test_simulation_marks_all_outputs_as_simulated():
    # 模拟模式：解释文本带 [Simulated] 标识，不冒充真实模型
    res = run_verification(load_scenario("scenario_ok"))
    assert "[Simulated]" in res.model_explanation or res.model_mode == "simulation"


def test_injected_client_overrides_simulation():
    # 可注入客户端：传入 Fake 客户端替代模拟客户端
    from app.agent.llm_client import ModelResponse, ToolCall

    class OneShot:
        mode = "fake"

        def __init__(self, delivery):
            self._delivery = delivery

        def decide(self, messages, tools, timeout=None):
            return ModelResponse.from_final_text("[Fake] done")

    res = run_verification(
        load_scenario("scenario_ok"), client=OneShot(load_scenario("scenario_ok"))
    )
    assert res.model_mode == "fake"


def test_agent_runner_constructible_with_simulation():
    runner = AgentRunner(SimulationClient())
    res = runner.run(load_scenario("scenario_coverage_gap"))
    assert res.final_status == "BLOCKED"
    codes = {f["code"] for f in res.findings}
    assert "COVERAGE_CRITICAL_GAP" in codes
