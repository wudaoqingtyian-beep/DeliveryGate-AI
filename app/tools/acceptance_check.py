"""验收相关两个工具：compare_api_acceptance 与 inspect_acceptance_coverage。

对齐 docs/DATA_CONTRACTS.md：

3.3 compare_api_acceptance（接口契约 vs 验收检查项）
- related_api 指向的 endpoint_id 不存在 → blocker / API_ENDPOINT_NOT_FOUND
- path 与 api_version 前缀不一致 → blocker / API_VERSION_CONFLICT
- auth 为空或不在允许集合（none/api_key/bearer_token）→ warning /
  API_AUTH_UNCLEAR
- 全部 certainty=confirmed
- 失败：api_contracts 或 acceptance_criteria 非列表 → INVALID_INPUT

3.4 inspect_acceptance_coverage（验收要求是否有验证步骤）
- critical 验收要求无验证步骤 → blocker / COVERAGE_CRITICAL_GAP
- normal 验收要求无验证步骤 → warning / COVERAGE_NORMAL_GAP
- verification_steps[].criterion_id 指向不存在的验收要求 → warning /
  COVERAGE_ORPHAN_STEP
- 全部 certainty=confirmed
- 失败：acceptance_criteria 或 verification_steps 非列表 → INVALID_INPUT

确定性纯函数，只读，无副作用。
"""

from __future__ import annotations

from app.schemas import contracts


def _as_list(value):
    return value if isinstance(value, list) else []


# ---------------------------------------------------------------- 3.3
def compare_api_acceptance(delivery) -> dict:
    tool = contracts.TOOL_COMPARE_API_ACCEPTANCE

    if not isinstance(delivery, dict):
        return contracts.error_result(
            tool, contracts.ERROR_INVALID_INPUT, "delivery 不是合法对象"
        )

    contracts_list = delivery.get("api_contracts")
    criteria_list = delivery.get("acceptance_criteria")
    if not isinstance(contracts_list, list) or not isinstance(criteria_list, list):
        return contracts.error_result(
            tool,
            contracts.ERROR_INVALID_INPUT,
            "api_contracts 或 acceptance_criteria 不是列表",
        )

    findings = []

    endpoint_ids = {
        c.get("endpoint_id")
        for c in contracts_list
        if isinstance(c, dict)
    }

    # 1) 验收要求引用的端点必须存在于契约中
    for crit in criteria_list:
        if not isinstance(crit, dict):
            continue
        related = crit.get("related_api")
        if related not in endpoint_ids:
            findings.append(
                contracts.make_finding(
                    severity=contracts.SEVERITY_BLOCKER,
                    certainty=contracts.CERTAINTY_CONFIRMED,
                    code="API_ENDPOINT_NOT_FOUND",
                    message=(
                        f"验收要求 {crit.get('criterion_id')} 引用的端点 "
                        f"{related} 在接口契约中不存在"
                    ),
                    source=f"acceptance_criteria[{crit.get('criterion_id')}].related_api",
                    expected="存在于 api_contracts 的 endpoint_id",
                    actual=related,
                    remediation="修正 related_api，或在 api_contracts 中补充该端点定义",
                )
            )

    # 2) path 与 api_version 前缀一致性
    for c in contracts_list:
        if not isinstance(c, dict):
            continue
        version = c.get("api_version")
        path = c.get("path")
        if isinstance(version, str) and version.strip():
            expected_prefix = f"/api/{version.strip()}/"
            if not (isinstance(path, str) and path.startswith(expected_prefix)):
                findings.append(
                    contracts.make_finding(
                        severity=contracts.SEVERITY_BLOCKER,
                        certainty=contracts.CERTAINTY_CONFIRMED,
                        code="API_VERSION_CONFLICT",
                        message=(
                            f"端点 {c.get('endpoint_id')} 的 path 与 api_version "
                            f"前缀不一致"
                        ),
                        source=f"api_contracts[{c.get('endpoint_id')}].path",
                        expected=f"以 {expected_prefix} 开头",
                        actual=path,
                        remediation="修正 path 或 api_version，使版本前缀一致",
                    )
                )

    # 3) auth 明确性
    for c in contracts_list:
        if not isinstance(c, dict):
            continue
        auth = c.get("auth")
        if auth not in contracts.ALLOWED_AUTH:
            findings.append(
                contracts.make_finding(
                    severity=contracts.SEVERITY_WARNING,
                    certainty=contracts.CERTAINTY_CONFIRMED,
                    code="API_AUTH_UNCLEAR",
                    message=(
                        f"端点 {c.get('endpoint_id')} 的认证要求为空或不在允许集合"
                    ),
                    source=f"api_contracts[{c.get('endpoint_id')}].auth",
                    expected=" / ".join(contracts.ALLOWED_AUTH),
                    actual=auth,
                    remediation="明确该端点的认证要求（none/api_key/bearer_token）",
                )
            )

    return contracts.ok_result(tool, findings)


# ---------------------------------------------------------------- 3.4
def inspect_acceptance_coverage(delivery) -> dict:
    tool = contracts.TOOL_INSPECT_ACCEPTANCE_COVERAGE

    if not isinstance(delivery, dict):
        return contracts.error_result(
            tool, contracts.ERROR_INVALID_INPUT, "delivery 不是合法对象"
        )

    criteria_list = delivery.get("acceptance_criteria")
    steps_list = delivery.get("verification_steps")
    if not isinstance(criteria_list, list) or not isinstance(steps_list, list):
        return contracts.error_result(
            tool,
            contracts.ERROR_INVALID_INPUT,
            "acceptance_criteria 或 verification_steps 不是列表",
        )

    findings = []

    # 已有验证步骤覆盖到的 criterion_id 集合
    covered = {
        s.get("criterion_id")
        for s in steps_list
        if isinstance(s, dict)
    }
    # 合法验收要求 id 集合（用于孤儿步骤检测）
    criterion_ids = {
        c.get("criterion_id")
        for c in criteria_list
        if isinstance(c, dict)
    }

    # 1) 每个验收要求是否被验证步骤覆盖
    for crit in criteria_list:
        if not isinstance(crit, dict):
            continue
        cid = crit.get("criterion_id")
        if cid in covered:
            continue
        severity = crit.get("severity")
        if severity == contracts.CRITICALITY_CRITICAL:
            findings.append(
                contracts.make_finding(
                    severity=contracts.SEVERITY_BLOCKER,
                    certainty=contracts.CERTAINTY_CONFIRMED,
                    code="COVERAGE_CRITICAL_GAP",
                    message=f"关键验收要求 {cid} 缺少对应的验证步骤",
                    source=f"acceptance_criteria[{cid}]",
                    expected="至少一条 verification_steps.criterion_id 指向该要求",
                    actual=None,
                    remediation=f"为关键验收要求 {cid} 补充验证步骤",
                )
            )
        else:
            findings.append(
                contracts.make_finding(
                    severity=contracts.SEVERITY_WARNING,
                    certainty=contracts.CERTAINTY_CONFIRMED,
                    code="COVERAGE_NORMAL_GAP",
                    message=f"验收要求 {cid} 缺少对应的验证步骤",
                    source=f"acceptance_criteria[{cid}]",
                    expected="至少一条 verification_steps.criterion_id 指向该要求",
                    actual=None,
                    remediation=f"为验收要求 {cid} 补充验证步骤",
                )
            )

    # 2) 孤儿验证步骤：指向不存在的验收要求
    for step in steps_list:
        if not isinstance(step, dict):
            continue
        scid = step.get("criterion_id")
        if scid not in criterion_ids:
            findings.append(
                contracts.make_finding(
                    severity=contracts.SEVERITY_WARNING,
                    certainty=contracts.CERTAINTY_CONFIRMED,
                    code="COVERAGE_ORPHAN_STEP",
                    message=(
                        f"验证步骤 {step.get('step_id')} 引用的验收要求 "
                        f"{scid} 不存在"
                    ),
                    source=f"verification_steps[{step.get('step_id')}].criterion_id",
                    expected="指向已存在的 acceptance_criteria.criterion_id",
                    actual=scid,
                    remediation="修正 criterion_id，或补充对应的验收要求",
                )
            )

    return contracts.ok_result(tool, findings)

