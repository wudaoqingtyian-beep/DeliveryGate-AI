"""AgentRunner 执行器测试。

使用可编程的 Fake 客户端精确控制调用序列，验证：
- 工具调用与最终文本的区分；
- 动态决策（依前次结果决定下一步）；
- 经 registry 白名单执行真实工具；
- 未知工具、无效参数、重复调用、最大步数/调用次数、模型异常；
- required_tools 白名单校验；
- tool_event_id / finding ID 分配与证据追溯；
- 最终状态由 compute_final_status 计算，不由模型决定。
"""

from __future__ import annotations

from app.agent.llm_client import (
    BaseModelClient,
    ModelClientError,
    ModelResponse,
    ToolCall,
)
from app.agent.runner import AgentRunner
from app.schemas import contracts
from tests.conftest import load_scenario


class ScriptedClient(BaseModelClient):
    """按预定脚本依次返回响应的 Fake 客户端（用于精确验证调用序列）。"""

    mode = "fake"

    def __init__(self, responses, delivery):
        self._responses = list(responses)
        self._delivery = delivery
        self.calls_seen = 0

    def decide(self, messages, tools, timeout=None):
        self.calls_seen += 1
        if self._responses:
            return self._responses.pop(0)
        return ModelResponse.from_final_text("[Fake] done")


def _tc(name, delivery, cid="c1"):
    return ToolCall(id=cid, name=name, arguments={"delivery": delivery})


def _final(text="[Fake] done"):
    return ModelResponse.from_final_text(text)


# ---------------- 基础：工具调用与最终文本 ----------------
def test_runner_executes_tools_then_final(ok_delivery):
    client = ScriptedClient(
        [
            ModelResponse.from_tool_calls([_tc("check_required_fields", ok_delivery)]),
            _final("全部通过"),
        ],
        ok_delivery,
    )
    runner = AgentRunner(client, required_tools=["check_required_fields"])
    res = runner.run(ok_delivery)
    assert res.final_status == "READY"
    assert res.run_status == "succeeded"
    assert res.tool_calls_used == 1
    assert len(res.tool_events) == 1
    assert res.model_explanation == "全部通过"


def test_runner_distinguishes_tool_call_and_final(ok_delivery):
    client = ScriptedClient(
        [
            ModelResponse.from_tool_calls(
                [_tc("check_required_fields", ok_delivery)]
            ),
            _final(),
        ],
        ok_delivery,
    )
    runner = AgentRunner(client, required_tools=["check_required_fields"])
    res = runner.run(ok_delivery)
    assert res.model_mode == "fake"
    assert res.tool_events[0].status == "succeeded"


def test_default_required_tools_is_all_four(ok_delivery):
    client = ScriptedClient([], ok_delivery)  # 直接结束
    runner = AgentRunner(client)
    assert runner.required_tools == list(contracts.ALL_TOOLS)


# ---------------- 动态决策 ----------------
def test_dynamic_decision_based_on_previous_result(missing_delivery):
    # Fake 客户端第一次调必填检查，第二次依据其结果改调环境核验
    seen = {"second": None}

    class DynClient(BaseModelClient):
        mode = "fake"

        def __init__(self, delivery):
            self._delivery = delivery
            self._n = 0

        def decide(self, messages, tools, timeout=None):
            self._n += 1
            if self._n == 1:
                return ModelResponse.from_tool_calls(
                    [_tc("check_required_fields", self._delivery, cid="a")]
                )
            # 第二次：依据 messages 中已回传的工具结果，选择环境核验
            seen["second"] = "compare_environment_config"
            return ModelResponse.from_tool_calls(
                [_tc("compare_environment_config", self._delivery, cid="b")]
            )

    runner = AgentRunner(
        DynClient(missing_delivery),
        required_tools=["check_required_fields", "compare_environment_config"],
    )
    res = runner.run(missing_delivery)
    assert seen["second"] == "compare_environment_config"
    executed = [e.tool for e in res.tool_events]
    assert executed == ["check_required_fields", "compare_environment_config"]


# ---------------- 未知工具 / 无效参数 ----------------
def test_unknown_tool_not_executed(ok_delivery):
    client = ScriptedClient(
        [ModelResponse.from_tool_calls([_tc("drop_database", ok_delivery)]), _final()],
        ok_delivery,
    )
    runner = AgentRunner(client, required_tools=["check_required_fields"])
    res = runner.run(ok_delivery)
    ev = res.tool_events[0]
    assert ev.status == "failed"
    assert ev.error_code == "UNKNOWN_TOOL"
    # 未知工具未产生 finding，且必需检查未完成 -> 不能 READY
    assert res.final_status == "BLOCKED"
    assert res.required_checks_incomplete == 1


def test_invalid_params_not_entering_tool(ok_delivery):
    bad = ToolCall(id="c1", name="check_required_fields", arguments={"foo": 1})
    client = ScriptedClient(
        [ModelResponse.from_tool_calls([bad]), _final()], ok_delivery
    )
    runner = AgentRunner(client, required_tools=["check_required_fields"])
    res = runner.run(ok_delivery)
    ev = res.tool_events[0]
    assert ev.status == "failed"
    assert ev.error_code == "INVALID_PARAMS"
    assert res.final_status == "BLOCKED"


def test_non_dict_arguments_invalid(ok_delivery):
    bad = ToolCall(id="c1", name="check_required_fields", arguments="oops")
    client = ScriptedClient(
        [ModelResponse.from_tool_calls([bad]), _final()], ok_delivery
    )
    runner = AgentRunner(client, required_tools=["check_required_fields"])
    res = runner.run(ok_delivery)
    assert res.tool_events[0].error_code == "INVALID_PARAMS"


# ---------------- 重复调用 ----------------
def test_repeat_call_not_re_executed(ok_delivery):
    # 同工具 + 相同参数重复两次：第二次不新增事件
    client = ScriptedClient(
        [
            ModelResponse.from_tool_calls(
                [_tc("check_required_fields", ok_delivery, cid="a")]
            ),
            ModelResponse.from_tool_calls(
                [_tc("check_required_fields", ok_delivery, cid="b")]
            ),
            _final(),
        ],
        ok_delivery,
    )
    runner = AgentRunner(client, required_tools=["check_required_fields"])
    res = runner.run(ok_delivery)
    # 只有第一次真正执行 -> 1 个事件
    assert len(res.tool_events) == 1
    assert res.final_status == "READY"



# ---------------- 停止条件 ----------------
def test_max_steps_safe_stop(ok_delivery):
    class LoopClient(BaseModelClient):
        mode = "fake"

        def __init__(self, delivery):
            self._delivery = delivery

        def decide(self, messages, tools, timeout=None):
            return ModelResponse.from_tool_calls(
                [_tc("check_required_fields", self._delivery)]
            )

    runner = AgentRunner(
        LoopClient(ok_delivery),
        required_tools=["check_required_fields"],
        max_steps=3,
    )
    res = runner.run(ok_delivery)
    assert res.run_status == "stopped_max_steps"
    assert res.steps_used <= 3


def test_max_tool_calls_safe_stop(ok_delivery):
    class ManyClient(BaseModelClient):
        mode = "fake"

        def __init__(self, delivery):
            self._delivery = delivery
            self._n = 0

        def decide(self, messages, tools, timeout=None):
            self._n += 1
            d = dict(self._delivery)
            d["_nonce"] = self._n  # 制造不同参数绕过重复检测
            return ModelResponse.from_tool_calls(
                [ToolCall(id=f"c{self._n}", name="check_required_fields",
                          arguments={"delivery": d})]
            )

    runner = AgentRunner(
        ManyClient(ok_delivery),
        required_tools=["check_required_fields"],
        max_tool_calls=3,
        max_steps=20,
    )
    res = runner.run(ok_delivery)
    assert res.run_status == "stopped_max_tool_calls"
    assert res.tool_calls_used <= 3


# ---------------- 模型异常 ----------------
def test_model_error_preserves_evidence(ok_delivery):
    class BoomClient(BaseModelClient):
        mode = "fake"

        def __init__(self, delivery):
            self._delivery = delivery
            self._n = 0

        def decide(self, messages, tools, timeout=None):
            self._n += 1
            if self._n == 1:
                return ModelResponse.from_tool_calls(
                    [_tc("check_required_fields", self._delivery)]
                )
            raise ModelClientError("boom")

    runner = AgentRunner(
        BoomClient(ok_delivery), required_tools=["check_required_fields"]
    )
    res = runner.run(ok_delivery)
    assert res.run_status == "model_error"
    assert len(res.tool_events) == 1
    assert res.tool_events[0].status == "succeeded"


def test_model_error_missing_required_not_ready(ok_delivery):
    class BoomNow(BaseModelClient):
        mode = "fake"

        def decide(self, messages, tools, timeout=None):
            raise ModelClientError("down")

    runner = AgentRunner(BoomNow())
    res = runner.run(ok_delivery)
    assert res.run_status == "model_error"
    assert res.final_status == "BLOCKED"
    assert res.required_checks_incomplete == 4


# ---------------- 提前结束不能 READY ----------------
def test_early_final_with_incomplete_required_not_ready(ok_delivery):
    client = ScriptedClient([_final("我觉得没问题")], ok_delivery)
    runner = AgentRunner(client)
    res = runner.run(ok_delivery)
    assert res.run_status == "succeeded"
    assert res.required_checks_incomplete == 4
    assert res.final_status == "BLOCKED"
    assert res.tool_calls_used == 0


def test_model_cannot_override_final_status(missing_delivery):
    class AllToolsThenLie(BaseModelClient):
        mode = "fake"

        def __init__(self, delivery):
            self._delivery = delivery
            self._queue = list(contracts.ALL_TOOLS)

        def decide(self, messages, tools, timeout=None):
            if self._queue:
                name = self._queue.pop(0)
                return ModelResponse.from_tool_calls(
                    [ToolCall(id=name, name=name,
                              arguments={"delivery": self._delivery})]
                )
            return ModelResponse.from_final_text("最终状态：READY，可以交付")

    runner = AgentRunner(AllToolsThenLie(missing_delivery))
    res = runner.run(missing_delivery)
    assert res.final_status == "BLOCKED"
    assert "READY" in res.model_explanation



# ---------------- required_tools 白名单校验 ----------------
def test_required_tools_reject_unknown_name(ok_delivery):
    import pytest

    with pytest.raises(ValueError):
        AgentRunner(ScriptedClient([], ok_delivery),
                    required_tools=["check_required_fields", "evil_tool"])


def test_required_tools_reject_empty(ok_delivery):
    import pytest

    with pytest.raises(ValueError):
        AgentRunner(ScriptedClient([], ok_delivery), required_tools=[])


def test_required_tools_valid_subset(ok_delivery):
    client = ScriptedClient([], ok_delivery)
    runner = AgentRunner(client, required_tools=["check_required_fields"])
    assert runner.required_tools == ["check_required_fields"]


def test_non_required_tool_not_called_does_not_block(ok_delivery):
    # 只要求一个工具并完成它 -> READY；其他工具未调用不影响
    client = ScriptedClient(
        [
            ModelResponse.from_tool_calls(
                [_tc("check_required_fields", ok_delivery)]
            ),
            _final(),
        ],
        ok_delivery,
    )
    runner = AgentRunner(client, required_tools=["check_required_fields"])
    res = runner.run(ok_delivery)
    assert res.required_checks_incomplete == 0
    assert res.final_status == "READY"


# ---------------- tool_event_id / finding ID 与追溯 ----------------
def test_tool_event_ids_are_unique_and_ordered(ok_delivery):
    # 顺序执行全部四个工具，事件 id 应唯一且递增
    class SeqClient(BaseModelClient):
        mode = "fake"

        def __init__(self, delivery):
            self._delivery = delivery
            self._queue = list(contracts.ALL_TOOLS)

        def decide(self, messages, tools, timeout=None):
            if self._queue:
                name = self._queue.pop(0)
                return ModelResponse.from_tool_calls(
                    [ToolCall(id=name, name=name,
                              arguments={"delivery": self._delivery})]
                )
            return ModelResponse.from_final_text("done")

    runner = AgentRunner(SeqClient(ok_delivery))
    res = runner.run(ok_delivery)
    ids = [e.tool_event_id for e in res.tool_events]
    assert ids == ["TE-0001", "TE-0002", "TE-0003", "TE-0004"]
    assert len(set(ids)) == 4


def test_finding_ids_unique_and_linked_to_event(missing_delivery):
    class AllTools(BaseModelClient):
        mode = "fake"

        def __init__(self, delivery):
            self._delivery = delivery
            self._queue = list(contracts.ALL_TOOLS)

        def decide(self, messages, tools, timeout=None):
            if self._queue:
                name = self._queue.pop(0)
                return ModelResponse.from_tool_calls(
                    [ToolCall(id=name, name=name,
                              arguments={"delivery": self._delivery})]
                )
            return ModelResponse.from_final_text("done")

    res = AgentRunner(AllTools(missing_delivery)).run(missing_delivery)
    assert len(res.findings) >= 1
    fids = [f["id"] for f in res.findings]
    assert len(set(fids)) == len(fids)  # 唯一
    assert fids[0] == "F-0001"
    # 每条 finding 关联到存在的 tool_event_id 且带 evidence.source
    event_ids = {e.tool_event_id for e in res.tool_events}
    for f in res.findings:
        assert f["tool_event_id"] in event_ids
        assert f["evidence"]["source"]
        assert f["tool"]


def test_report_structure_matches_contract(missing_delivery):
    class AllTools(BaseModelClient):
        mode = "fake"

        def __init__(self, delivery):
            self._delivery = delivery
            self._queue = list(contracts.ALL_TOOLS)

        def decide(self, messages, tools, timeout=None):
            if self._queue:
                name = self._queue.pop(0)
                return ModelResponse.from_tool_calls(
                    [ToolCall(id=name, name=name,
                              arguments={"delivery": self._delivery})]
                )
            return ModelResponse.from_final_text("done")

    res = AgentRunner(AllTools(missing_delivery)).run(missing_delivery)
    report = res.to_report()
    for key in ("model_mode", "delivery_id", "final_status", "summary",
                "counts", "required_checks_incomplete", "findings",
                "tool_events", "model_explanation", "run_status"):
        assert key in report
    assert report["final_status"] in ("READY", "NEEDS_REVIEW", "BLOCKED")
    assert report["delivery_id"] == "DLV-2024-002"

