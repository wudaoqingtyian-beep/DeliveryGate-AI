"""FastAPI 应用入口（对齐 docs/DATA_CONTRACTS.md 第 7 节）。

启动：python -m uvicorn app.main:app --reload

设计约束：
- 模块导入时只创建轻量应用对象，不创建真实模型客户端、不读取 API Key、
  不初始化数据库（Repository 惰性创建，init_db 不覆盖历史数据）。
- 同步路由 + 同步处理（AgentRunner 与 Repository 均为同步调用）。
- 统一错误响应：{"error": {"code", "message"}}；不向客户端暴露堆栈、
  API Key 或环境变量值。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.agent.llm_client import BaseModelClient
from app.api.errors import CODE_INTERNAL_ERROR, CODE_VALIDATION_ERROR
from app.api.runs import router as runs_router
from app.api.scenarios import router as scenarios_router
from app.schemas.api import HealthResponse

logger = logging.getLogger("deliverygate")

SERVICE_NAME = "deliverygate-ai"

# 前端静态资源目录：基于本文件位置解析，不依赖启动时的工作目录。
STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(
    db_path: Optional[str] = None,
    client_factory: Optional[Callable[[str], BaseModelClient]] = None,
) -> FastAPI:
    """创建 FastAPI 应用。

    - db_path：数据库文件路径（缺省用环境变量 DELIVERYGATE_DB_PATH 或默认值）；
      测试传入临时库路径。
    - client_factory：按 mode 构造模型客户端的工厂（测试注入替身）；
      缺省 None 时按生产规则构造（simulation/real）。
    """
    app = FastAPI(title=SERVICE_NAME, version="0.4.2")
    app.state.db_path = db_path
    app.state.client_factory = client_factory
    app.state.repo = None  # 惰性创建

    # ---- 前端工作台（阶段 4.3）：静态挂载与首页 ----
    # 只读取静态文件：不建模型客户端、不读 API Key、不建库；与 /api 路由无冲突。
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        """前端工作台首页（静态页面，不执行核验）。"""
        return FileResponse(STATIC_DIR / "index.html")

    # ---- 健康检查：不触碰模型与核验工具 ----
    @app.get("/api/health", response_model=HealthResponse, tags=["health"])
    def health() -> dict:
        return {"status": "ok", "service": SERVICE_NAME}

    app.include_router(scenarios_router)
    app.include_router(runs_router)

    # ---- 统一错误响应 ----
    @app.exception_handler(HTTPException)
    async def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict) and "code" in detail:
            body = {"error": {"code": detail["code"], "message": str(detail.get("message", ""))}}
        else:
            body = {"error": {"code": "HTTP_ERROR", "message": str(detail)}}
        return JSONResponse(status_code=exc.status_code, content=body, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # 只回传字段位置与原因，不回显完整输入内容
        problems = [
            {"loc": [str(x) for x in err.get("loc", [])], "msg": err.get("msg", "")}
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": CODE_VALIDATION_ERROR,
                    "message": "请求参数校验失败",
                    "detail": problems,
                }
            },
        )

    @app.exception_handler(Exception)
    async def _internal_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        # 完整堆栈只记录在服务端日志；响应为通用错误，不泄漏内部信息
        logger.exception("未处理的服务端异常")
        return JSONResponse(
            status_code=500,
            content={
                "error": {"code": CODE_INTERNAL_ERROR, "message": "服务器内部错误"}
            },
        )

    return app


# Uvicorn 入口：python -m uvicorn app.main:app --reload
app = create_app()
