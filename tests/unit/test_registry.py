"""工具注册入口 registry 测试：白名单、未知工具、无效参数、执行失败。"""

from __future__ import annotations

from app.tools import registry
from tests.conftest import load_scenario


def test_list_tools_returns_four_known_tools():
    assert registry.list_tools() == [
        "check_required_fields",
        "compare_environment_config",
        "compare_api_acceptance",
        "inspect_acceptance_coverage",
    ]


def test_call_known_tool_returns_unified_ok_result():
    res = registry.call_tool(
        "check_required_fields", {"delivery": load_scenario("scenario_ok")}
    )
    assert res["ok"] is True
    assert res["tool"] == "check_required_fields"
    assert res["findings"] == []


def test_unknown_tool_returns_unknown_tool_error():
    res = registry.call_tool("drop_database", {"delivery": {}})
    assert res["ok"] is False
    assert res["findings"] == []
    assert res["error"]["code"] == "UNKNOWN_TOOL"


def test_non_string_tool_name_is_unknown_tool():
    res = registry.call_tool(123, {"delivery": {}})
    assert res["ok"] is False
    assert res["error"]["code"] == "UNKNOWN_TOOL"


def test_params_not_dict_is_invalid_params():
    res = registry.call_tool("check_required_fields", "oops")
    assert res["ok"] is False
    assert res["error"]["code"] == "INVALID_PARAMS"


def test_params_missing_delivery_is_invalid_params():
    res = registry.call_tool("check_required_fields", {"foo": 1})
    assert res["ok"] is False
    assert res["error"]["code"] == "INVALID_PARAMS"


def test_registry_does_not_expose_arbitrary_callables():
    # 注册表只能按固定名称找到工具，无法通过参数指定任意函数
    assert registry.has_tool("check_required_fields") is True
    assert registry.has_tool("os.system") is False
    assert registry.has_tool("eval") is False


def test_tool_error_is_wrapped_not_raised(monkeypatch):
    # 模拟工具内部抛异常：registry 必须兜底为 TOOL_ERROR，不向上抛
    def _boom(_delivery):
        raise RuntimeError("boom")

    monkeypatch.setitem(registry._REGISTRY, "check_required_fields", _boom)
    res = registry.call_tool("check_required_fields", {"delivery": {}})
    assert res["ok"] is False
    assert res["error"]["code"] == "TOOL_ERROR"
    assert res["findings"] == []


def test_invalid_delivery_propagates_as_tool_invalid_input():
    # 合法参数名但 delivery 非法 -> 工具层返回 INVALID_INPUT
    res = registry.call_tool("compare_environment_config", {"delivery": 42})
    assert res["ok"] is False
    assert res["error"]["code"] == "INVALID_INPUT"
