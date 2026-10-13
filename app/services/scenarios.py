"""固定核验场景目录服务（对齐 docs/DATA_CONTRACTS.md 第 2、7.2 节）。

安全约束：
- 只从预置目录 data/sample_packages 加载场景。
- scenario_id 必须精确匹配内置目录白名单，绝不把用户输入拼接成任意文件路径。
- 场景读取/解析失败抛 ScenarioLoadError（映射 500），绝不把无法加载的资料
  当作核验通过。
"""

from __future__ import annotations

from pathlib import Path

import yaml

# app/services -> app -> 项目根
SAMPLES_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "sample_packages"

# 内置场景目录：id、人读名称、期望最终状态（固定口径，来自 DATA_CONTRACTS.md 第 2 节）。
# expected_status 仅是样例说明元数据；POST /api/runs 的最终状态始终由
# compute_final_status 确定性计算，绝不由本目录决定。
CATALOG: tuple[tuple[str, str, str], ...] = (
    ("scenario_ok", "完整且无冲突", "READY"),
    ("scenario_missing", "必需信息缺失", "BLOCKED"),
    ("scenario_env_mismatch", "环境配置与目标要求不一致", "BLOCKED"),
    ("scenario_api_conflict", "接口契约与验收要求引用冲突", "BLOCKED"),
    ("scenario_coverage_gap", "关键验收要求缺少验证步骤", "BLOCKED"),
)


class ScenarioNotFoundError(Exception):
    """场景 id 不在白名单目录中（映射 404）。"""


class ScenarioLoadError(Exception):
    """场景文件缺失或解析失败（映射 500）——不得视为核验通过。"""


def _file_exists(scenario_id: str) -> bool:
    return (SAMPLES_DIR / f"{scenario_id}.yaml").is_file()


def list_scenarios() -> list[dict]:
    """返回目录中文件真实存在的场景条目。一个都没有时抛 ScenarioLoadError。"""
    entries = [
        {"id": sid, "name": name, "expected_status": expected}
        for sid, name, expected in CATALOG
        if _file_exists(sid)
    ]
    if not entries:
        raise ScenarioLoadError("未找到任何可用场景样例")
    return entries


def load_scenario(scenario_id: str) -> dict:
    """按白名单 id 加载场景 YAML，返回交付资料 dict。

    未知 id 抛 ScenarioNotFoundError；文件缺失或解析失败抛 ScenarioLoadError。
    """
    if not isinstance(scenario_id, str) or scenario_id not in {
        sid for sid, _, _ in CATALOG
    }:
        raise ScenarioNotFoundError(scenario_id)
    if not _file_exists(scenario_id):
        raise ScenarioLoadError(f"场景文件缺失：{scenario_id}.yaml")
    path = SAMPLES_DIR / f"{scenario_id}.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - 解析失败必须显式暴露
        raise ScenarioLoadError(f"场景 {scenario_id} 解析失败：{exc}") from exc
    if not isinstance(data, dict):
        raise ScenarioLoadError(f"场景 {scenario_id} 内容不是合法交付资料对象")
    return data
