"""模型客户端接口、模拟客户端与真实 OpenAI 兼容客户端。

设计目标（对齐 docs/AGENT_DESIGN.md 第 2、3、6 节）：
- 用稳定、可测试的内部数据结构表达模型响应，区分"工具调用"与"最终文本"。
- 客户端可注入、可替换：执行器不依赖具体供应商。
- 模拟客户端（SimulationClient）不发起任何网络请求、不需要 API Key，用于本地
  运行与测试；它依据已执行的工具结果动态决定下一步，绝不硬编码最终状态，
  也绝不绕过真实核验工具。它不是真实 LLM 推理，不代表模型质量。
- 真实客户端（OpenAICompatibleClient）通过 HTTPX 调用兼容 OpenAI Chat
  Completions 的接口，mode="real"；配置缺失或请求失败时抛 ModelClientError，
  绝不静默退回模拟模式。

模型响应结构（ModelResponse）：
- kind == "tool_calls"：携带 tool_calls，每个为 ToolCall{id, name, arguments}。
- kind == "final"：携带 text（模型最终解释，仅辅助阅读，不作为最终状态依据）。

decide() 约定：
    decide(messages, tools, timeout=None) -> ModelResponse
- messages：对话历史（list[dict]，role/content，含工具结果回传）。
- tools：暴露给模型的工具 schema 列表（由 prompts.build_tool_schemas 提供）。
- timeout：单次请求超时（秒）。模拟客户端忽略真实等待，仅为接口一致性保留。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional


# ---- 响应类型 ----
RESPONSE_TOOL_CALLS = "tool_calls"
RESPONSE_FINAL = "final"


@dataclass
class ToolCall:
    """一次工具调用请求。arguments 必须是 dict（模型提供的结构化参数）。"""

    id: str
    name: str
    arguments: dict = field(default_factory=dict)


@dataclass
class ModelResponse:
    """模型响应：要么是工具调用列表，要么是最终文本。

    kind 取值 RESPONSE_TOOL_CALLS / RESPONSE_FINAL。
    - tool_calls 模式：tool_calls 非空，text 为空。
    - final 模式：text 非空，tool_calls 为空。
    """

    kind: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    text: str = ""

    @classmethod
    def from_tool_calls(cls, tool_calls: list[ToolCall]) -> "ModelResponse":
        return cls(kind=RESPONSE_TOOL_CALLS, tool_calls=list(tool_calls), text="")

    @classmethod
    def from_final_text(cls, text: str) -> "ModelResponse":
        return cls(kind=RESPONSE_FINAL, tool_calls=[], text=text or "")


class ModelClientError(Exception):
    """模型客户端异常（请求失败、超时、非法响应等）。

    执行器捕获后记为 model_error，依据已有证据收尾，绝不伪造成功。
    """


class BaseModelClient:
    """模型客户端接口。阶段 3 的真实 OpenAI 兼容客户端应实现同一 decide() 约定。"""

    mode = "real"

    def decide(
        self,
        messages: list[dict],
        tools: list[dict],
        timeout: Optional[float] = None,
    ) -> ModelResponse:
        raise NotImplementedError


class SimulationClient(BaseModelClient):
    """模拟模型客户端：不发起网络请求、不需要 API Key。

    重要说明：这是**模拟**，不是真实 LLM 推理。它依据已执行的工具结果，
    用简单、确定、可解释的规则决定下一个要调用的工具或结束，用来在无 API Key
    时跑通流程与测试。它绝不：
      - 硬编码任何场景的最终状态；
      - 绕过真实核验工具（工具始终经 app.tools.registry 真实执行）；
      - 冒充真实模型的推理质量。

    决策策略（简单、可解释）：
      1. 从未执行过工具时，先调用 check_required_fields 作为基线（若它在必需清单中）。
      2. 已执行过工具时，从"必需但尚未执行"的工具中挑下一个：
         - 若刚执行完 check_required_fields 且其发现与环境/变量相关的问题，
           优先调用 compare_environment_config（体现"依据前次结果决策"）。
         - 否则按必需清单的既定顺序取下一个。
      3. 必需工具全部执行完毕后，输出最终解释文本结束。
    required_tools 从初始 user 消息（JSON）中读取，缺省为全部四个工具。
    """

    mode = "simulation"

    def __init__(self) -> None:
        self._call_counter = 0

    # -- 内部：解析对话历史 --
    @staticmethod
    def _required_tools(messages: list[dict]) -> list[str]:
        for msg in messages:
            if msg.get("role") == "user":
                content = msg.get("content", "")
                try:
                    data = json.loads(content)
                except (TypeError, ValueError):
                    continue
                if isinstance(data, dict) and isinstance(
                    data.get("required_tools"), list
                ):
                    return [t for t in data["required_tools"] if isinstance(t, str)]
        from app.schemas import contracts

        return list(contracts.ALL_TOOLS)

    @staticmethod
    def _executed(messages: list[dict]) -> list[str]:
        # 从 assistant 工具调用消息读取已执行的工具名（符合 OpenAI 协议格式：
        # assistant.tool_calls[].function.name）。与真实客户端的消息历史一致。
        executed: list[str] = []
        for m in messages:
            if m.get("role") != "assistant":
                continue
            for tc in m.get("tool_calls", []) or []:
                fn = tc.get("function") if isinstance(tc, dict) else None
                name = fn.get("name") if isinstance(fn, dict) else None
                if isinstance(name, str):
                    executed.append(name)
        return executed

    @staticmethod
    def _last_tool_result(messages: list[dict]) -> Optional[dict]:
        for m in reversed(messages):
            if m.get("role") == "tool":
                try:
                    return json.loads(m.get("content", ""))
                except (TypeError, ValueError):
                    return None
        return None

    def _next_call_id(self) -> str:
        self._call_counter += 1
        return f"sim-call-{self._call_counter}"

    def decide(
        self,
        messages: list[dict],
        tools: list[dict],
        timeout: Optional[float] = None,
    ) -> ModelResponse:
        # timeout 对模拟客户端无真实等待意义，仅为接口一致性保留。
        required = self._required_tools(messages)
        executed = self._executed(messages)
        remaining = [t for t in required if t not in executed]

        if not remaining:
            return ModelResponse.from_final_text(
                "[Simulated] 全部必需核验已执行，以下为基于工具结果的汇总解释。"
            )

        next_tool = self._choose_next(required, executed, remaining, messages)
        call = ToolCall(
            id=self._next_call_id(),
            name=next_tool,
            arguments={"delivery": self._delivery_from_messages(messages)},
        )
        return ModelResponse.from_tool_calls([call])

    def _choose_next(
        self,
        required: list[str],
        executed: list[str],
        remaining: list[str],
        messages: list[dict],
    ) -> str:
        # 动态决策：刚做完必填检查且发现环境/变量相关问题时，优先补环境核验。
        last = self._last_tool_result(messages)
        if (
            executed
            and executed[-1] == "check_required_fields"
            and "compare_environment_config" in remaining
            and self._looks_env_related(last)
        ):
            return "compare_environment_config"
        # 否则按必需清单既定顺序取下一个未执行项
        return remaining[0]

    @staticmethod
    def _looks_env_related(result: Optional[dict]) -> bool:
        if not isinstance(result, dict):
            return False
        for f in result.get("findings", []) or []:
            code = str(f.get("code", ""))
            source = str(f.get("evidence", {}).get("source", ""))
            if code.startswith("ENV_") or "env_var" in source or "environment" in source:
                return True
        return False

    @staticmethod
    def _delivery_from_messages(messages: list[dict]) -> Any:
        # 交付资料由执行器在初始 user 消息中以 JSON 提供，模拟客户端原样取用。
        for msg in messages:
            if msg.get("role") == "user":
                try:
                    data = json.loads(msg.get("content", ""))
                except (TypeError, ValueError):
                    continue
                if isinstance(data, dict) and "delivery" in data:
                    return data["delivery"]
        return None


class OpenAICompatibleClient(BaseModelClient):
    """真实 OpenAI 兼容 Chat Completions 客户端（mode="real"）。

    - 通过 HTTPX 调用 {BASE_URL}/chat/completions，Bearer Token 认证。
    - 只负责与模型通信和响应转换，不执行核验工具。
    - 配置（BASE_URL/API_KEY/MODEL）缺失或无效时，构造即抛 ModelClientError，
      绝不静默创建模拟客户端。
    - 真实请求失败（HTTP 错误、超时、连接失败、限流、非法响应）抛
      ModelClientError；绝不把模拟输出当作真实结果。

    重试策略（保守）：
    - 连接失败、传输异常、请求超时：最多自动重试一次。
    - HTTP 500/502/503/504：最多自动重试一次。
    - HTTP 400/401/403/404：明确错误，不重试。
    - HTTP 429 限流：不自动重试，返回明确限流错误。
    - 响应 JSON 非法/缺字段/工具参数无效：不重试，直接抛错。

    API Key 绝不写入异常信息、日志或返回值。
    """

    mode = "real"

    # 需要重试的服务端瞬时错误
    _RETRYABLE_STATUS = {500, 502, 503, 504}
    # 明确不重试的客户端/认证/配置错误
    _NON_RETRYABLE_STATUS = {400, 401, 403, 404, 422}

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: int = 1,
        http_client: Optional[Any] = None,
    ) -> None:
        # 允许显式传参（测试/编程调用）；缺省从环境变量读取。
        from app.agent import config

        self.base_url = (base_url if base_url is not None else config.get_base_url()).strip()
        self.api_key = api_key if api_key is not None else config.get_api_key()
        self.model = (model if model is not None else config.get_model()).strip()
        self.timeout = timeout if timeout is not None else config.get_timeout()
        self.max_retries = max(0, int(max_retries))
        self._http_client = http_client  # 测试可注入 MockTransport 的 httpx.Client

        missing = []
        if not self.base_url:
            missing.append("DELIVERYGATE_LLM_BASE_URL")
        if not self.api_key:
            missing.append("DELIVERYGATE_LLM_API_KEY")
        if not self.model:
            missing.append("DELIVERYGATE_LLM_MODEL")
        if missing:
            raise ModelClientError(
                "真实模型客户端配置缺失：" + "、".join(missing)
            )
        if self.timeout <= 0:
            raise ModelClientError("请求超时配置必须为正数")

        self._url = config.build_chat_completions_url(self.base_url)

    # ---- 请求构造 ----
    def _build_payload(self, messages: list[dict], tools: list[dict]) -> dict:
        return {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
        }

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }


    # ---- 主入口 ----
    def decide(
        self,
        messages: list[dict],
        tools: list[dict],
        timeout: Optional[float] = None,
    ) -> ModelResponse:
        import httpx

        req_timeout = timeout if timeout is not None else self.timeout
        payload = self._build_payload(messages, tools)

        client = self._http_client or httpx.Client(timeout=req_timeout)
        owns_client = self._http_client is None
        last_error: Optional[str] = None
        try:
            for attempt in range(self.max_retries + 1):
                try:
                    resp = client.post(
                        self._url,
                        headers=self._headers(),
                        json=payload,
                        timeout=req_timeout,
                    )
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    # 连接失败 / 传输异常 / 超时：可重试一次
                    last_error = self._safe_msg("网络请求失败", exc)
                    if attempt < self.max_retries:
                        continue
                    raise ModelClientError(last_error)

                # HTTP 状态码处理
                if resp.status_code in self._RETRYABLE_STATUS:
                    last_error = f"模型服务暂时不可用 HTTP {resp.status_code}"
                    if attempt < self.max_retries:
                        continue
                    raise ModelClientError(last_error)
                if resp.status_code == 429:
                    raise ModelClientError(
                        "模型服务限流（HTTP 429），本版本不自动重试，请稍后重试"
                    )
                if resp.status_code in self._NON_RETRYABLE_STATUS or resp.status_code >= 400:
                    raise ModelClientError(
                        f"模型请求失败 HTTP {resp.status_code}："
                        f"{self._safe_body(resp)}"
                    )

                # 成功：解析响应（解析类错误不重试）
                return self._parse_response(resp)

            # 理论不可达；防御性处理
            raise ModelClientError(last_error or "模型请求失败")
        finally:
            if owns_client:
                client.close()

    # ---- 响应解析 ----
    def _parse_response(self, resp) -> ModelResponse:
        import httpx  # noqa: F401 - 保留以便将来细分 httpx 异常

        try:
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise ModelClientError(f"模型响应不是合法 JSON：{exc}")

        if not isinstance(data, dict):
            raise ModelClientError("模型响应结构异常：顶层不是对象")

        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ModelClientError("模型响应缺少 choices")

        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise ModelClientError("模型响应缺少有效 message")

        raw_calls = message.get("tool_calls")
        content = message.get("content")

        if raw_calls:
            tool_calls = self._parse_tool_calls(raw_calls)
            if tool_calls:
                return ModelResponse.from_tool_calls(tool_calls)
            # 有 tool_calls 字段但全部无效 -> 已在 _parse_tool_calls 抛错

        # 无工具调用：取最终文本
        if isinstance(content, str) and content.strip():
            return ModelResponse.from_final_text(content)

        raise ModelClientError("模型响应既无有效工具调用也无最终文本")

    def _parse_tool_calls(self, raw_calls) -> list[ToolCall]:
        if not isinstance(raw_calls, list):
            raise ModelClientError("tool_calls 结构异常：不是列表")
        calls: list[ToolCall] = []
        for idx, tc in enumerate(raw_calls):
            if not isinstance(tc, dict):
                raise ModelClientError(f"tool_calls[{idx}] 不是对象")
            call_id = tc.get("id")
            fn = tc.get("function")
            if not isinstance(fn, dict):
                raise ModelClientError(f"tool_calls[{idx}] 缺少 function")
            name = fn.get("name")
            if not isinstance(call_id, str) or not isinstance(name, str):
                raise ModelClientError(f"tool_calls[{idx}] 缺少 id 或 name")
            arguments = self._parse_arguments(fn.get("arguments"), name)
            calls.append(ToolCall(id=call_id, name=name, arguments=arguments))
        return calls

    @staticmethod
    def _parse_arguments(raw, name: str) -> dict:
        # 参数是 JSON 字符串；解析失败不得执行任何工具。
        if raw is None or raw == "":
            return {}
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                obj = json.loads(raw)
            except (TypeError, ValueError) as exc:
                raise ModelClientError(
                    f"工具 {name} 的参数不是合法 JSON：{exc}"
                )
            if not isinstance(obj, dict):
                raise ModelClientError(f"工具 {name} 的参数必须是 JSON 对象")
            return obj
        raise ModelClientError(f"工具 {name} 的参数类型异常")

    # ---- 安全：异常/响应体不得包含 API Key ----
    @staticmethod
    def _safe_msg(prefix: str, exc: Exception) -> str:
        return f"{prefix}：{type(exc).__name__}"

    @staticmethod
    def _safe_body(resp) -> str:
        # 仅取响应体前若干字符，且不包含请求头（Authorization）。
        try:
            text = resp.text
        except Exception:  # noqa: BLE001
            return "<无响应体>"
        text = (text or "").strip()
        if len(text) > 200:
            text = text[:200] + "..."
        return text or "<空响应体>"

