"""核验任务 API 路由：创建执行、查询详情、读取报告。

POST /api/runs                同步执行一次核验并落库，返回报告（200）
GET  /api/runs/{run_id}       任务详情（只读，不重新执行）
GET  /api/runs/{run_id}/report 最终核验报告（只读，不重新执行）

最终交付状态（READY/NEEDS_REVIEW/BLOCKED）与执行状态（run_status）相互独立：
BLOCKED 不是 HTTP 错误；模型执行失败（model_error）保留已有证据并落库，
绝不静默切换模拟模式，也绝不把未完成的必需检查当成通过。
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.agent.runner import AgentRunner
from app.api.deps import build_client, get_repository
from app.api.errors import (
    CODE_REPORT_NOT_READY,
    CODE_RUN_NOT_FOUND,
    CODE_SCENARIO_LOAD_ERROR,
    CODE_SCENARIO_NOT_FOUND,
    api_error,
)
from app.schemas.api import CreateRunRequest, ReportResponse, RunDetailResponse
from app.services import scenarios as scenario_service

router = APIRouter(prefix="/api", tags=["runs"])

# 运行未完成（第一版同步执行通常不会出现，保留契约兼容 409 语义）
_UNFINISHED_STATUSES = {"queued", "running"}


# ---------------------------------------------------------------- 映射辅助
def _map_events(full: dict) -> list[dict]:
    """工具事件行 -> API 结构：对外用任务内编号（TE-xxxx），不暴露数据库主键。"""
    return [
        {
            "tool_event_id": e["task_event_id"],
            "tool": e["tool"],
            "status": e["status"],
            "arguments": e.get("params") or {},
            "error_code": e.get("error_code"),
        }
        for e in full.get("tool_events", [])
    ]


def _map_findings(full: dict) -> list[dict]:
    """findings 行 -> API 结构：id=F-xxxx、tool_event_id=TE-xxxx，保留追溯链。"""
    tool_by_pk = {e["id"]: e["tool"] for e in full.get("tool_events", [])}
    result = []
    for f in full.get("findings", []):
        evidence = f.get("evidence") or {}
        result.append(
            {
                "id": f.get("task_finding_id"),
                "tool": tool_by_pk.get(f.get("tool_event_id")),
                "tool_event_id": f.get("task_event_id"),
                "severity": f["severity"],
                "certainty": f["certainty"],
                "code": f["code"],
                "message": f["message"],
                "evidence": {
                    "source": evidence.get("source"),
                    "expected": evidence.get("expected"),
                    "actual": evidence.get("actual"),
                },
                "remediation": f.get("remediation"),
            }
        )
    return result


def _build_report(run_id: int, full: dict) -> dict:
    """get_full_run 行 -> 第 5 节报告结构（+ run_id）。"""
    return {
        "run_id": run_id,
        "model_mode": full["model_mode"],
        "model_name": full.get("model_name"),
        "delivery_id": full.get("delivery_id"),
        "final_status": full["final_status"],
        "run_status": full["status"],
        "summary": full.get("summary"),
        "counts": full.get("counts") or {},
        "required_checks_incomplete": full.get("required_checks_incomplete") or 0,
        "findings": _map_findings(full),
        "tool_events": _map_events(full),
        "model_explanation": full.get("model_explanation") or "",
        "steps_used": full.get("steps_used") or 0,
        "tool_calls_used": full.get("tool_calls_used") or 0,
        "duration_ms": full.get("duration_ms"),
        "created_at": full["created_at"],
        "finished_at": full.get("finished_at"),
    }


def _require_run(request: Request, run_id: int) -> dict:
    full = get_repository(request.app).get_full_run(run_id)
    if full is None:
        raise api_error(404, CODE_RUN_NOT_FOUND, f"任务不存在：{run_id}")
    return full


# ---------------------------------------------------------------- 路由
@router.post("/runs", response_model=ReportResponse)
def create_run(payload: CreateRunRequest, request: Request) -> dict:
    """创建并同步执行一次核验任务，落库后返回报告。

    required_tools 由服务端固定为全部四项必需检查，HTTP 请求不可削减。
    """
    # 1) 场景校验与加载（白名单，不拼接任意路径）
    try:
        delivery = scenario_service.load_scenario(payload.scenario_id)
    except scenario_service.ScenarioNotFoundError as exc:
        raise api_error(
            404, CODE_SCENARIO_NOT_FOUND, f"场景不存在：{payload.scenario_id}"
        ) from exc
    except scenario_service.ScenarioLoadError as exc:
        raise api_error(500, CODE_SCENARIO_LOAD_ERROR, str(exc)) from exc

    # 2) 按模式构造客户端（集中组装；real 配置缺失 503，不回退模拟）
    client = build_client(request.app, payload.mode)

    # 3) 执行（AgentRunner 捕获可恢复模型错误 -> run_status=model_error，保留证据）
    runner = AgentRunner(client=client)
    result = runner.run(delivery)

    # 4) 落库（runs + tool_events + findings，单事务）
    repo = get_repository(request.app)
    run_id = repo.save_run(result, delivery, model_name=getattr(client, "model", None))

    # 5) 返回报告（与 GET /report 一致）
    full = repo.get_full_run(run_id)
    return _build_report(run_id, full)


@router.get("/runs/{run_id}", response_model=RunDetailResponse)
def get_run(run_id: int, request: Request) -> dict:
    """查询任务详情（只读，不重新执行核验、不重新调用模型）。"""
    full = _require_run(request, run_id)
    return {
        "run_id": run_id,
        "status": full["status"],
        "final_status": full.get("final_status"),
        "model_mode": full["model_mode"],
        "model_name": full.get("model_name"),
        "delivery_id": full.get("delivery_id"),
        "summary": full.get("summary"),
        "counts": full.get("counts") or {},
        "required_checks_incomplete": full.get("required_checks_incomplete") or 0,
        "steps_used": full.get("steps_used") or 0,
        "tool_calls_used": full.get("tool_calls_used") or 0,
        "duration_ms": full.get("duration_ms"),
        "model_explanation": full.get("model_explanation") or "",
        "created_at": full["created_at"],
        "finished_at": full.get("finished_at"),
        "findings": _map_findings(full),
        "tool_events": _map_events(full),
    }


@router.get("/runs/{run_id}/report", response_model=ReportResponse)
def get_report(run_id: int, request: Request) -> dict:
    """读取最终核验报告（只读，不重新执行核验）。"""
    full = _require_run(request, run_id)
    if full.get("status") in _UNFINISHED_STATUSES or not full.get("finished_at"):
        raise api_error(409, CODE_REPORT_NOT_READY, "任务尚未完成，暂无最终报告")
    return _build_report(run_id, full)

