# DeliveryGate AI

企业软件交付前置核验 Agent。在部署或联调**之前**，检查交付资料是否完整、环境配置是否自相矛盾、接口契约与验收要求是否一致，以及关键验收要求是否配齐验证步骤。它只做交付前的核验与风险提示——不自动部署软件，不直接修改生产环境。

> 当前进度：**阶段 4.3 已完成**——四个确定性核验工具、工具注册入口、最终状态计算、单 Agent 执行器（含模拟模式与真实 OpenAI 兼容客户端）、SQLite 持久化层、FastAPI 服务层（五个接口）与中文前端工作台均已实现并通过自动化测试。设计以三份文档为准，见下方"设计文档"。

## 设计文档

- [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) —— 需求、功能范围、非目标、业务规则、验收与测试标准。
- [docs/DATA_CONTRACTS.md](docs/DATA_CONTRACTS.md) —— 交付资料 YAML/JSON 结构、场景样例、四个核验工具的输入输出、核验报告结构、数据库与 API 契约。
- [docs/AGENT_DESIGN.md](docs/AGENT_DESIGN.md) —— Agent 执行循环、动态决策、工具调用机制、状态判定原则、停止条件与异常处理。

三份文档在术语、工具名、状态值、接口路径和表名上保持一致；`DATA_CONTRACTS.md` 是交付资料结构与最终状态判定表的权威定义。

## 技术栈

Python 3.12 + FastAPI + Pydantic v2 + HTTPX + SQLite（标准库 `sqlite3`，不上 ORM）+ YAML/JSON + 原生 HTML/CSS/JS + pytest + 自建轻量级 Tool Calling Loop。不引入 LangGraph、CrewAI 等 Agent 编排框架，不引入无实际需求的第三方依赖。工具校验全部由确定性 Python 逻辑完成，LLM 只负责选工具、决定下一步、解释结果。

## 核心流程（后续阶段的回归基准）

选择交付资料 → 创建核验任务 → Agent 依据任务与证据选择工具 → 执行确定性资料核验 → 依据工具结果决定是否继续调用 → 汇总问题与证据 → **由确定性规则计算最终就绪状态** → 生成并保存核验报告 → 查询执行详情与历史报告。

最终就绪状态（`READY` / `NEEDS_REVIEW` / `BLOCKED`）必须由确定性业务规则计算，不采用 LLM 自行生成的状态。

## 跑起来

```bash
pip install -r requirements.txt
python -m pytest    # 运行全部单元测试（不访问网络、不需 API Key）
```

代码直接位于项目根（`app/`、`tests/`），无 `backend/` 子目录。

## 三种运行方式

1. **模拟模式（`simulation`）**：默认。不调用外部模型、不需要 API Key，用确定性的模拟客户端跑通完整流程。用于本地开发与无凭证测试。
2. **HTTP Mock 测试**：单元测试用 `httpx.MockTransport` 模拟服务端，不产生任何外部请求或费用。
3. **真实模型模式（`real`）**：接入兼容 OpenAI Chat Completions 的接口，用真实模型做工具选择与调用。

三者严格区分：真实请求失败不会静默退回模拟模式；模拟结果标注 `simulation`，真实结果标注 `real`。

## 真实模型运行

通过环境变量配置（勿写入代码或提交）：

| 环境变量 | 说明 |
| --- | --- |
| `DELIVERYGATE_LLM_BASE_URL` | OpenAI 兼容 Base URL，如 `https://api.openai.com/v1`（**不含** `/chat/completions`，客户端自动拼接） |
| `DELIVERYGATE_LLM_API_KEY` | Bearer Token |
| `DELIVERYGATE_LLM_MODEL` | 模型名称 |
| `DELIVERYGATE_LLM_TIMEOUT_SECONDS` | 单次请求超时（秒，默认 30） |

Python 中显式选择真实客户端：

```python
from app.agent.llm_client import OpenAICompatibleClient
from app.agent.runner import AgentRunner
from tests.conftest import load_scenario

client = OpenAICompatibleClient()          # 从环境变量读取配置；配置缺失会抛错
result = AgentRunner(client).run(load_scenario("scenario_ok"))
print(result.final_status, result.model_mode)   # 最终状态由 compute_final_status 计算
```

参考 `.env.example` 了解字段（`.env` 已被 `.gitignore` 忽略，请勿提交真实密钥）。

### 真实 API 冒烟测试（可能产生费用）

```bash
set DELIVERYGATE_RUN_REAL_API_TEST=1
set DELIVERYGATE_LLM_BASE_URL=https://api.openai.com/v1
set DELIVERYGATE_LLM_API_KEY=<你的key>
set DELIVERYGATE_LLM_MODEL=<模型名>
python -m pytest tests/integration -v
```

未设置 `DELIVERYGATE_RUN_REAL_API_TEST=1` 或缺少配置时，冒烟测试**跳过**而非假装通过；普通 `python -m pytest` 只跑单元测试，不会发起真实请求。**真实模型调用可能产生费用。**

## SQLite 持久化（阶段 4.1）

标准库 `sqlite3`，无 ORM。三张表 `runs` / `tool_events` / `findings` 对齐 `DATA_CONTRACTS.md` 第 6 节；数据库路径由环境变量 `DELIVERYGATE_DB_PATH` 指定，缺省为 `data/deliverygate.db`（`data/*.db` 已被 `.gitignore` 忽略）。

```python
from app.agent import run_verification
from app.repositories import RunRepository
from tests.conftest import load_scenario

delivery = load_scenario("scenario_ok")
result = run_verification(delivery)

repo = RunRepository()                       # 初始化：建目录、建表、开外键
run_id = repo.save_run(result, delivery)     # 单事务写入三张表，失败整体回滚
print(repo.get_full_run(run_id))             # 任务 + 工具事件 + findings
```

要点：

- `save_run` 在一个事务中写入 `runs`、`tool_events`、`findings`；任一步失败整体回滚，不留部分数据。
- 数据库自增整数主键仅用于内部关联；任务内编号（`TE-0001` / `F-0001`）单独存为 `task_event_id` / `task_finding_id`，不假定跨任务唯一。
- `findings.tool_event_id` 外键指向 `tool_events` 主键，形成 **finding → 工具执行事件 → evidence.source** 可追溯链。
- `runs.status` 是执行态（`succeeded`/`model_error`/...），`runs.final_status` 是交付就绪态（`READY`/`NEEDS_REVIEW`/`BLOCKED`），两者独立保存；`model_explanation` 仅存档，绝不参与状态判定。

## HTTP API（阶段 4.2）

FastAPI + Pydantic v2，同步请求处理，契约见 `DATA_CONTRACTS.md` 第 7 节。启动：

```bash
python -m uvicorn app.main:app --reload
```

启动后浏览器打开 `http://127.0.0.1:8000/` 即可使用**中文核验工作台**（`app/static/`，纯原生 HTML/CSS/JS，无构建链、无外部依赖）：选择场景与运行模式发起核验、查看最终状态 / findings / 工具执行记录与证据来源、按 run_id 查询任务详情与报告、浏览器本地"最近访问任务"索引。前端只调用下列既有 API，不在页面中重复实现核验规则；静态资源路径为 `/static/*`，与 `/api` 无冲突。

数据库同样使用 `DELIVERYGATE_DB_PATH`（缺省 `data/deliverygate.db`），启动不依赖真实模型 API Key。

| 接口 | 说明 |
| --- | --- |
| `GET /api/health` | 健康检查：`{ "status": "ok", "service": "deliverygate-ai" }` |
| `GET /api/scenarios` | 列出五个内置固定场景（含 `id` / `name` / `expected_status`） |
| `POST /api/runs` | 创建并同步执行核验：`{ "scenario_id": "scenario_ok", "mode": "simulation" }`；`mode` 仅允许 `simulation`（缺省）或 `real`；成功返回含 `run_id` 的核验报告并落库 |
| `GET /api/runs/{run_id}` | 任务详情（执行状态、交付状态、工具事件、findings；只读，不重新执行） |
| `GET /api/runs/{run_id}/report` | 最终核验报告（findings 含 `F-xxxx` 编号、`tool_event_id` 与 `evidence.source` 追溯链） |

```bash
curl http://127.0.0.1:8000/api/scenarios
curl -X POST http://127.0.0.1:8000/api/runs -H "Content-Type: application/json" -d "{\"scenario_id\":\"scenario_missing\"}"
curl http://127.0.0.1:8000/api/runs/1
curl http://127.0.0.1:8000/api/runs/1/report
```

要点：

- `real` 模式依赖上文真实模型环境变量；配置缺失返回 `503` 并明确列出缺失变量名，**绝不**回退模拟模式。
- 错误统一为 `{ "error": { "code", "message" } }`（如未知场景 `404 SCENARIO_NOT_FOUND`、未知任务 `404 RUN_NOT_FOUND`），不泄漏堆栈与密钥。
- `final_status`（`READY`/`NEEDS_REVIEW`/`BLOCKED`）是交付状态而非 HTTP 状态码；模型请求失败的任务以 `run_status=model_error` 落库并保留已有证据。
- 请求不接受任意文件路径与工具列表；必需检查固定为全部四项。

## 不做的事

不做自动部署，不做生产环境修改；不做登录权限与多租户；不做 RAG、向量数据库、多 Agent 编排；不解析 PDF/Word 等复杂文档；工具不开放任意 SQL、Shell、任意文件系统访问。模拟模式（`simulation`）必须明确标识，绝不冒充真实模型（`real`）结果。
