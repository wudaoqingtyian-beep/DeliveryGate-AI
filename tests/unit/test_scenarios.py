"""五个固定场景的端到端集成测试。

模拟核心回归流程的确定性核验部分：
加载场景 -> 经 registry 调用四个工具 -> 汇总 finding 与完成情况
-> 计算最终状态 -> 校验结果与证据来源。
"""

from __future__ import annotations

import pytest

from app.services.final_status import compute_final_status
from app.tools import registry
from tests.conftest import SCENARIO_EXPECTED_STATUS, list_scenarios, load_scenario


def _run_scenario(name):
    delivery = load_scenario(name)
    tool_results = {
        t: registry.call_tool(t, {"delivery": delivery})
        for t in registry.list_tools()
    }
    findings = [f for r in tool_results.values() for f in r["findings"]]
    final = compute_final_status(findings, {"tool_results": tool_results})
    return delivery, tool_results, findings, final


def test_all_five_scenarios_exist():
    scenarios = set(list_scenarios())
    assert set(SCENARIO_EXPECTED_STATUS).issubset(scenarios)
    assert len(SCENARIO_EXPECTED_STATUS) == 5


@pytest.mark.parametrize("name,expected", sorted(SCENARIO_EXPECTED_STATUS.items()))
def test_scenario_final_status(name, expected):
    _, _, _, final = _run_scenario(name)
    assert final == expected


def test_scenario_ok_has_no_findings():
    _, _, findings, final = _run_scenario("scenario_ok")
    assert findings == []
    assert final == "READY"


def test_scenario_missing_reports_expected_codes():
    _, _, findings, final = _run_scenario("scenario_missing")
    codes = {f["code"] for f in findings}
    assert "MISSING_REQUIRED_FIELD" in codes
    assert "ENV_REQUIRED_VAR_MISSING" in codes
    assert final == "BLOCKED"


def test_scenario_api_conflict_reports_endpoint_not_found():
    _, _, findings, final = _run_scenario("scenario_api_conflict")
    codes = {f["code"] for f in findings}
    assert "API_ENDPOINT_NOT_FOUND" in codes
    assert final == "BLOCKED"


def test_scenario_coverage_gap_reports_critical_gap():
    _, _, findings, final = _run_scenario("scenario_coverage_gap")
    codes = {f["code"] for f in findings}
    assert "COVERAGE_CRITICAL_GAP" in codes
    assert final == "BLOCKED"


def test_every_finding_has_evidence_source():
    # 证据来源可追溯：每个 finding 必须带 evidence.source
    for name in SCENARIO_EXPECTED_STATUS:
        _, _, findings, _ = _run_scenario(name)
        for f in findings:
            assert f["evidence"]["source"], f"{name}: finding 缺少 evidence.source"
            assert f["code"]
            assert f["severity"] in ("blocker", "warning", "pass")
            assert f["certainty"] in ("confirmed", "uncertain")


def test_all_tools_succeed_on_all_scenarios():
    for name in SCENARIO_EXPECTED_STATUS:
        _, tool_results, _, _ = _run_scenario(name)
        for tool, res in tool_results.items():
            assert res["ok"] is True, f"{name}/{tool} 未成功执行"


def test_scenario_field_references_are_consistent():
    # 场景内字段关联：related_api -> endpoint_id；criterion_id -> 验收要求
    d = load_scenario("scenario_ok")
    endpoint_ids = {c["endpoint_id"] for c in d["api_contracts"]}
    criterion_ids = {c["criterion_id"] for c in d["acceptance_criteria"]}
    for crit in d["acceptance_criteria"]:
        assert crit["related_api"] in endpoint_ids
    for step in d["verification_steps"]:
        assert step["criterion_id"] in criterion_ids
