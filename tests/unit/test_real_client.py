"""OpenAICompatibleClient 真实客户端单元测试（HTTP Mock，不访问网络）。

使用 httpx.MockTransport 模拟服务端响应，覆盖：
- 配置读取与缺失错误；
- URL 拼接；
- 请求体使用现有工具 schema；
- 最终文本 / 单工具调用 / 多工具调用解析；
- 模型工具调用 ID 保留；
- 参数 JSON 无效；
- 空响应与非法结构；
- HTTP 400/401/403/429/5xx；
- 连接失败/超时；
- 瞬时错误最多重试一次、非瞬时错误不重试；
- 超时传递给 httpx；
- API Key 不出现在错误信息中。
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.agent.llm_client import (
    ModelClientError,
    OpenAICompatibleClient,
    RESPONSE_FINAL,
    RESPONSE_TOOL_CALLS,
)
from app.agent import prompts


def _make_client(handler, **kwargs):
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport)
    params = {
        "base_url": "https://api.example.com/v1",
        "api_key": "sk-test-SECRET",
        "model": "test-model",
        "timeout": 5.0,
        "http_client": http,
    }
    params.update(kwargs)
    return OpenAICompatibleClient(**params)


def _ok_text(content="全部通过"):
    return httpx.Response(
        200,
        json={
            "choices": [
                {"message": {"role": "assistant", "content": content}}
            ]
        },
    )


def _tool_call_response(name="check_required_fields", call_id="call_1",
                        arguments=None):
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": call_id,
                                "type": "function",
                                "function": {
                                    "name": name,
                                    "arguments": json.dumps(
                                        arguments or {"delivery": {"x": 1}}
                                    ),
                                },
                            }
                        ],
                    }
                }
            ]
        },
    )


# ---------------- 配置 ----------------
def test_config_missing_raises(monkeypatch):
    monkeypatch.delenv("DELIVERYGATE_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("DELIVERYGATE_LLM_API_KEY", raising=False)
    monkeypatch.delenv("DELIVERYGATE_LLM_MODEL", raising=False)
    with pytest.raises(ModelClientError) as ei:
        OpenAICompatibleClient()
    msg = str(ei.value)
    assert "DELIVERYGATE_LLM_BASE_URL" in msg
    assert "DELIVERYGATE_LLM_API_KEY" in msg
    assert "DELIVERYGATE_LLM_MODEL" in msg


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("DELIVERYGATE_LLM_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("DELIVERYGATE_LLM_API_KEY", "sk-env")
    monkeypatch.setenv("DELIVERYGATE_LLM_MODEL", "env-model")
    monkeypatch.setenv("DELIVERYGATE_LLM_TIMEOUT_SECONDS", "12")
    client = OpenAICompatibleClient()
    assert client.model == "env-model"
    assert client.timeout == 12.0
    assert client._url == "https://api.example.com/v1/chat/completions"


def test_url_no_double_slash():
    client = _make_client(lambda r: _ok_text(), base_url="https://api.example.com/v1/")
    assert client._url == "https://api.example.com/v1/chat/completions"


def test_url_already_has_path():
    client = _make_client(
        lambda r: _ok_text(),
        base_url="https://api.example.com/v1/chat/completions",
    )
    assert client._url == "https://api.example.com/v1/chat/completions"


# ---------------- 响应解析 ----------------
def test_final_text_response():
    client = _make_client(lambda r: _ok_text("核验完成"))
    resp = client.decide(
        prompts.build_initial_messages({"delivery_id": "x"}, ["check_required_fields"]),
        prompts.build_tool_schemas(),
    )
    assert resp.kind == RESPONSE_FINAL
    assert resp.text == "核验完成"


def test_single_tool_call_parsed():
    client = _make_client(
        lambda r: _tool_call_response("compare_environment_config", "call_a",
                                      {"delivery": {"a": 1}})
    )
    resp = client.decide([], prompts.build_tool_schemas())
    assert resp.kind == RESPONSE_TOOL_CALLS
    assert len(resp.tool_calls) == 1
    tc = resp.tool_calls[0]
    assert tc.id == "call_a"
    assert tc.name == "compare_environment_config"
    assert tc.arguments == {"delivery": {"a": 1}}


def test_multiple_tool_calls_all_parsed():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {"id": "c1", "type": "function",
                                 "function": {"name": "check_required_fields",
                                              "arguments": '{"delivery": {}}'}},
                                {"id": "c2", "type": "function",
                                 "function": {"name": "compare_api_acceptance",
                                              "arguments": '{"delivery": {}}'}},
                            ],
                        }
                    }
                ]
            },
        )

    client = _make_client(handler)
    resp = client.decide([], prompts.build_tool_schemas())
    assert len(resp.tool_calls) == 2
    assert [tc.id for tc in resp.tool_calls] == ["c1", "c2"]
    assert [tc.name for tc in resp.tool_calls] == [
        "check_required_fields", "compare_api_acceptance"]


def test_model_call_id_preserved():
    client = _make_client(lambda r: _tool_call_response(call_id="call_xyz"))
    resp = client.decide([], prompts.build_tool_schemas())
    assert resp.tool_calls[0].id == "call_xyz"


def test_invalid_arguments_json_not_executed():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"tool_calls": [
                        {"id": "c1", "type": "function",
                         "function": {"name": "check_required_fields",
                                      "arguments": "{not valid json"}}
                    ]}}
                ]
            },
        )

    client = _make_client(handler)
    with pytest.raises(ModelClientError):
        client.decide([], prompts.build_tool_schemas())


def test_empty_response_is_error():
    client = _make_client(
        lambda r: httpx.Response(200, json={"choices": [{"message": {}}]})
    )
    with pytest.raises(ModelClientError):
        client.decide([], prompts.build_tool_schemas())


def test_missing_choices_is_error():
    client = _make_client(lambda r: httpx.Response(200, json={"foo": 1}))
    with pytest.raises(ModelClientError):
        client.decide([], prompts.build_tool_schemas())


def test_invalid_json_body_is_error():
    client = _make_client(
        lambda r: httpx.Response(200, content=b"not json",
                                 headers={"Content-Type": "application/json"})
    )
    with pytest.raises(ModelClientError):
        client.decide([], prompts.build_tool_schemas())



# ---------------- HTTP 错误 ----------------
@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_non_retryable_http_errors(status):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(status, json={"error": "bad"})

    client = _make_client(handler)
    with pytest.raises(ModelClientError):
        client.decide([], prompts.build_tool_schemas())
    assert calls["n"] == 1  # 不重试


def test_429_not_retried():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(429, json={"error": "rate limited"})

    client = _make_client(handler)
    with pytest.raises(ModelClientError) as ei:
        client.decide([], prompts.build_tool_schemas())
    assert "429" in str(ei.value) or "限流" in str(ei.value)
    assert calls["n"] == 1  # 限流不自动重试


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_retryable_5xx_retried_once(status):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(status, json={"error": "server"})

    client = _make_client(handler, max_retries=1)
    with pytest.raises(ModelClientError):
        client.decide([], prompts.build_tool_schemas())
    assert calls["n"] == 2  # 原始 + 1 次重试


def test_5xx_then_success():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, json={"error": "later"})
        return _ok_text("恢复")

    client = _make_client(handler, max_retries=1)
    resp = client.decide([], prompts.build_tool_schemas())
    assert resp.kind == RESPONSE_FINAL
    assert calls["n"] == 2


def test_connection_error_retried_once():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        raise httpx.ConnectError("refused")

    client = _make_client(handler, max_retries=1)
    with pytest.raises(ModelClientError):
        client.decide([], prompts.build_tool_schemas())
    assert calls["n"] == 2


def test_timeout_retried_once():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        raise httpx.ReadTimeout("timeout")

    client = _make_client(handler, max_retries=1)
    with pytest.raises(ModelClientError):
        client.decide([], prompts.build_tool_schemas())
    assert calls["n"] == 2


def test_no_retry_when_max_retries_zero():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(503, json={"error": "x"})

    client = _make_client(handler, max_retries=0)
    with pytest.raises(ModelClientError):
        client.decide([], prompts.build_tool_schemas())
    assert calls["n"] == 1


# ---------------- 请求构造 ----------------
def test_request_body_uses_tool_schemas():
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return _ok_text()

    client = _make_client(handler)
    client.decide([{"role": "user", "content": "hi"}], prompts.build_tool_schemas())
    body = captured["body"]
    assert body["model"] == "test-model"
    assert body["tool_choice"] == "auto"
    assert isinstance(body["tools"], list)
    names = {t["function"]["name"] for t in body["tools"]}
    assert names == {
        "check_required_fields", "compare_environment_config",
        "compare_api_acceptance", "inspect_acceptance_coverage"}


def test_authorization_header_bearer():
    captured = {}

    def handler(request):
        captured["auth"] = request.headers.get("Authorization")
        return _ok_text()

    client = _make_client(handler)
    client.decide([], prompts.build_tool_schemas())
    assert captured["auth"] == "Bearer sk-test-SECRET"


def test_timeout_passed_to_httpx():
    # 验证 decide 使用的超时确实作为参数传递给 httpx 的 post。
    seen = {}

    class RecordingClient(httpx.Client):
        def post(self, *args, **kwargs):
            seen["timeout"] = kwargs.get("timeout")
            return super().post(*args, **kwargs)

    transport = httpx.MockTransport(lambda r: _ok_text())
    http = RecordingClient(transport=transport)
    client = OpenAICompatibleClient(
        base_url="https://api.example.com/v1",
        api_key="sk-x", model="m", timeout=9.0, http_client=http,
    )
    # decide 显式传入不同 timeout，应优先生效
    client.decide([], prompts.build_tool_schemas(), timeout=3.0)
    assert seen["timeout"] == 3.0


def test_timeout_defaults_to_client_timeout():
    seen = {}

    class RecordingClient(httpx.Client):
        def post(self, *args, **kwargs):
            seen["timeout"] = kwargs.get("timeout")
            return super().post(*args, **kwargs)

    http = RecordingClient(transport=httpx.MockTransport(lambda r: _ok_text()))
    client = OpenAICompatibleClient(
        base_url="https://api.example.com/v1",
        api_key="sk-x", model="m", timeout=11.0, http_client=http,
    )
    client.decide([], prompts.build_tool_schemas())
    assert seen["timeout"] == 11.0


def test_api_key_not_in_error_message():
    def handler(request):
        return httpx.Response(401, json={"error": "unauthorized"})

    client = _make_client(handler)
    with pytest.raises(ModelClientError) as ei:
        client.decide([], prompts.build_tool_schemas())
    assert "sk-test-SECRET" not in str(ei.value)



# ---------------- 与 AgentRunner 集成（多轮工具调用协议） ----------------
def _scripted_real_client(responses):
    """按顺序返回预置 HTTP 响应的真实客户端工厂（仍走 OpenAI 协议）。"""
    state = {"i": 0, "requests": []}

    def handler(request):
        state["requests"].append(json.loads(request.content))
        idx = state["i"]
        state["i"] += 1
        return responses[idx]()

    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport)
    client = OpenAICompatibleClient(
        base_url="https://api.example.com/v1",
        api_key="sk-test", model="m", timeout=5.0, http_client=http,
    )
    return client, state


def test_real_client_multi_round_tool_call_protocol(ok_delivery):
    from app.agent.runner import AgentRunner
    from tests.conftest import load_scenario

    delivery = load_scenario("scenario_ok")
    responses = [
        lambda: _tool_call_response("check_required_fields", "call_r1",
                                    {"delivery": delivery}),
        lambda: _ok_text("核验完成"),
    ]
    client, state = _scripted_real_client(responses)
    runner = AgentRunner(client, required_tools=["check_required_fields"])
    res = runner.run(delivery)

    assert res.final_status == "READY"
    assert res.model_mode == "real"
    second_req = state["requests"][1]
    roles = [m["role"] for m in second_req["messages"]]
    assert "assistant" in roles and "tool" in roles
    assistant_msgs = [m for m in second_req["messages"] if m["role"] == "assistant"]
    tc = assistant_msgs[0]["tool_calls"][0]
    assert tc["type"] == "function"
    assert tc["function"]["name"] == "check_required_fields"
    assert isinstance(tc["function"]["arguments"], str)
    tool_msgs = [m for m in second_req["messages"] if m["role"] == "tool"]
    assert tool_msgs[0]["tool_call_id"] == "call_r1"


def test_real_client_model_call_id_separate_from_tool_event_id(ok_delivery):
    from app.agent.runner import AgentRunner
    from tests.conftest import load_scenario

    delivery = load_scenario("scenario_ok")
    responses = [
        lambda: _tool_call_response("check_required_fields", "MODEL-CALL-ID",
                                    {"delivery": delivery}),
        lambda: _ok_text("done"),
    ]
    client, _ = _scripted_real_client(responses)
    runner = AgentRunner(client, required_tools=["check_required_fields"])
    res = runner.run(delivery)
    assert res.tool_events[0].tool_event_id == "TE-0001"
    assert res.tool_events[0].tool_event_id != "MODEL-CALL-ID"


def test_real_client_multiple_tool_calls_in_one_response(ok_delivery):
    from app.agent.runner import AgentRunner
    from tests.conftest import load_scenario

    delivery = load_scenario("scenario_ok")

    def multi():
        return httpx.Response(
            200,
            json={"choices": [{"message": {
                "role": "assistant", "content": None,
                "tool_calls": [
                    {"id": "mc1", "type": "function",
                     "function": {"name": "check_required_fields",
                                  "arguments": json.dumps({"delivery": delivery})}},
                    {"id": "mc2", "type": "function",
                     "function": {"name": "compare_environment_config",
                                  "arguments": json.dumps({"delivery": delivery})}},
                ]}}]},
        )

    responses = [multi, lambda: _ok_text("done")]
    client, state = _scripted_real_client(responses)
    runner = AgentRunner(
        client, required_tools=["check_required_fields", "compare_environment_config"]
    )
    res = runner.run(delivery)
    assert res.final_status == "READY"
    assert res.tool_calls_used == 2
    second_req = state["requests"][1]
    tool_ids = [m["tool_call_id"] for m in second_req["messages"] if m["role"] == "tool"]
    assert set(tool_ids) == {"mc1", "mc2"}



def test_real_client_final_status_not_from_model(ok_delivery):
    from app.agent.runner import AgentRunner
    from app.schemas import contracts
    from tests.conftest import load_scenario

    delivery = load_scenario("scenario_missing")
    tools = list(contracts.ALL_TOOLS)
    state = {"i": 0}

    def handler(request):
        idx = state["i"]
        state["i"] += 1
        if idx < len(tools):
            name = tools[idx]
            return _tool_call_response(name, f"c{idx}", {"delivery": delivery})
        return _ok_text("最终状态：READY")

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = OpenAICompatibleClient(
        base_url="https://api.example.com/v1", api_key="sk-x", model="m",
        timeout=5.0, http_client=http,
    )
    res = AgentRunner(client).run(delivery)
    assert res.final_status == "BLOCKED"
    assert "READY" in res.model_explanation


def test_real_and_simulation_interchangeable(ok_delivery):
    from app.agent.runner import AgentRunner
    from app.agent.llm_client import SimulationClient
    from tests.conftest import load_scenario

    delivery = load_scenario("scenario_ok")
    sim = AgentRunner(SimulationClient()).run(delivery)
    responses = [lambda: _ok_text("done")]
    client, _ = _scripted_real_client(responses)
    real = AgentRunner(client).run(delivery)
    assert sim.model_mode == "simulation"
    assert real.model_mode == "real"


def test_real_client_error_becomes_model_error_status(ok_delivery):
    from app.agent.runner import AgentRunner
    from tests.conftest import load_scenario

    delivery = load_scenario("scenario_ok")

    def handler(request):
        return httpx.Response(500, json={"error": "boom"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = OpenAICompatibleClient(
        base_url="https://api.example.com/v1", api_key="sk-x", model="m",
        timeout=5.0, http_client=http, max_retries=0,
    )
    res = AgentRunner(client).run(delivery)
    assert res.run_status == "model_error"
    assert res.final_status == "BLOCKED"
    assert res.required_checks_incomplete == 4


def test_simulation_still_no_network(monkeypatch):
    # 阶段 3 引入真实客户端后，模拟模式仍不得发起任何网络请求
    import socket

    def _blocked(*a, **k):
        raise AssertionError("模拟模式不应发起网络请求")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    from app.agent import run_verification
    from tests.conftest import load_scenario

    res = run_verification(load_scenario("scenario_ok"))
    assert res.model_mode == "simulation"
    assert res.final_status == "READY"

