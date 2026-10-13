# DeliveryGate AI —— 需求文档（REQUIREMENTS）

版本：v0.1（阶段 0 冻结稿）
状态：已冻结为第一版设计与开发依据；本阶段未实现任何业务功能。
配套文档：本文件与 `DATA_CONTRACTS.md`、`AGENT_DESIGN.md` 共同构成本项目第一版完整契约。三份文档的术语、工具名、状态值、接口路径、表名保持一致。

---

## 1. 项目定位与边界

DeliveryGate AI 是企业软件交付的**前置核验 Agent**。在软件部署或联调之前，它针对一份结构化交付资料，完成四类核验：

1. 交付必填信息是否完整。
2. 目标环境要求与实际部署配置是否存在矛盾。
3. 接口契约自身是否自洽（路径、版本、认证要求是否明确），以及验收检查项对接口的引用是否存在（端点是否存在）。第一版不做验收侧与契约侧的认证交叉比对。
4. 关键验收要求是否具备对应的验证步骤。

它只提供交付前的核验结论与风险提示，**不自动部署软件，不直接修改生产环境**。所有核验结论供人决策，最终是否交付由人决定。

## 2. 第一版功能范围

技术栈（已冻结）：Python 3.12、FastAPI、Pydantic v2、HTTPX、SQLite（标准库 `sqlite3`）、YAML/JSON、原生 HTML/CSS/JavaScript、pytest、自建轻量级 Tool Calling Loop。

- **双模式**：支持 `simulation`（模拟）与 `real`（真实模型）两种执行模式。
  - 真实模式：通过兼容 OpenAI Chat Completions 工具调用协议的接口接入模型。
  - 模拟模式：必须明确标识为模拟，**不得冒充真实模型执行结果**。模拟模式用于在无 API Key 时跑通流程与测试。
- **四个核心核验工具**（名称固定，单一职责）：
  | 工具名 | 职责 |
  | --- | --- |
  | `check_required_fields` | 检查交付必填信息是否完整 |
  | `compare_environment_config` | 比较目标环境要求与实际部署配置 |
  | `compare_api_acceptance` | 比较接口契约与验收检查项的一致性 |
  | `inspect_acceptance_coverage` | 检查关键验收要求是否有对应验证步骤 |
  各工具的输入、输出、失败处理、证据记录与副作用规则见 `DATA_CONTRACTS.md` 第 3 节。
- **工具只读**：工具仅访问预先提供的交付资料与受控检查接口，全部由确定性 Python 逻辑完成具体校验。**禁止**开放任意 SQL、Shell 命令、任意文件系统访问和生产环境修改能力。工具不允许产生副作用。
- **Agent 职责**：LLM 依据任务与已有证据选择工具、决定下一步操作、解释检查结果。执行循环、动态决策、停止条件与异常处理见 `AGENT_DESIGN.md`。

## 3. 非目标（第一版明确不做）

- 不自动部署软件、不修改生产环境、不执行任何真实系统命令。
- 不做登录、权限、多租户、审计等系统级能力。
- 不引入 LangGraph、CrewAI 等 Agent 编排框架；不做多 Agent、RAG、向量数据库。
- 不解析 PDF/Word 等复杂文档；交付资料只支持结构化 YAML/JSON。
- 不实现异步任务队列、流式输出、后台任务框架（见第 6 节 API 边界）。
- 不新增无实际需求的第三方依赖。

## 4. 术语与命名约定

- 字段、参数、表列统一 `snake_case`。
- 工具名：`check_required_fields`、`compare_environment_config`、`compare_api_acceptance`、`inspect_acceptance_coverage`。
- 执行模式：`simulation` / `real`。
- 运行态（`runs.status`）：`queued` / `running` / `succeeded` / `failed`。
- 最终就绪状态（`runs.final_status` 与报告 `final_status`）：`READY` / `NEEDS_REVIEW` / `BLOCKED`。
- 问题严重度（finding `severity`）：`blocker` / `warning` / `pass`。
- 确定性区分（finding `certainty`）：`confirmed`（确定发现的问题）/ `uncertain`（尚未确认、需人工确认的风险）。
- 数据库表：`runs`、`tool_events`、`findings`。
- API 前缀统一 `/api/`。

## 5. 最终状态判定规则（权威定义见 DATA_CONTRACTS.md 第 4 节）

第一版最终状态只有三种：

- `READY`：所有必需核验均已成功通过，无 `blocker`，也无未解决的关键不确定性（无 `uncertain` 的 `warning`）。
- `NEEDS_REVIEW`：存在警告、证据不足或需人工确认的问题，当前不能直接认定为就绪。
- `BLOCKED`：存在关键资料缺失、明确配置冲突或其他阻止后续交付的条件。

判定原则（必须在实现中严格遵守）：

1. 最终状态由**确定性业务规则代码**计算（`compute_final_status`），**绝不直接采用 LLM 自行生成的 `READY`/`NEEDS_REVIEW`/`BLOCKED`**。
2. **关键检查未成功完成，不能当作通过**。若必需工具执行失败或模型失败导致必需核验缺失，结果不能是 `READY`。
3. 合并优先级：`BLOCKED` > `NEEDS_REVIEW` > `READY`。
4. 区分"确定发现的问题"（`certainty=confirmed` 的 blocker/warning）与"尚未确认的风险"（`certainty=uncertain`）。
5. 三种状态值**绝不能仅凭模型自然语言回答变更**，只能由确定性规则在汇总全部 finding 后计算。

完整判定表与优先级规则见 `DATA_CONTRACTS.md` 第 4 节。

## 6. API 契约（只定契约，本阶段不实现）

规划以下接口，详见 `DATA_CONTRACTS.md` 第 7 节：

- `GET /api/health`
- `GET /api/scenarios`
- `POST /api/runs`
- `GET /api/runs/{run_id}`
- `GET /api/runs/{run_id}/report`

第一版 `POST /api/runs` 采用**同步执行并直接返回结果**的方式，不实现后台队列、异步任务与流式输出。查询执行详情与读取报告是两次独立的只读请求。

## 7. 验收与测试标准（计划测试，本阶段均未执行）

后续阶段必须覆盖以下测试。**本阶段只撰写文档，未执行其中任何测试，不得声称已通过。**

- 必填信息检查（`check_required_fields`）：完整、缺失两种情形。
- 环境配置比较（`compare_environment_config`）：一致、冲突两种情形。
- API 契约与验收要求比较（`compare_api_acceptance`）：一致、端点引用不存在/路径与版本前缀不一致/契约认证不明确三种情形。
- 验收覆盖检查（`inspect_acceptance_coverage`）：关键要求有/无验证步骤两种情形。
- 三种最终状态（`READY`/`NEEDS_REVIEW`/`BLOCKED`）的判定，各至少一个用例。
- Agent 工具调用及连续调用（依前次结果决定下一步）。
- Agent 达到最大步数后安全停止。
- 无效参数、未知工具、工具执行失败的处理。
- 模型请求超时或调用失败的处理。
- 关键 `blocker` 不能被模型最终回答覆盖（最终状态仍为确定性计算结果）。
- 模拟模式与真实模型模式的明确区分（模拟结果必须标识，不得冒充真实）。
- 报告与证据来源的关联可追溯。

测试命名与数据样例应复用 `DATA_CONTRACTS.md` 第 2 节的固定场景样例，保证测试与文档口径一致。

## 8. 核心回归流程（固定为后续阶段基准）

```
选择交付资料
  → 创建核验任务
  → Agent 依据任务与证据选择工具
  → 执行确定性的资料核验
  → 依据工具结果决定是否继续调用工具
  → 汇总问题与证据
  → 由确定性规则计算最终就绪状态
  → 生成并保存核验报告
  → 查询执行详情与历史报告
```

后续每个阶段的实现与回归测试都应以该流程为基准。

## 9. 待决策事项（阶段 1 前需确认）

1. 交付资料样例的具体业务字段（`DATA_CONTRACTS.md` 第 2 节给出的是最小可用集合，是否扩展待业务确认）。
2. 最大步数、最大工具调用次数、单次请求超时、总体超时的最终取值（`AGENT_DESIGN.md` 给出的是初始建议值，未经压测验证）。
3. `findings` 是否需要单独记录模型层产生的合并说明（当前设计仅记录工具产生的 finding）。
4. 报告是否需要版本号或历史对比（当前第一版不含）。

> 说明：以上均为"计划与建议"，本阶段未做实现与验证。
