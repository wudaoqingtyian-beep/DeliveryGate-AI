"""API 依赖组装：Repository 与模型客户端的集中构造点。

- Repository 按应用惰性创建一次（app.state.repo），数据库路径可由测试注入。
- 模型客户端按 mode 集中构造：simulation -> SimulationClient；real ->
  OpenAICompatibleClient（配置缺失抛 503，绝不回退模拟模式）。
- 测试可通过 app.state.client_factory 注入替身客户端，生产路径不感知。
"""

from __future__ import annotations

from typing import Callable, Optional

from fastapi import FastAPI

from app.agent.llm_client import (
    BaseModelClient,
    ModelClientError,
    OpenAICompatibleClient,
    SimulationClient,
)
from app.api.errors import CODE_REAL_MODEL_CONFIG_MISSING, api_error
from app.repositories import RunRepository


def get_repository(app: FastAPI) -> RunRepository:
    """惰性创建并复用应用级 Repository（init_db 不覆盖历史数据）。"""
    repo = getattr(app.state, "repo", None)
    if repo is None:
        repo = RunRepository(db_path=getattr(app.state, "db_path", None))
        app.state.repo = repo
    return repo


def build_client(app: FastAPI, mode: str) -> BaseModelClient:
    """按模式构造模型客户端（模式必须明确，失败绝不静默切换）。

    - 测试注入优先：app.state.client_factory 存在时使用它（替身不触网）。
    - simulation：SimulationClient，不调用真实模型。
    - real：OpenAICompatibleClient；配置缺失抛 503，错误信息只含缺失的
      环境变量名，不含任何密钥值。
    """
    factory: Optional[Callable[[str], BaseModelClient]] = getattr(
        app.state, "client_factory", None
    )
    if factory is not None:
        try:
            return factory(mode)
        except ModelClientError as exc:
            # 构造失败（如配置缺失）同样明确报错，不回退模拟模式
            raise api_error(503, CODE_REAL_MODEL_CONFIG_MISSING, str(exc)) from exc

    if mode == "simulation":
        return SimulationClient()
    if mode == "real":
        try:
            return OpenAICompatibleClient()
        except ModelClientError as exc:
            raise api_error(503, CODE_REAL_MODEL_CONFIG_MISSING, str(exc)) from exc
    # mode 已由 Pydantic Literal 限制，此分支不可达；防御性抛错。
    raise api_error(400, "INVALID_MODE", f"不支持的运行模式：{mode}")
