# DeliveryGate AI —— 数据契约（DATA_CONTRACTS）

版本：v0.1（阶段 0 冻结稿）
权威性：本文件是**交付资料结构、场景样例、四个核验工具的输入输出、核验报告结构与最终状态判定表**的权威定义。`REQUIREMENTS.md` 与 `AGENT_DESIGN.md` 引用本文件，不得与之矛盾。

---

## 1. 交付资料结构（YAML/JSON）

第一版交付资料为单份结构化文件，YAML 或 JSON 均可（同一 schema）。字段统一 `snake_case`。下表为最小可用字段集，足够支撑四个核验工具；不做企业级通用建模。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `delivery_id` | string | 是 | 交付唯一标识，如 `DLV-2024-001` |
| `project_name` | string | 是 | 项目名称 |
| `version` | string | 是 | 交付版本号 |
| `contact` | string | 是 | 交付负责人及联系方式 |
| `target_environment` | object | 是 | 目标环境要求，见下 |
| `actual_deployment` | object | 是 | 实际部署配置，见下 |
| `api_contracts` | list | 是 | 接口契约列表，见下 |
| `acceptance_criteria` | list | 是 | 验收要求列表，见下 |
| `verification_steps` | list | 是 | 验证步骤列表，见下 |

子结构：

- `target_environment`：`runtime`(string)、`python_version`(string)、`os`(string)、`required_env_vars`(list of `{name, description, required}`)。
- `actual_deployment`：`environment_name`(string)、`runtime`(string)、`python_version`(string)、`os`(string)、`env_vars_present`(list of string)。
- `api_contracts[]`：`endpoint_id`(string)、`path`(string)、`method`(string)、`auth`(string)、`api_version`(string)。
- `acceptance_criteria[]`：`criterion_id`(string)、`description`(string)、`related_api`(string，指向 `endpoint_id`)、`severity`(`critical`/`normal`)。
- `verification_steps[]`：`step_id`(string)、`criterion_id`(string，指向验收要求)、`action`(string)。

字段间关联关系：`acceptance_criteria[].related_api` → `api_contracts[].endpoint_id`；`verification_steps[].criterion_id` → `acceptance_criteria[].criterion_id`；`target_environment.required_env_vars` → `actual_deployment.env_vars_present`。

---

## 2. 固定场景样例

以下样例是后续开发与测试的**固定口径**。完整样例 `scenario_ok` 见 2.1，其余场景只列出**与 `scenario_ok` 不同的部分**，未列出的字段保持一致。

### 2.1 `scenario_ok` —— 完整且无冲突（期望 `READY`）

完整样例（其余场景以它为基准，只列差异）：

```yaml
delivery_id: "DLV-2024-001"
project_name: "订单中心"
version: "1.2.0"
contact: "张三 <zhangsan@example.com>"

target_environment:
  runtime: "python"
  python_version: "3.12"
  os: "Ubuntu 22.04"
  required_env_vars:
    - name: "DB_URL"
      description: "数据库连接串"
      required: true

actual_deployment:
  environment_name: "staging-01"
  runtime: "python"
  python_version: "3.12"
  os: "Ubuntu 22.04"
  env_vars_present:
    - "DB_URL"

api_contracts:
  - endpoint_id: "create_order"
    path: "/api/v1/orders"
    method: "POST"
    auth: "bearer_token"
    api_version: "v1"

acceptance_criteria:
  - criterion_id: "AC-1"
    description: "下单成功返回 201 且订单号非空"
    related_api: "create_order"
    severity: "critical"

verification_steps:
  - step_id: "VS-1"
    criterion_id: "AC-1"
    action: "调用 create_order 接口，校验状态码 201 且订单号非空"
```

### 2.2 `scenario_missing` —— 必需信息缺失（期望 `BLOCKED`）

与 `scenario_ok` 的差异：缺少 `contact`，且 `actual_deployment.env_vars_present` 未包含必需的 `DB_URL`。

```yaml
# contact 字段整个缺失
# actual_deployment 改为：
actual_deployment:
  environment_name: "staging-01"
  runtime: "python"
  python_version: "3.12"
  os: "Ubuntu 22.04"
  env_vars_present: []
```

`check_required_fields` 报 `contact` 缺失（blocker, confirmed）；`compare_environment_config` 报必需变量 `DB_URL` 缺失（blocker, confirmed）。

### 2.3 `scenario_env_mismatch` —— 环境配置与目标要求不一致（期望 `BLOCKED` 或 `NEEDS_REVIEW`）

与 `scenario_ok` 的差异：实际 `python_version` 与目标不一致。

```yaml
actual_deployment:
  environment_name: "staging-01"
  runtime: "python"
  python_version: "3.11"   # 目标要求 3.12
  os: "CentOS 7"           # 目标要求 Ubuntu 22.04
  env_vars_present:
    - "DB_URL"
```

`compare_environment_config` 对版本/OS 差异报 blocker（若工具规则定义为阻塞）或 warning（若定义为提示），并在文档统一：**运行时版本不匹配 = blocker；OS 不匹配 = warning**。因此本场景期望 `BLOCKED`。

### 2.4 `scenario_api_conflict` —— 接口契约与验收要求引用冲突（期望 `BLOCKED`）

与 `scenario_ok` 的差异：验收要求引用的 `related_api` 与接口契约不一致——验收要求指向 `endpoint_id: "submit_order"`，但 `api_contracts` 中并不存在该端点。

```yaml
api_contracts:
  - endpoint_id: "create_order"
    path: "/api/v1/orders"
    method: "POST"
    auth: "bearer_token"
    api_version: "v1"

acceptance_criteria:
  - criterion_id: "AC-1"
    description: "下单成功返回 201 且订单号非空"
    related_api: "submit_order"   # 契约中不存在该 endpoint_id
    severity: "critical"
```

`compare_api_acceptance` 报：验收要求引用的端点不存在（`API_ENDPOINT_NOT_FOUND`，blocker, confirmed）。期望 `BLOCKED`。

> 说明：第一版 `compare_api_acceptance` 只校验**接口契约自身**与**验收要求对端点的引用**（端点是否存在、path 与 api_version 前缀是否一致、auth 是否在允许集合内）。当前资料结构**没有**验收侧的认证字段，因此**不支持**"契约认证 vs 验收期望认证"的交叉比对；本场景不涉及、也不宣称该能力。如后续需要认证交叉比较，需先在资料结构中增补验收侧认证字段并扩展工具职责，另行设计。

### 2.5 `scenario_coverage_gap` —— 关键验收要求缺少验证步骤（期望 `BLOCKED`）

与 `scenario_ok` 的差异：存在 `severity: critical` 的验收要求 `AC-1`，但 `verification_steps` 为空（或未包含指向 `AC-1` 的步骤）。

```yaml
verification_steps: []
```

`inspect_acceptance_coverage` 报：关键验收要求 `AC-1` 无对应验证步骤（warning 或 blocker）。**规则定为：critical 验收要求缺验证步骤 = blocker**（因为关键要求未被验证会阻止安全交付）。期望 `BLOCKED`。若该要求为 `normal` 严重度则记为 warning，期望 `NEEDS_REVIEW`。

> 场景与期望状态的对应关系是**测试口径**，用于后续验证 `compute_final_status`。工具对某类问题判 blocker 还是 warning 的精确规则见第 3 节。

---

## 3. 四个核验工具的输入输出契约

四个工具均遵循**统一返回结构**与**统一失败结构**，便于 Agent 循环统一处理。所有工具**只读、无副作用**，仅访问预先提供的交付资料。

### 3.0 通用约定

工具入参统一为一个对象，含 `delivery`（交付资料，见第 1 节结构）与工具相关的可选参数。

统一成功返回：

```json
{
  "tool": "check_required_fields",
  "ok": true,
  "findings": [
    {
      "id": "F-0001",
      "severity": "blocker",
      "certainty": "confirmed",
      "code": "MISSING_REQUIRED_FIELD",
      "message": "必填字段 contact 缺失",
      "evidence": {
        "source": "delivery.contact",
        "expected": "非空字符串",
        "actual": null
      },
      "remediation": "补充交付负责人及联系方式"
    }
  ],
  "summary": "发现 1 个阻塞项"
}
```

字段说明：

- `tool`：工具名，恒等于被调用的四个工具名之一。
- `ok`：工具**是否成功执行**（`true` 表示校验逻辑正常跑完；即使发现 blocker 也是 `ok=true`，因为"发现问题"属于成功执行）。
- `findings[]`：本次检查发现的问题列表；无问题时为空数组 `[]`。
  - `severity`：`blocker` / `warning` / `pass`。
  - `certainty`：`confirmed`（确定发现）/ `uncertain`（需人工确认的风险）。
  - `code`：稳定问题码，供前端与测试断言，见各工具。
  - `evidence.source`：问题在交付资料中的定位路径（如 `delivery.contact`）。
  - `evidence.expected` / `evidence.actual`：预期值与实际值，用于报告展示与追溯。
  - `remediation`：整改建议（人读）。
- `summary`：人读的一句话总结。

统一失败返回（工具自身抛错、入参非法、资料无法解析等）：

```json
{
  "tool": "compare_environment_config",
  "ok": false,
  "findings": [],
  "error": {
    "code": "INVALID_INPUT",
    "message": "delivery 不是合法对象"
  },
  "summary": "工具执行失败"
}
```

失败规则：`ok=false` 时 `findings` 必须为空，`error.code` 取 `INVALID_INPUT`（入参/资料非法）或 `TOOL_ERROR`（执行期异常）。**工具失败绝不产生 finding，也绝不视为通过**（见第 4 节对"关键检查未完成"的处理）。工具失败不抛异常中断 Agent 循环，而是以 `ok=false` 结果回传给模型，由 Agent 决定是否重试或收尾。

### 3.1 `check_required_fields`

- 职责：检查第 1 节中标记"必填"的顶层字段是否完整且非空。
- 参数：`delivery`（必需，object）。
- 检查规则：
  - 每个必填顶层字段（`delivery_id`、`project_name`、`version`、`contact`、`target_environment`、`actual_deployment`、`api_contracts`、`acceptance_criteria`、`verification_steps`）缺失、为 `null`、空字符串或空列表 → 记一条 `severity=blocker`、`certainty=confirmed`、`code=MISSING_REQUIRED_FIELD` 的 finding。
- 结果：仅含 `MISSING_REQUIRED_FIELD` 类 finding。
- 失败：`delivery` 非对象 → `INVALID_INPUT`。

### 3.2 `compare_environment_config`

- 职责：比较 `target_environment` 与 `actual_deployment` 是否一致。
- 参数：`delivery`（必需，object）。
- 检查规则（severity 已冻结）：
  - `runtime` 不一致 → `blocker`，`code=ENV_RUNTIME_MISMATCH`。
  - `python_version` 不一致 → `blocker`，`code=ENV_PYTHON_VERSION_MISMATCH`。
  - `os` 不一致 → `warning`，`code=ENV_OS_MISMATCH`。
  - `target_environment.required_env_vars` 中 `required=true` 的变量，未出现在 `actual_deployment.env_vars_present` → `blocker`，`code=ENV_REQUIRED_VAR_MISSING`。
  - 每条差异 `certainty=confirmed`。
- 失败：任一环境对象缺失或非对象 → `INVALID_INPUT`。

### 3.3 `compare_api_acceptance`

- 职责：核对接口契约与验收检查项的一致性。
- 参数：`delivery`（必需，object）。
- 检查规则：
  - `acceptance_criteria[].related_api` 指向的 `endpoint_id` 在 `api_contracts` 中不存在 → `blocker`，`code=API_ENDPOINT_NOT_FOUND`。
  - 契约 `path` 与 `api_version` 前缀不一致（如 `api_version=v1` 但 `path` 以 `/api/v2/` 开头）→ `blocker`，`code=API_VERSION_CONFLICT`。
  - 契约 `auth` 为空或取值不在允许集合（`none`/`api_key`/`bearer_token`）→ `warning`，`code=API_AUTH_UNCLEAR`。
  - 全部 `certainty=confirmed`。
- 失败：`api_contracts` 或 `acceptance_criteria` 非列表 → `INVALID_INPUT`。

### 3.4 `inspect_acceptance_coverage`

- 职责：检查每个验收要求是否有对应验证步骤。
- 参数：`delivery`（必需，object）。
- 检查规则：
  - `severity=critical` 的验收要求，`verification_steps` 中无任何 `criterion_id` 指向它 → `blocker`，`code=COVERAGE_CRITICAL_GAP`。
  - `severity=normal` 的验收要求无对应验证步骤 → `warning`，`code=COVERAGE_NORMAL_GAP`。
  - `verification_steps[].criterion_id` 指向不存在的验收要求 → `warning`，`code=COVERAGE_ORPHAN_STEP`。
  - 全部 `certainty=confirmed`。
- 失败：`acceptance_criteria` 或 `verification_steps` 非列表 → `INVALID_INPUT`。


---

## 4. 最终状态判定规则（权威定义）

最终状态 `final_status` 只有 `READY` / `NEEDS_REVIEW` / `BLOCKED`，由确定性函数 `compute_final_status(findings, run_context)` 计算。**绝不采用 LLM 自行生成的状态。** 输入为全部工具的 finding 汇总与运行上下文（含各必需工具是否成功完成）。

### 4.0 run_context 结构（必需检查的语义）

`run_context` 承载"本次任务必需完成哪些检查"与"本次实际执行了哪些工具"两部分，两者分离以避免把"非必需且未执行"误判为未完成：

```json
{
  "required_tools": ["check_required_fields", "compare_environment_config", "..."],
  "tool_results": {
    "check_required_fields": { "ok": true, "findings": [] },
    "compare_environment_config": { "ok": false, "findings": [] }
  }
}
```

- `required_tools`：**本次任务必需完成的检查工具列表**。缺省时回落到全部四个核验工具。必需检查范围必须来自**任务规则或应用层上下文**，**不得根据 LLM 最终回答决定**。
- `tool_results`：**本次实际执行过的工具及其统一返回结果**（含 `ok` 字段）。
- **必需但未完成**：`required_tools` 中的工具在 `tool_results` 里没有 `ok=true` 的结果——包括两种情况：工具被调用但失败（`ok=false`），以及工具根本没被调用（键缺失）。两者都计入 `required_checks_incomplete`。
- **非必需且未执行**：不在 `required_tools` 中的工具未被执行，**不影响**最终状态。
- 这样 Agent 动态决定调用顺序时，只要 `required_tools` 覆盖了必需检查，就不会因为"某些工具还没调"而误得 `READY`；反过来，遗漏任一必需检查都会被优先级 1 压成 `BLOCKED`。

### 4.1 输入信号

- `blockers`：`severity=blocker` 且 `certainty=confirmed` 的 finding 数。
- `warnings`：`severity=warning` 的 finding 数（含 `certainty=uncertain`）。
- `uncertain_warnings`：`severity=warning` 且 `certainty=uncertain` 的 finding 数。
- `required_checks_incomplete`：`required_tools` 中，`tool_results` 未提供 `ok=true` 结果的数量（工具失败 `ok=false` 或**未被调用**都计入）。关键检查未成功完成**不得当作通过**。

### 4.2 判定表（按优先级从上到下，命中即返回）

| 优先级 | 条件 | 结果 | 理由 |
| --- | --- | --- | --- |
| 1 | `required_checks_incomplete > 0` | `BLOCKED` | 关键核验未完成，无法确认就绪，按最保守处理。 |
| 2 | `blockers > 0` | `BLOCKED` | 存在确定的关键资料缺失或配置冲突。 |
| 3 | `warnings > 0`（含 `uncertain_warnings`） | `NEEDS_REVIEW` | 存在警告、证据不足或需人工确认的问题。 |
| 4 | 以上均不满足（全 `pass`，无 warning，四工具均成功） | `READY` | 全部必需核验通过，无阻塞与关键不确定性。 |

### 4.3 关键原则

1. **`compute_final_status` 是纯确定性函数**，无随机、无模型参与，相同输入必得相同输出。
2. **关键检查未完成 = 不能就绪**：优先级 1 保证任何必需工具失败/缺失都把结果压到 `BLOCKED`，绝不因"其他都过了"而放行成 `READY`。
3. **确定问题 vs 未确认风险**：`certainty=confirmed` 的 blocker 直接进优先级 2；`certainty=uncertain` 的项只能进 `NEEDS_REVIEW`（优先级 3），不会单独造成 `BLOCKED`。
4. **合并优先级 `BLOCKED` > `NEEDS_REVIEW` > `READY`**：多项检查结果不同时，取最严重者。
5. **状态不可被模型覆盖**：三种状态值绝不能仅凭模型自然语言回答变更。模型只能在报告中提供解释文本，`final_status` 字段永远来自 `compute_final_status`。

---

## 5. 核验报告结构

`GET /api/runs/{run_id}/report` 返回的结构（与落库内容一致）：

```json
{
  "run_id": 12,
  "model_mode": "simulation",
  "delivery_id": "DLV-2024-001",
  "final_status": "BLOCKED",
  "summary": "发现 2 个阻塞项，1 个警告",
  "counts": { "blockers": 2, "warnings": 1, "pass": 3 },
  "required_checks_incomplete": 0,
  "findings": [
    {
      "id": "F-0001",
      "tool": "check_required_fields",
      "severity": "blocker",
      "certainty": "confirmed",
      "code": "MISSING_REQUIRED_FIELD",
      "message": "必填字段 contact 缺失",
      "evidence": { "source": "delivery.contact", "expected": "非空字符串", "actual": null },
      "remediation": "补充交付负责人及联系方式",
      "tool_event_id": 3
    }
  ],
  "model_explanation": "（模型对结论的自然语言解释，仅辅助阅读，不改变 final_status）",
  "created_at": "2026-10-13T09:00:00"
}
```

要点：

- `final_status` 来自 `compute_final_status`，`model_explanation` 仅辅助阅读。
- 每个 `finding` 通过 `tool_event_id` 关联到具体工具调用，实现**结论可追溯到证据来源**。
- `counts` 与 `findings` 由确定性汇总得出，与 `final_status` 一致。

ID 生成与关联规则（阶段 1.1 收尾，权威定义）：

- **四个核验工具不生成 finding 的 `id`，也不生成 `tool_event_id`**。工具只产出自身的结构化核验结果（severity/certainty/code/message/evidence/remediation），避免多个工具各自编号产生跨工具冲突。
- **`tool_event_id` 由 Agent 执行器生成**：每次工具调用被记录为一个独立的执行事件（对应数据库 `tool_events` 行的主键），是该次工具执行事件的标识。
- **finding 的任务内 `id`（如 `F-0001`）由汇总层统一分配**：在汇总全部工具结果时按顺序编号，作用范围为**单次核验任务内**唯一。
- 汇总层为每条 finding 记录其**来源工具执行事件**，即 `tool_event_id`，形成 **结论 → 工具执行事件 → 资料字段（evidence.source）** 的可追溯链。
- 本阶段尚未实现数据库与 Agent 执行器，因此工具与测试中不出现 `id`/`tool_event_id` 字段；二者在阶段 2 由汇总层/执行器补齐。

---

## 6. 数据库设计（SQLite，本阶段只设计不建库）

三张表：`runs`、`tool_events`、`findings`。主键自增，外键 `runs` → `tool_events` → `findings`。字段名与报告结构一致，`snake_case`。

### 6.1 `runs` —— 一次核验任务

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `id` | INTEGER PK AUTOINCREMENT | 运行 ID |
| `delivery_id` | TEXT | 关联的交付标识 |
| `delivery_snapshot_json` | TEXT NOT NULL | 输入交付资料的完整快照（保证可追溯） |
| `status` | TEXT NOT NULL | 运行态：`queued`/`running`/`succeeded`/`failed` |
| `final_status` | TEXT | 最终状态：`READY`/`NEEDS_REVIEW`/`BLOCKED`（运行未完成时为空） |
| `model_mode` | TEXT NOT NULL | `simulation`/`real` |
| `model_name` | TEXT | 使用的模型名（模拟模式可为内置标识） |
| `summary` | TEXT | 报告一句话总结 |
| `counts_json` | TEXT | `{blockers,warnings,pass}` |
| `required_checks_incomplete` | INTEGER NOT NULL DEFAULT 0 | 未完成的必需检查数 |
| `steps_used` | INTEGER NOT NULL DEFAULT 0 | 实际执行步数 |
| `duration_ms` | INTEGER | 总耗时（毫秒） |
| `created_at` | TEXT NOT NULL | 创建时间 |
| `finished_at` | TEXT | 完成时间 |

### 6.2 `tool_events` —— 工具调用记录

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `id` | INTEGER PK AUTOINCREMENT | 事件 ID |
| `run_id` | INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE | 所属运行 |
| `seq` | INTEGER NOT NULL | 本次运行内的调用序号 |
| `tool` | TEXT NOT NULL | 工具名（四个之一，或 `model` 表示模型回合） |
| `status` | TEXT NOT NULL | `succeeded`/`failed` |
| `params_json` | TEXT | 调用参数快照 |
| `result_json` | TEXT | 工具返回结果快照 |
| `error_code` | TEXT | 失败时的错误码（成功为空） |
| `created_at` | TEXT NOT NULL | 调用时间 |

### 6.3 `findings` —— 核验问题

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `id` | INTEGER PK AUTOINCREMENT | 问题 ID |
| `run_id` | INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE | 所属运行 |
| `tool_event_id` | INTEGER REFERENCES tool_events(id) | 来源工具调用（关联证据） |
| `severity` | TEXT NOT NULL | `blocker`/`warning`/`pass` |
| `certainty` | TEXT NOT NULL | `confirmed`/`uncertain` |
| `code` | TEXT NOT NULL | 稳定问题码 |
| `message` | TEXT NOT NULL | 人读描述 |
| `evidence_source` | TEXT | 资料定位路径 |
| `expected` | TEXT | 预期值 |
| `actual` | TEXT | 实际值 |
| `remediation` | TEXT | 整改建议 |
| `created_at` | TEXT NOT NULL | 记录时间 |

表间关联：`tool_events.run_id → runs.id`；`findings.run_id → runs.id`；`findings.tool_event_id → tool_events.id`（可空，允许记录非单一工具产生的汇总说明，当前第一版均来自工具）。

> 待决策：`findings.tool_event_id` 是否始终非空；是否需要 `updated_at`（当前按只追加设计，不设）。

---

## 7. API 契约

统一前缀 `/api/`。第一版 `POST /api/runs` 为**同步执行并直接返回报告**，不实现后台队列、异步任务与流式输出。

### 7.1 `GET /api/health`

- 用途：健康检查。
- 响应：`200 { "status": "ok", "service": "deliverygate-ai" }`。

### 7.2 `GET /api/scenarios`

- 用途：列出内置固定场景样例（第 2 节），供前端选择与测试复用。
- 响应：`200 { "scenarios": [ { "id": "scenario_ok", "name": "完整且无冲突", "expected_status": "READY" }, ... ] }`。

### 7.3 `POST /api/runs`

- 用途：创建核验任务并**同步执行**，直接返回报告。
- 请求体（第一版最小结构）：`{ "scenario_id": "scenario_missing", "mode": "simulation" }`。
  - `scenario_id`：必需，只能取第 2 节白名单中的预置场景标识。
  - `mode`：可选，仅允许 `"simulation"` 或 `"real"`，缺省 `"simulation"`。`real` 模式需要真实模型配置（见 README），配置缺失返回 503 且**绝不**回退模拟模式。
  - 第一版不开放任意交付资料对象、任意文件路径、任意工具列表提交；必需检查固定为全部四项。
- 成功响应：`200`，返回第 5 节报告结构（含 `run_id`、`final_status`、`findings` 等）。
- 典型错误：`404`（`scenario_id` 不在白名单）、`422`（请求体校验失败）、`503`（`real` 模式配置缺失）。
- 说明：`run_status=model_error`（模型请求失败）仍返回 `200` 并保留已有检查证据与落库记录——执行失败不是 HTTP 请求失败；`final_status` 始终为确定性计算结果。

### 7.4 `GET /api/runs/{run_id}`

- 用途：查看某次运行的执行状态与元信息（不重新执行核验）。
- 成功响应：`200 { "run_id": 12, "status": "succeeded", "final_status": "BLOCKED", "model_mode": "simulation", "model_name": null, "steps_used": 4, "duration_ms": 812, "created_at": "...", "finished_at": "...", "findings": [ ... ], "tool_events": [ ... ] }`；`findings`/`tool_events` 为任务内编号（`F-xxxx`/`TE-xxxx`）的结构化条目，不包含交付资料快照。
- 典型错误：`404`（run_id 不存在）。

### 7.5 `GET /api/runs/{run_id}/report`

- 用途：读取最终核验报告（第 5 节完整结构）。
- 成功响应：`200`，返回第 5 节报告结构。
- 典型错误：`404`（run_id 不存在）、`409`（运行尚未完成，无最终报告）。

### 7.6 三者关系

`POST /api/runs` 创建并执行任务、落库 `runs`/`tool_events`/`findings`；`GET /api/runs/{run_id}` 查询该任务的执行状态与元信息；`GET /api/runs/{run_id}/report` 读取最终报告。第一版因同步执行，创建后通常已可立即读到状态与报告；查询与报告是两次独立只读请求，用于后续支持异步或历史查看时保持契约稳定。



### 7.7 统一错误响应结构

所有 API 错误（非 2xx）统一返回：

```json
{ "error": { "code": "SCENARIO_NOT_FOUND", "message": "场景不存在：nope" } }
```

错误码与状态码（阶段 4.2 实现口径）：

| code | HTTP | 含义 |
| --- | --- | --- |
| `VALIDATION_ERROR` | 422 | 请求体/路径参数校验失败 |
| `SCENARIO_NOT_FOUND` | 404 | `scenario_id` 不在白名单目录 |
| `RUN_NOT_FOUND` | 404 | `run_id` 不存在 |
| `REPORT_NOT_READY` | 409 | 任务尚未完成，暂无最终报告 |
| `SCENARIO_LOAD_ERROR` | 500 | 场景文件缺失或解析失败 |
| `REAL_MODEL_CONFIG_MISSING` | 503 | `real` 模式模型配置缺失或客户端构造失败（不回退模拟） |
| `INTERNAL_ERROR` | 500 | 未预期的服务端异常（通用信息，不泄漏堆栈/密钥/环境变量值） |

错误响应绝不包含 API Key、堆栈或内部环境变量值。
