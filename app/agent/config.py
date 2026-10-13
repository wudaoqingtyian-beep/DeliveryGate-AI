"""真实模型客户端配置读取（环境变量）。

阶段 3 真实 OpenAI 兼容客户端专用。直接读取进程环境变量，不引入 python-dotenv，
不新建 .env 保存真实密钥。API Key 只从环境读取，绝不写入代码、日志或异常信息。

环境变量：
- DELIVERYGATE_LLM_BASE_URL        例如 https://api.openai.com/v1
- DELIVERYGATE_LLM_API_KEY         Bearer Token
- DELIVERYGATE_LLM_MODEL           模型名称
- DELIVERYGATE_LLM_TIMEOUT_SECONDS 单次请求超时（秒，默认 30）
"""

from __future__ import annotations

import os

# 单次请求超时默认值：阶段 0/2 已确定的初始建议值 30 秒（非压测验证的最佳值）。
DEFAULT_TIMEOUT_SECONDS = 30.0


def get_base_url() -> str:
    return (os.environ.get("DELIVERYGATE_LLM_BASE_URL") or "").strip()


def get_api_key() -> str:
    return (os.environ.get("DELIVERYGATE_LLM_API_KEY") or "").strip()


def get_model() -> str:
    return (os.environ.get("DELIVERYGATE_LLM_MODEL") or "").strip()


def get_timeout() -> float:
    raw = (os.environ.get("DELIVERYGATE_LLM_TIMEOUT_SECONDS") or "").strip()
    if not raw:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_TIMEOUT_SECONDS


def build_chat_completions_url(base_url: str) -> str:
    """统一拼接 /chat/completions，处理末尾斜杠并避免重复路径。"""
    base = (base_url or "").strip().rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def is_configured() -> bool:
    """BASE_URL、API_KEY、MODEL 三者齐全才视为已配置真实客户端。"""
    return bool(get_base_url() and get_api_key() and get_model())


def missing_config_fields() -> list[str]:
    """返回缺失的配置项名称（用于给出明确、可理解的配置错误）。"""
    missing = []
    if not get_base_url():
        missing.append("DELIVERYGATE_LLM_BASE_URL")
    if not get_api_key():
        missing.append("DELIVERYGATE_LLM_API_KEY")
    if not get_model():
        missing.append("DELIVERYGATE_LLM_MODEL")
    return missing
