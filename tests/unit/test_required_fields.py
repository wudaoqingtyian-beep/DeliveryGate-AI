"""check_required_fields 工具测试：正常、缺失、边界。"""

from __future__ import annotations

import copy

from app.tools.required_fields import check_required_fields
from tests.conftest import load_scenario


def _base():
    return load_scenario("scenario_ok")


def test_ok_scenario_no_findings():
    res = check_required_fields(_base())
    assert res["tool"] == "check_required_fields"
    assert res["ok"] is True
    assert res["findings"] == []


def test_missing_contact_is_blocker():
    d = _base()
    del d["contact"]
    res = check_required_fields(d)
    assert res["ok"] is True
    assert len(res["findings"]) == 1
    f = res["findings"][0]
    assert f["severity"] == "blocker"
    assert f["certainty"] == "confirmed"
    assert f["code"] == "MISSING_REQUIRED_FIELD"
    assert f["evidence"]["source"] == "delivery.contact"


def test_empty_string_is_missing():
    d = _base()
    d["project_name"] = "   "
    res = check_required_fields(d)
    codes = [f["evidence"]["source"] for f in res["findings"]]
    assert "delivery.project_name" in codes


def test_empty_list_is_missing():
    d = _base()
    d["api_contracts"] = []
    res = check_required_fields(d)
    sources = [f["evidence"]["source"] for f in res["findings"]]
    assert "delivery.api_contracts" in sources


def test_null_value_is_missing():
    d = _base()
    d["version"] = None
    res = check_required_fields(d)
    sources = [f["evidence"]["source"] for f in res["findings"]]
    assert "delivery.version" in sources


def test_multiple_missing_fields_all_reported():
    d = _base()
    del d["contact"]
    del d["delivery_id"]
    res = check_required_fields(d)
    assert len(res["findings"]) == 2


def test_optional_subfield_absence_not_flagged():
    # actual_deployment 缺少可选子字段 environment_name 不应被本工具报缺失
    d = _base()
    del d["actual_deployment"]["environment_name"]
    res = check_required_fields(d)
    assert res["findings"] == []


def test_non_object_delivery_is_invalid_input():
    res = check_required_fields("not-a-dict")
    assert res["ok"] is False
    assert res["findings"] == []
    assert res["error"]["code"] == "INVALID_INPUT"


def test_all_required_fields_present_returns_empty():
    # 每个必填顶层字段逐一验证：存在即不报
    d = _base()
    for field in (
        "delivery_id", "project_name", "version", "contact",
        "target_environment", "actual_deployment", "api_contracts",
        "acceptance_criteria", "verification_steps",
    ):
        assert field in d
    assert check_required_fields(copy.deepcopy(d))["findings"] == []
