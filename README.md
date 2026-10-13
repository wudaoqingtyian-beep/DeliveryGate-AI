# AI 辅助企业软件交付前核验工具

**在软件交付、部署、联调之前，先把交付资料检查一遍的工具。**

很多项目出问题，不是代码写错了，而是交付环节掉链子：资料缺一块、环境配置前后矛盾、验收标准对不上号、关键验收项连测试步骤都没有。这类问题往往等到真正部署时才爆发，改起来又慢又贵。

AI 辅助企业软件交付前核验工具干的就是"提前排雷"：读取一份交付资料，自动查一遍，输出一份**带证据的问题清单**，并给出明确结论——可以交付（READY）、需要人工再看看（NEEDS_REVIEW）、还是先别交付（BLOCKED）。

它只做检查和提醒：**不自动部署，不碰生产环境。**

## 它能检查哪四件事

输入一份交付资料（YAML 或 JSON 格式），它查这四件事：

| 检查项 | 通俗点说就是 |
| --- | --- |
| 资料齐不齐 | 项目名、版本、联系人、目标环境、接口清单、验收标准……必填的有没有缺 |
| 环境有没有冲突 | 比如目标环境要求 Python 3.10，实际部署记录里写的却是 3.8；该配的环境变量配没配 |
| 接口和验收对不对得上 | 验收条目引用的接口存不存在，路径、认证方式写得规不规范 |
| 验收有没有兜底 | 关键验收项是不是每个都有对应的验证步骤 |

每个问题都带：严重程度（必须解决 / 建议确认）、出在哪个字段、期望值和实际值、修复建议。

## 结论是怎么算出来的

最终结论**不是 AI 拍脑袋说的**，是按固定规则算出来的：

1. 资料缺失、环境冲突、关键验收没覆盖这类硬伤 → **BLOCKED（先别交付）**
2. 有问题但不算致命、需要人再确认 → **NEEDS_REVIEW（人工复核）**
3. 没查出毛病 → **READY（可以交付）**

AI 在流程里只干两件事：决定"下一步查什么"，以及用人话解释结果。就算它嘴上说"可以交"，只要规则查出了硬伤，结论依然是 BLOCKED——这条有专门的自动化测试盯着。

## 快速开始

```bash
pip install -r requirements.txt
python -m pytest                    # 跑全部测试：209 项，不联网、不花钱
python -m uvicorn app.main:app --reload
```

浏览器打开 `http://127.0.0.1:8000/`，就是一个中文操作页面：选场景 → 点"开始核验" → 看结果，全程点鼠标。

第一次用，建议照着 **[docs/USER_GUIDE.md](docs/USER_GUIDE.md)** 走一遍——图文教程，从装环境到看懂结果，每步都有真实截图。

项目内置 5 个示例场景（1 个完全正常、4 个各埋了一类典型问题），不用准备任何资料就能试。

## 三种运行方式

| 方式 | 什么时候用 | 花不花钱 |
| --- | --- | --- |
| 模拟模式（simulation，默认） | 日常开发、演示 | 免费，不联网、不要 API Key |
| 自动化测试 | 改完代码跑一遍 | 免费，测试里用假服务器，不发真实请求 |
| 真实模型模式（real） | 让真正的大模型来决定下一步查什么 | **可能产生 API 费用** |

三者结果严格区分：模拟的标 `simulation`，真实的标 `real`。真实调用失败就明确报错，**绝不**偷偷换回模拟模式糊弄人。

## 接真实模型（可选）

只有选真实模型模式才需要配置。把环境变量设好（别写进代码、更别提交到 Git）：

| 环境变量 | 说明 |
| --- | --- |
| `DELIVERYGATE_LLM_BASE_URL` | 模型服务地址，如 `https://api.openai.com/v1`（不用带 `/chat/completions`，程序自动拼上） |
| `DELIVERYGATE_LLM_API_KEY` | API Key |
| `DELIVERYGATE_LLM_MODEL` | 模型名称 |
| `DELIVERYGATE_LLM_TIMEOUT_SECONDS` | 单次请求超时秒数（默认 30） |

照着 `.env.example` 填即可（`.env` 已被 Git 忽略，不会被提交）。

真实接口的联网测试默认是关的：没配置就自动跳过，不会假装通过。想跑（**会产生费用**）：

```bash
set DELIVERYGATE_RUN_REAL_API_TEST=1
set DELIVERYGATE_LLM_BASE_URL=https://api.openai.com/v1
set DELIVERYGATE_LLM_API_KEY=<你的key>
set DELIVERYGATE_LLM_MODEL=<模型名>
python -m pytest tests/integration -v
```

平时的 `python -m pytest` 只跑单元测试，永远不会发出真实请求。

## 网页界面能干什么

1. 选场景、选模式，发起一次核验；
2. 看结果：最终状态、问题清单、每一步检查的执行记录和证据来源；
3. 输入任务 ID，查历史任务详情或完整报告；
4. "最近访问"一键打开之前看过的任务（只存在你自己的浏览器里，不代表服务器完整历史）。

页面只负责展示，所有判断都在后端做——不会出现"网页一个样、接口另一个样"。

## 后端接口（5 个）

| 接口 | 干什么 |
| --- | --- |
| `GET /api/health` | 服务是否正常 |
| `GET /api/scenarios` | 列出 5 个内置场景 |
| `POST /api/runs` | 发起核验：`{"scenario_id": "scenario_ok", "mode": "simulation"}`，成功返回报告并存库 |
| `GET /api/runs/{run_id}` | 查任务详情（只读，不会重新跑一遍） |
| `GET /api/runs/{run_id}/report` | 查完整核验报告（问题编号、检查步骤、证据来源都能追溯） |

```bash
curl http://127.0.0.1:8000/api/scenarios
curl -X POST http://127.0.0.1:8000/api/runs -H "Content-Type: application/json" -d "{\"scenario_id\":\"scenario_missing\"}"
curl http://127.0.0.1:8000/api/runs/1
curl http://127.0.0.1:8000/api/runs/1/report
```

几个要点：

- 选了真实模式但没配环境变量 → 返回 `503`，并告诉你缺哪个变量，**不会**回退成模拟模式；
- 错误统一为 `{"error": {"code", "message"}}` 格式（如场景不存在 `404 SCENARIO_NOT_FOUND`），不泄露报错堆栈和密钥；
- 接口不接受任意文件路径，也不接受随意指定要跑哪些检查——检查项是固定的。

## 数据存在哪

SQLite 单文件，默认在 `data/deliverygate.db`（想换位置就设 `DELIVERYGATE_DB_PATH`）。每次核验存三样东西：任务本身、每一步检查的执行记录、发现的问题。

- 要么全存进去，要么全不存——中途出错整体回滚，不会留半截脏数据；
- 每个问题都能顺藤摸瓜找到"是哪一步检查发现的、依据是哪个字段"——报告里的 `F-0001`（问题编号）、`TE-0001`（检查步骤编号）就是干这个的；
- "这次跑得顺不顺"（如模型出错 `model_error`）和"能不能交付"（READY 等）是两回事，分开记录、互不干扰。

## 技术栈

Python 3.12 + FastAPI + SQLite（标准库，没用 ORM）+ 原生 HTML/CSS/JS + pytest。执行流程是自己写的轻量循环，没有引入 LangGraph、CrewAI 这类框架——直接依赖一共 6 个，`pip install -r requirements.txt` 装完就能跑。

## 项目结构

```
app/
  agent/         # 执行流程、模型接入
  tools/         # 四个检查工具
  services/      # 结论判定等业务逻辑
  api/           # 5 个接口
  repositories/  # 数据库存取
  static/        # 网页界面
tests/           # 测试（209 项）
docs/            # 文档
data/            # 5 个示例场景
```

## 文档

- [docs/USER_GUIDE.md](docs/USER_GUIDE.md) —— **先看这个**：图文使用手册，附真实截图
- [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) —— 做什么、不做什么、业务规则
- [docs/DATA_CONTRACTS.md](docs/DATA_CONTRACTS.md) —— 资料格式、报告格式的权威定义
- [docs/AGENT_DESIGN.md](docs/AGENT_DESIGN.md) —— 执行流程的设计细节

这几份文档在术语、工具名、状态值、接口路径上保持一致。

## 不做的事

- 不自动部署，不改生产环境；
- 不做登录、权限、多租户；
- 不解析 PDF/Word 等复杂文档（资料只支持 YAML/JSON）；
- 不给模型任意执行 SQL、Shell、访问文件的权力；
- 没做的功能（RAG、多 Agent 编排等）不硬吹——模拟结果永远标注 `simulation`，绝不冒充真实模型（`real`）结果。
