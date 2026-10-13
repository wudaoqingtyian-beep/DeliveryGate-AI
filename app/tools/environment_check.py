"""工具 compare_environment_config：比较目标环境要求与实际部署配置。

对齐 docs/DATA_CONTRACTS.md 第 3.2 节（severity 已冻结）：
- runtime 不一致 → blocker / ENV_RUNTIME_MISMATCH
- python_version 不一致 → blocker / ENV_PYTHON_VERSION_MISMATCH
- os 不一致 → warning / ENV_OS_MISMATCH
- required=true 的变量未出现在 env_vars_present → blocker /
  ENV_REQUIRED_VAR_MISSING
- 每条差异 certainty=confirmed
- 失败：任一环境对象缺失或非对象 → INVALID_INPUT。
确定性纯函数，只读，无副作用。
"""

from __future__ import annotations

from app.schemas import contracts


def _as_dict(value):
    return value if isinstance(value, dict) else None


def _as_list(value):
    return value if isinstance(value, list) else []


def compare_environment_config(delivery) -> dict:
    tool = contracts.TOOL_COMPARE_ENVIRONMENT

    if not isinstance(delivery, dict):
        return contracts.error_result(
            tool, contracts.ERROR_INVALID_INPUT, "delivery 不是合法对象"
        )

    target = _as_dict(delivery.get("target_environment"))
    actual = _as_dict(delivery.get("actual_deployment"))
    if target is None or actual is None:
        return contracts.error_result(
            tool,
            contracts.ERROR_INVALID_INPUT,
            "target_environment 或 actual_deployment 缺失或不是对象",
        )

    findings = []

    # runtime：不一致 = blocker
    t_runtime = target.get("runtime")
    a_runtime = actual.get("runtime")
    if t_runtime != a_runtime:
        findings.append(
            contracts.make_finding(
                severity=contracts.SEVERITY_BLOCKER,
                certainty=contracts.CERTAINTY_CONFIRMED,
                code="ENV_RUNTIME_MISMATCH",
                message="运行时类型与目标要求不一致",
                source="actual_deployment.runtime",
                expected=t_runtime,
                actual=a_runtime,
                remediation="将实际部署的运行时调整为与目标环境一致",
            )
        )

    # python_version：不一致 = blocker
    t_pyver = target.get("python_version")
    a_pyver = actual.get("python_version")
    if t_pyver != a_pyver:
        findings.append(
            contracts.make_finding(
                severity=contracts.SEVERITY_BLOCKER,
                certainty=contracts.CERTAINTY_CONFIRMED,
                code="ENV_PYTHON_VERSION_MISMATCH",
                message="Python 版本与目标要求不一致",
                source="actual_deployment.python_version",
                expected=t_pyver,
                actual=a_pyver,
                remediation="将实际部署的 Python 版本调整为与目标环境一致",
            )
        )

    # os：不一致 = warning
    t_os = target.get("os")
    a_os = actual.get("os")
    if t_os != a_os:
        findings.append(
            contracts.make_finding(
                severity=contracts.SEVERITY_WARNING,
                certainty=contracts.CERTAINTY_CONFIRMED,
                code="ENV_OS_MISMATCH",
                message="操作系统与目标要求不一致",
                source="actual_deployment.os",
                expected=t_os,
                actual=a_os,
                remediation="确认操作系统差异是否影响部署，必要时对齐目标环境",
            )
        )

    # required_env_vars：required=true 的变量必须出现在 env_vars_present
    present = set(_as_list(actual.get("env_vars_present")))
    for var in _as_list(target.get("required_env_vars")):
        if not isinstance(var, dict):
            continue
        if var.get("required") is not True:
            continue
        name = var.get("name")
        if name not in present:
            findings.append(
                contracts.make_finding(
                    severity=contracts.SEVERITY_BLOCKER,
                    certainty=contracts.CERTAINTY_CONFIRMED,
                    code="ENV_REQUIRED_VAR_MISSING",
                    message=f"必需环境变量 {name} 未在实际部署中配置",
                    source=f"target_environment.required_env_vars[{name}]",
                    expected=name,
                    actual=None,
                    remediation=f"在实际部署中配置环境变量 {name}",
                )
            )

    return contracts.ok_result(tool, findings)
