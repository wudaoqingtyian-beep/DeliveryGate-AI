# DeliveryGate AI —— Agent 执行设计（AGENT_DESIGN）

版本：v0.1（阶段 0 冻结稿）
依赖：本文件定义 Agent 的执行循环、工具调用机制、状态判定原则、停止条件与异常处理。工具名与字段以 `DATA_CONTRACTS.md` 为准；功能范围与验收以 `REQUIREMENTS.md` 为准。本阶段**不实现**执行循环。

---

## 1. 设计定位

DeliveryGate AI 第一版为**单 Agent**，自建轻量级 Tool Calling Loop，不引入 LangGraph、CrewAI 等编排框架。职责划分严格固定：

- **LLM 负责**：依据任务与已有证据**选择调用哪个工具**、依据工具返回结果**决定下一步**、在最终报告中**解释**检查结果。
- **确定性 Python 负责**：四个工具的具体校验逻辑，以及**最终就绪状态的判定**。

一句话原则：**LLM 决定"下一步做什么"，确定性代码决定"结论是什么"。**

## 2. 执行循环（准备 → 调用 → 判断 → 校验 → 执行 → 回传 → 决策 → 收尾）

一次核验任务的主循环如下（伪流程，阶段 1 再实现）：

```
1. 准备：载入交付资料快照，构造系统提示与初始任务上下文。
2. 调用模型：请求模型输出（工具调用 or 结束）。
3. 判断动作：
   - 若模型请求调用工具 → 进入 4；
   - 若模型请求结束（无待办工具）→ 进入 8。
4. 校验：校验工具名是否在四个白名单内、参数是否符合该工具 schema。
   - 未知工具 / 参数非法 → 不执行，将错误结果回传给模型（见第 5 节）。
5. 执行：调用受控工具（确定性校验，只读，无副作用），捕获异常。
6. 记录与回传：将工具名、参数、结果/异常写入 tool_events，把结果回传模型。
7. 决策：依据新证据，返回 2 决定下一步；同时检查停止条件（见第 4 节）。
8. 收尾：模型给出自然语言总结；汇总全部 finding 与证据。
9. 定状态：调用确定性 compute_final_status() 计算 final_status，覆盖任何模型自称状态。
10. 生成报告：落库 runs/findings，产出报告结构（见 DATA_CONTRACTS 第 5 节）。
```

## 3. 工具选择与动态决策

Agent 通过 OpenAI Chat Completions 的 `tools` 字段把四个工具的 JSON Schema 暴露给模型，模型以 `tool_calls` 形式发起调用。Agent **不固定顺序执行全部工具**，而是依据任务与证据动态选择。

## 4. 停止条件与循环安全

Agent 循环必须同时满足**正常结束条件**与**强制停止上限**，二者缺一不可。

### 4.1 正常结束

- 模型明确请求结束，且当前证据足以支撑结论 → 正常收尾（进入第 6 节定状态）。
- Agent 在结束前应确保**四个必需核验要么已成功执行、要么已明确记录为无法执行**（记 `tool_events.status=failed`），不允许"静默跳过"。

### 4.2 强制停止上限（初始建议值，未经压测验证）

| 限制项 | 初始建议值 | 说明 |
| --- | --- | --- |
| 最大执行步数 `MAX_STEPS` | 12 | 单次任务内"模型调用+工具执行"的总回合数上限。 |
| 单次任务最大工具调用次数 `MAX_TOOL_CALLS` | 8 | 同一工具或不同工具的工具调用累计次数上限。 |
| 单次模型请求超时 | 30s | 单次 Chat Completions 请求超时。 |
| 总体任务超时 | 120s | 整个核验任务的墙钟上限。 |

> 以上为阶段 0 的**初始配置建议**，用于给出明确上限，**不是经压测验证的最佳值**。阶段 1 应以真实运行数据复核。

### 4.3 防无限循环

- **步数上限**：达到 `MAX_STEPS` 仍未结束 → 强制停止，`runs.status=succeeded` 但标记"达到最大步数"，最终状态由已收集证据经确定性规则计算（通常不会是 `READY`）。
- **工具调用上限**：达到 `MAX_TOOL_CALLS` → 拒绝新的工具调用，提示模型直接收尾。
- **重复调用检测**：同一工具 + 完全相同的参数（按规范化 JSON 比较）连续命中 → 不重复执行，返回上一次结果并提示模型换角度或收尾，避免原地打转。
- **总体超时**：超过总体墙钟上限 → 中断循环，按已有证据收尾并记录"超时停止"。

## 5. 异常处理原则

| 异常 | 处理方式 |
| --- | --- |
| 模型请求超时 | 捕获超时，可重试一次；仍失败则该回合记为 `model_error`，Agent 依据已有证据收尾，最终状态按确定性规则计算（因关键检查可能缺失，不会是 `READY`）。 |
| 模型返回非法 JSON / 无 `tool_calls` 也无结束意图 | 记为 `model_error`，回传纠错提示一次；仍失败则收尾。 |
| 未知工具名 | **不执行**，返回 `{ok:false, error.code:UNKNOWN_TOOL}`，提示模型从四个白名单工具中选择。 |
| 无效工具参数（不符合 schema） | **不执行**，返回 `{ok:false, error.code:INVALID_PARAMS}`，附校验错误，提示模型修正参数。 |
| 工具执行失败 | 工具以 `ok=false` 返回（不抛异常中断循环），记 `tool_events.status=failed`；该检查视为**未完成**，不得计入通过。 |
| 重复调用 | 按第 4.3 节处理，返回缓存结果，不重复执行。 |

**核心原则**：任何模型侧或工具侧失败，都不能伪造出一个 `READY`。失败只影响"证据是否充分"，最终状态仍由确定性规则在既有证据上计算。

## 6. 模拟与真实模式的区分

- 模式取值 `simulation` / `real`，写入 `runs.model_mode` 与每次报告头部。
- **真实模式**（`real`）：通过 `OpenAICompatibleClient` 经 HTTPX 调用兼容 OpenAI Chat Completions 的接口发起真实工具调用。配置来自环境变量 `DELIVERYGATE_LLM_BASE_URL` / `DELIVERYGATE_LLM_API_KEY` / `DELIVERYGATE_LLM_MODEL` / `DELIVERYGATE_LLM_TIMEOUT_SECONDS`（阶段 3 已实现）。
- **模拟模式**（`simulation`）：`SimulationClient` 不发起任何网络请求、不需要 API Key，用确定性规则选择工具，**所有模拟产出必须显式标识为模拟**（如解释文本带 `[Simulated]` 前缀），**绝不冒充真实模型执行结果**。用于无 API Key 时跑通流程与测试。
- **严格分离**：真实客户端配置缺失或请求失败时抛 `ModelClientError`，执行器记为 `model_error` 并按确定性规则收尾，**绝不静默切换到模拟模式**，也绝不用模拟输出冒充真实结果。
- 模式不影响 `compute_final_status` 的确定性——但报告必须标明本次运行所属模式，便于人判断可信度。

### 6.1 工具调用消息协议（阶段 3 收尾）

多轮工具调用的消息历史遵循 OpenAI Chat Completions 协议：

1. 模型返回的 assistant 消息含 `tool_calls[]`，每个为 `{id, type:"function", function:{name, arguments}}`，其中 `arguments` 是 **JSON 字符串**。
2. 执行器为每个工具调用执行后，生成 `role:"tool"` 消息，用 `tool_call_id` 关联到对应的模型调用 `id`，`content` 为工具结果的 JSON。
3. 模型一次返回多个工具调用时，每个工具结果都关联各自的原始 `tool_call_id`。
4. **模型调用 ID 与系统内部 `tool_event_id` 分开**：前者维持模型协议的调用关联，后者标识本次实际执行事件；finding 仍由汇总层分配任务内 ID，追溯链 `finding.id → tool_event_id → evidence.source` 不变。

### 6.2 真实客户端的错误与重试（阶段 3 收尾）

- 连接失败、传输异常、请求超时：最多自动重试一次。
- HTTP 500/502/503/504：最多自动重试一次。
- HTTP 400/401/403/404/422：明确错误，不重试。
- HTTP 429 限流：不自动重试，返回明确限流错误。
- 响应 JSON 非法、缺字段、工具参数无效：不重试，抛 `ModelClientError`。
- 所有异常信息与日志均不含 API Key。
- 单次请求超时由客户端控制（可配置）；任务总时长由 `AgentRunner` 统一管理，二者不混为一谈。


**体现动态决策的场景（`scenario_missing`）**：

1. Agent 先调用 `check_required_fields`，发现 `actual_deployment.env_vars_present` 缺失关键变量信息（证据：`target_environment.required_env_vars` 中 `DB_URL.required=true`，但实际配置无对应项）。
2. 模型依据该结果，判断需要进一步确认环境差异，**接着调用 `compare_environment_config`**，而不是机械地按列表顺序走完所有工具。
3. `compare_environment_config` 返回 `blocker`（确认缺失）。模型据此判断环境核验已明确阻塞，转而评估是否需要 `inspect_acceptance_coverage` 补充证据，最终结束循环并汇总。

该场景说明：**下一步工具由上一次工具返回的证据驱动**，而非预设固定调用序列。
## 7. 记录与追溯

- 每一次模型回合与工具调用都写入 `tool_events`：工具名、参数快照、结果快照、执行状态（`succeeded`/`failed`）、错误码、时间。
- 每个 finding 通过 `tool_event_id` 关联到产生它的工具调用；`evidence.source` 再指回交付资料中的具体字段路径，形成 **结论 → 工具调用 → 资料字段** 的可追溯链。
- 最终报告中每个 finding 均带证据来源，人能据此核对结论依据的资料。

ID 分配边界（阶段 1.1 收尾，权威定义见 `DATA_CONTRACTS.md` 第 5 节）：

- **四个核验工具不生成 `id` / `tool_event_id`**，只产出结构化核验结果；避免各工具分别编号产生冲突。
- **`tool_event_id` 由 Agent 执行器生成**：每次工具调用记录为一个独立执行事件（对应 `tool_events` 行主键）。执行器在调用工具、拿到结果后，把该结果的 finding 标记上本次事件的 `tool_event_id`。
- **finding 的任务内 `id`（如 `F-0001`）由汇总层统一分配**：按汇总顺序编号，单次任务内唯一。
- 追溯链固定为：**finding.id → tool_event_id（来源执行事件）→ evidence.source（资料字段）**。

`run_context` 与必需检查（阶段 1.1 收尾）：

- Agent 执行器在收尾时构造 `run_context = {"required_tools": [...], "tool_results": {...}}`：`required_tools` 指明本次必需检查（来自任务规则，不由模型回答决定），`tool_results` 收录实际执行过的工具结果。
- 这样 Agent 动态决定调用顺序也不会遗漏必需检查：任一必需工具未成功完成（失败或未调用），`compute_final_status` 都会按优先级 1 判为 `BLOCKED`，不会误得 `READY`。

## 8. 待决策事项（阶段 1 前需确认）

1. 第 4.2 节的步数、工具调用次数、超时初始建议值需以真实运行数据复核，不作为已验证结论。
2. 模拟模式下 Agent 的"预设脚本"需要覆盖哪些场景序列（当前只需能跑通四个工具各至少一次）。
3. `model_explanation` 是否需要单独落库（当前仅存于报告结构，不单独建列）。
4. 真实模式下模型工具调用的重试策略（当前为失败重试一次）。

> 本节所列均为待决策项，本阶段未做实现与验证。

