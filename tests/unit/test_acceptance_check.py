"""compare_api_acceptance 与 inspect_acceptance_coverage 工具测试。"""

from __future__ import annotations

from app.tools.acceptance_check import (
    compare_api_acceptance,
    inspect_acceptance_coverage,
)
from tests.conftest import load_scenario


def _base():
    return load_scenario("scenario_ok")


def _codes(res):
    return [f["code"] for f in res["findings"]]


# ---------------- compare_api_acceptance ----------------
def test_api_ok_scenario_no_findings():
    res = compare_api_acceptance(_base())
    assert res["tool"] == "compare_api_acceptance"
    assert res["ok"] is True
    assert res["findings"] == []


def test_related_api_not_found_is_blocker():
    res = compare_api_acceptance(load_scenario("scenario_api_conflict"))
    assert "API_ENDPOINT_NOT_FOUND" in _codes(res)
    f = next(x for x in res["findings"] if x["code"] == "API_ENDPOINT_NOT_FOUND")
    assert f["severity"] == "blocker"
    assert f["evidence"]["actual"] == "submit_order"


def test_version_conflict_is_blocker():
    d = _base()
    d["api_contracts"][0]["path"] = "/api/v2/orders"  # version=v1 但 path 是 v2
    res = compare_api_acceptance(d)
    assert "API_VERSION_CONFLICT" in _codes(res)
    f = next(x for x in res["findings"] if x["code"] == "API_VERSION_CONFLICT")
    assert f["severity"] == "blocker"


def test_auth_unclear_is_warning():
    d = _base()
    d["api_contracts"][0]["auth"] = ""  # 空 auth
    res = compare_api_acceptance(d)
    assert "API_AUTH_UNCLEAR" in _codes(res)
    f = next(x for x in res["findings"] if x["code"] == "API_AUTH_UNCLEAR")
    assert f["severity"] == "warning"


def test_auth_invalid_value_is_warning():
    d = _base()
    d["api_contracts"][0]["auth"] = "oauth_magic"  # 不在允许集合
    res = compare_api_acceptance(d)
    assert "API_AUTH_UNCLEAR" in _codes(res)


def test_api_non_list_is_invalid_input():
    d = _base()
    d["api_contracts"] = "not-a-list"
    res = compare_api_acceptance(d)
    assert res["ok"] is False
    assert res["error"]["code"] == "INVALID_INPUT"


# ---------------- inspect_acceptance_coverage ----------------
def test_coverage_ok_scenario_no_findings():
    res = inspect_acceptance_coverage(_base())
    assert res["tool"] == "inspect_acceptance_coverage"
    assert res["ok"] is True
    assert res["findings"] == []


def test_critical_gap_is_blocker():
    res = inspect_acceptance_coverage(load_scenario("scenario_coverage_gap"))
    assert "COVERAGE_CRITICAL_GAP" in _codes(res)
    f = next(x for x in res["findings"] if x["code"] == "COVERAGE_CRITICAL_GAP")
    assert f["severity"] == "blocker"


def test_normal_gap_is_warning():
    d = _base()
    d["acceptance_criteria"][0]["severity"] = "normal"
    d["verification_steps"] = []
    res = inspect_acceptance_coverage(d)
    assert "COVERAGE_NORMAL_GAP" in _codes(res)
    f = next(x for x in res["findings"] if x["code"] == "COVERAGE_NORMAL_GAP")
    assert f["severity"] == "warning"


def test_orphan_step_is_warning():
    d = _base()
    d["verification_steps"].append(
        {"step_id": "VS-2", "criterion_id": "AC-999", "action": "不存在的要求"}
    )
    res = inspect_acceptance_coverage(d)
    assert "COVERAGE_ORPHAN_STEP" in _codes(res)
    f = next(x for x in res["findings"] if x["code"] == "COVERAGE_ORPHAN_STEP")
    assert f["severity"] == "warning"


def test_coverage_non_list_is_invalid_input():
    d = _base()
    d["verification_steps"] = None
    res = inspect_acceptance_coverage(d)
    assert res["ok"] is False
    assert res["error"]["code"] == "INVALID_INPUT"
