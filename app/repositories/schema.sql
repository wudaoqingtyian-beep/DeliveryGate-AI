-- DeliveryGate AI SQLite schema（标准库 sqlite3，无 ORM/迁移框架）
-- 对齐 docs/DATA_CONTRACTS.md 第 6 节。
--
-- ID 作用域说明：
--   各表自增整数主键 id 仅用于数据库内部关联与外键。
--   任务内编号（AgentRunner 生成的 TE-0001 / F-0001）是字符串、仅在单次任务内唯一，
--   不假定跨任务全局唯一，因此单独存为 task_event_id / task_finding_id 列用于展示与追溯。
--   findings 通过数据库整数主键 tool_events.id 建立外键关联。

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  delivery_id TEXT,
  delivery_snapshot_json TEXT NOT NULL,
  status TEXT NOT NULL,                 -- 执行态：queued/running/succeeded/failed/...
  final_status TEXT,                    -- 交付就绪态：READY/NEEDS_REVIEW/BLOCKED（未完成可空）
  model_mode TEXT NOT NULL,             -- simulation / real
  model_name TEXT,                      -- 模型名（模拟模式可为内置标识）
  summary TEXT,
  counts_json TEXT,                     -- {blockers,warnings,pass}
  required_checks_incomplete INTEGER NOT NULL DEFAULT 0,
  steps_used INTEGER NOT NULL DEFAULT 0,
  tool_calls_used INTEGER NOT NULL DEFAULT 0,
  duration_ms INTEGER,
  model_explanation TEXT,               -- 可选；不用于推导最终状态
  required_tools_json TEXT,             -- 本次必需检查清单
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  finished_at TEXT
);

CREATE TABLE IF NOT EXISTS tool_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  seq INTEGER NOT NULL,                 -- 本次运行内的调用序号
  task_event_id TEXT NOT NULL,          -- 任务内编号，如 TE-0001
  tool TEXT NOT NULL,
  status TEXT NOT NULL,                 -- succeeded / failed
  params_json TEXT,
  result_json TEXT,
  error_code TEXT,
  error_message TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS findings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  tool_event_id INTEGER REFERENCES tool_events(id),  -- 数据库主键关联来源事件
  task_finding_id TEXT NOT NULL,        -- 任务内编号，如 F-0001
  task_event_id TEXT,                   -- 来源事件的任务内编号（展示/追溯用）
  severity TEXT NOT NULL,               -- blocker / warning / pass
  certainty TEXT NOT NULL,              -- confirmed / uncertain
  code TEXT NOT NULL,
  message TEXT NOT NULL,
  evidence_source TEXT,
  expected TEXT,
  actual TEXT,
  remediation TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_tool_events_run ON tool_events(run_id);
CREATE INDEX IF NOT EXISTS idx_findings_run ON findings(run_id);
CREATE INDEX IF NOT EXISTS idx_findings_tool_event ON findings(tool_event_id);
