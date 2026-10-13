"""HTTP API 错误工具。

统一错误响应结构（DATA_CONTRACTS.md 7.7 节）：{"error": {"code", "message"}}。
路由中用 api_error() 构造带 code/message 的 HTTPException，由 app.main 的
异常处理器渲染为统一结构；不向客户端暴露堆栈、API Key 或环境变量值。
"""

from __future__ import annotations

from fastapi import HTTPException


def api_error(status_code: int, code: str, message: str) -> HTTPException:
    """构造统一结构的 HTTP 异常。"""
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


# ---- 错误码（与 DATA_CONTRACTS.md 7.7 节一致） ----
CODE_VALIDATION_ERROR = "VALIDATION_ERROR"
CODE_SCENARIO_NOT_FOUND = "SCENARIO_NOT_FOUND"
CODE_SCENARIO_LOAD_ERROR = "SCENARIO_LOAD_ERROR"
CODE_RUN_NOT_FOUND = "RUN_NOT_FOUND"
CODE_REPORT_NOT_READY = "REPORT_NOT_READY"
CODE_REAL_MODEL_CONFIG_MISSING = "REAL_MODEL_CONFIG_MISSING"
CODE_INTERNAL_ERROR = "INTERNAL_ERROR"
