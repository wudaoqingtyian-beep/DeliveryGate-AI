"""测试公共辅助：加载 data/sample_packages 下的固定场景 YAML。

场景命名与 docs/DATA_CONTRACTS.md 第 2 节一致，作为测试与文档的统一口径。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

# tests/ -> 项目根
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLES_DIR = PROJECT_ROOT / "data" / "sample_packages"

# 五个固定场景及其期望最终状态（对齐 DATA_CONTRACTS.md 第 2 节）
SCENARIO_EXPECTED_STATUS = {
    "scenario_ok": "READY",
    "scenario_missing": "BLOCKED",
    "scenario_env_mismatch": "BLOCKED",
    "scenario_api_conflict": "BLOCKED",
    "scenario_coverage_gap": "BLOCKED",
}


def load_scenario(name: str) -> dict:
    path = SAMPLES_DIR / f"{name}.yaml"
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def list_scenarios() -> list[str]:
    return sorted(p.stem for p in SAMPLES_DIR.glob("*.yaml"))


@pytest.fixture
def ok_delivery() -> dict:
    return load_scenario("scenario_ok")


@pytest.fixture
def missing_delivery() -> dict:
    return load_scenario("scenario_missing")
