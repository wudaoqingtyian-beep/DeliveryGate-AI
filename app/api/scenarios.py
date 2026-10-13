"""场景列表 API 路由。"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.errors import CODE_SCENARIO_LOAD_ERROR, api_error
from app.schemas.api import ScenarioListResponse
from app.services import scenarios as scenario_service

router = APIRouter(prefix="/api", tags=["scenarios"])


@router.get("/scenarios", response_model=ScenarioListResponse)
def list_scenarios(request: Request) -> ScenarioListResponse:
    """列出内置固定核验场景（白名单目录，不接受任何输入）。"""
    try:
        entries = scenario_service.list_scenarios()
    except scenario_service.ScenarioLoadError as exc:
        raise api_error(500, CODE_SCENARIO_LOAD_ERROR, str(exc)) from exc
    return ScenarioListResponse(scenarios=entries)
