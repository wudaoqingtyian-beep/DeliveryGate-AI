"""compute_final_status 测试：三种状态、判定优先级、关键检查未完成。"""

from __future__ import annotations

from app.schemas import contracts
from app.services.final_status import (
    compute_final_status,
    count_required_checks_incomplete,
)


def _all_tools_ok():
    return {
        t: {"ok": True, "findings": []}
        for t in contracts.ALL_TOOLS
    }


def _ctx(tool_results):
    return {"tool_results": tool_results}


def _blocker(code="X", certainty="confirmed"):
    return {
        "severity": "blocker",
        "certainty": certainty,
        "code": code,
        "message": "m",
        "evidence": {"source": "s", "expected": None, "actual": None},
        "remediation": "",
    }


def _warning(code="X", certainty="confirmed"):
    return {
        "severity": "warning",
        "certainty": certainty,
        "code": code,
        "message": "m",
        "evidence": {"source": "s", "expected": None, "actual": None},
        "remediation": "",
    }


def test_ready_when_all_pass_and_checks_complete():
    fs = compute_final_status([], _ctx(_all_tools_ok()))
    assert fs == "READY"


def test_needs_review_when_only_warnings():
    fs = compute_final_status([_warning()], _ctx(_all_tools_ok()))
    assert fs == "NEEDS_REVIEW"


def test_needs_review_when_uncertain_warning():
    fs = compute_final_status([_warning(certainty="uncertain")], _ctx(_all_tools_ok()))
    assert fs == "NEEDS_REVIEW"


def test_blocked_when_confirmed_blocker():
    fs = compute_final_status([_blocker()], _ctx(_all_tools_ok()))
    assert fs == "BLOCKED"


def test_blocker_beats_review_priority():
    # blocker 与 warning 同时存在 -> BLOCKED
    fs = compute_final_status([_blocker(), _warning()], _ctx(_all_tools_ok()))
    assert fs == "BLOCKED"


def test_incomplete_check_cannot_be_ready():
    # 关键检查未完成（缺一个工具）-> 即使无 finding 也不能 READY
    tools = _all_tools_ok()
    del tools["inspect_acceptance_coverage"]
    fs = compute_final_status([], _ctx(tools))
    assert fs == "BLOCKED"
    assert count_required_checks_incomplete(_ctx(tools)) == 1


def test_failed_tool_counts_as_incomplete():
    tools = _all_tools_ok()
    tools["compare_environment_config"] = {"ok": False, "findings": []}
    fs = compute_final_status([], _ctx(tools))
    assert fs == "BLOCKED"


def test_incomplete_beats_blocker_priority():
    # 关键检查未完成 + 有 blocker：优先级 1 命中，仍 BLOCKED
    tools = _all_tools_ok()
    del tools["check_required_fields"]
    fs = compute_final_status([_blocker()], _ctx(tools))
    assert fs == "BLOCKED"


def test_uncertain_blocker_alone_not_blocked():
    # 契约：blockers 只统计 certainty=confirmed；uncertain 的 blocker 不算 blocker
    # 这里仅确认一个 uncertain 的"blocker"不会因 severity=blocker 触发 BLOCKED
    findings = [_blocker(certainty="uncertain")]
    fs = compute_final_status(findings, _ctx(_all_tools_ok()))
    # 它既不是 confirmed blocker，也不是 warning，按契约无信号 -> READY
    assert fs == "READY"


def test_empty_context_all_incomplete_is_blocked():
    # 空 run_context：四个必需检查全部未完成
    fs = compute_final_status([], {})
    assert fs == "BLOCKED"
    assert count_required_checks_incomplete({}) == 4


def test_deterministic_same_input_same_output():
    findings = [_blocker(), _warning()]
    ctx = _ctx(_all_tools_ok())
    a = compute_final_status(findings, ctx)
    b = compute_final_status(findings, ctx)
    assert a == b == "BLOCKED"


def test_status_values_are_frozen_enum():
    assert contracts.STATUS_READY == "READY"
    assert contracts.STATUS_NEEDS_REVIEW == "NEEDS_REVIEW"
    assert contracts.STATUS_BLOCKED == "BLOCKED"


# ---------------- run_context.required_tools 语义（阶段 1.1 收尾） ----------------
def test_required_tools_explicit_subset_ready():
    # 仅要求两个工具且都成功完成 -> READY；未执行的非必需工具不影响
    tools = _all_tools_ok()
    ctx = {
        "required_tools": ["check_required_fields", "compare_environment_config"],
        "tool_results": {
            "check_required_fields": tools["check_required_fields"],
            "compare_environment_config": tools["compare_environment_config"],
        },
    }
    assert compute_final_status([], ctx) == "READY"
    assert count_required_checks_incomplete(ctx) == 0


def test_required_tool_not_executed_is_blocked():
    # 必需但未执行（tool_results 中无该键）-> 不能 READY
    tools = _all_tools_ok()
    ctx = {
        "required_tools": ["check_required_fields", "compare_environment_config"],
        "tool_results": {
            "check_required_fields": tools["check_required_fields"],
            # compare_environment_config 未执行
        },
    }
    assert compute_final_status([], ctx) == "BLOCKED"
    assert count_required_checks_incomplete(ctx) == 1


def test_non_required_tool_not_executed_does_not_block():
    # 非必需且未执行的工具不影响最终状态（区别于"必需但未完成"）
    tools = _all_tools_ok()
    ctx = {
        "required_tools": ["check_required_fields"],
        "tool_results": {
            "check_required_fields": tools["check_required_fields"],
            "compare_api_acceptance": tools["compare_api_acceptance"],
        },
    }
    assert compute_final_status([], ctx) == "READY"
    assert count_required_checks_incomplete(ctx) == 0


def test_required_tool_failed_vs_not_called_both_incomplete():
    # 工具失败（ok=false）与未调用（键缺失）都算未完成
    tools = _all_tools_ok()
    tools["compare_environment_config"] = {"ok": False, "findings": []}
    ctx = {
        "required_tools": [
            "check_required_fields",
            "compare_environment_config",
            "inspect_acceptance_coverage",
        ],
        "tool_results": {
            "check_required_fields": tools["check_required_fields"],
            "compare_environment_config": tools["compare_environment_config"],
            # inspect_acceptance_coverage 未调用
        },
    }
    # 1 个失败 + 1 个未调用 = 2 个未完成
    assert count_required_checks_incomplete(ctx) == 2
    assert compute_final_status([], ctx) == "BLOCKED"


def test_invalid_required_tools_fall_back_to_all():
    # required_tools 非法（空列表/非列表）时回落到全部四个工具
    tools = _all_tools_ok()
    ctx = {"required_tools": [], "tool_results": tools}
    assert count_required_checks_incomplete(ctx) == 0  # 回落为全部且都完成

    ctx2 = {"required_tools": "not-a-list", "tool_results": tools}
    assert count_required_checks_incomplete(ctx2) == 0


def test_unknown_tool_in_required_tools_is_ignored():
    # required_tools 中出现白名单外的工具名应被忽略，不计入未完成
    tools = _all_tools_ok()
    ctx = {
        "required_tools": ["check_required_fields", "nonexistent_tool"],
        "tool_results": {"check_required_fields": tools["check_required_fields"]},
    }
    assert count_required_checks_incomplete(ctx) == 0
    assert compute_final_status([], ctx) == "READY"


def test_backward_compatible_default_required_is_all_tools():
    # 不传 required_tools 时，缺省即全部四个工具（向后兼容阶段 1 行为）
    tools = _all_tools_ok()
    ctx = {"tool_results": tools}
    assert compute_final_status([], ctx) == "READY"
    del tools["inspect_acceptance_coverage"]
    assert compute_final_status([], {"tool_results": tools}) == "BLOCKED"

