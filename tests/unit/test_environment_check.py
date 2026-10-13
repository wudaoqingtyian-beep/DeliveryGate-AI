"""compare_environment_config 工具测试：一致与冲突。"""

from __future__ import annotations

import copy

from app.tools.environment_check import compare_environment_config
from tests.conftest import load_scenario


def _base():
    return load_scenario("scenario_ok")


def _codes(res):
    return [f["code"] for f in res["findings"]]


def test_ok_scenario_no_findings():
    res = compare_environment_config(_base())
    assert res["tool"] == "compare_environment_config"
    assert res["ok"] is True
    assert res["findings"] == []


def test_python_version_mismatch_is_blocker():
    d = _base()
    d["actual_deployment"]["python_version"] = "3.11"
    res = compare_environment_config(d)
    assert "ENV_PYTHON_VERSION_MISMATCH" in _codes(res)
    f = next(x for x in res["findings"] if x["code"] == "ENV_PYTHON_VERSION_MISMATCH")
    assert f["severity"] == "blocker"
    assert f["evidence"]["expected"] == "3.12"
    assert f["evidence"]["actual"] == "3.11"


def test_runtime_mismatch_is_blocker():
    d = _base()
    d["actual_deployment"]["runtime"] = "node"
    res = compare_environment_config(d)
    assert "ENV_RUNTIME_MISMATCH" in _codes(res)


def test_os_mismatch_is_warning():
    d = _base()
    d["actual_deployment"]["os"] = "CentOS 7"
    res = compare_environment_config(d)
    assert "ENV_OS_MISMATCH" in _codes(res)
    f = next(x for x in res["findings"] if x["code"] == "ENV_OS_MISMATCH")
    assert f["severity"] == "warning"


def test_required_env_var_missing_is_blocker():
    d = _base()
    d["actual_deployment"]["env_vars_present"] = []
    res = compare_environment_config(d)
    assert "ENV_REQUIRED_VAR_MISSING" in _codes(res)
    f = next(x for x in res["findings"] if x["code"] == "ENV_REQUIRED_VAR_MISSING")
    assert f["severity"] == "blocker"
    assert f["evidence"]["actual"] is None


def test_optional_env_var_missing_not_flagged():
    d = _base()
    # 增加一个 required=false 的变量，未配置不应报缺失
    d["target_environment"]["required_env_vars"].append(
        {"name": "OPT_VAR", "description": "可选", "required": False}
    )
    res = compare_environment_config(d)
    assert res["findings"] == []


def test_env_mismatch_scenario_reports_version_and_os():
    res = compare_environment_config(load_scenario("scenario_env_mismatch"))
    codes = _codes(res)
    assert "ENV_PYTHON_VERSION_MISMATCH" in codes
    assert "ENV_OS_MISMATCH" in codes


def test_missing_environment_object_is_invalid_input():
    d = _base()
    del d["actual_deployment"]
    res = compare_environment_config(d)
    assert res["ok"] is False
    assert res["error"]["code"] == "INVALID_INPUT"


def test_non_object_delivery_is_invalid_input():
    res = compare_environment_config([1, 2, 3])
    assert res["ok"] is False
    assert res["error"]["code"] == "INVALID_INPUT"


def test_consistent_config_copy_returns_empty():
    assert compare_environment_config(copy.deepcopy(_base()))["findings"] == []
