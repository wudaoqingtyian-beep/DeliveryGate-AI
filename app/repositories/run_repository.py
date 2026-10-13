"""RunResult 的 SQLite 持久化与读取（最小 Repository）。

职责：
- 将一次完整的 RunResult（连同 ToolEvent 与 findings）在同一个事务中写入
  runs / tool_events / findings 三张表；任一步失败则整体回滚，不留下部分数据。
- 按 run_id 读取任务、工具执行事件与 findings。

ID 处理：
- 数据库自增整数主键 id 用于内部关联与外键。
- AgentRunner 生成的任务内编号（tool_event_id 如 TE-0001、finding id 如 F-0001）
  是字符串、仅单次任务内唯一，单独存为 task_event_id / task_finding_id。
- findings 通过 tool_events 的整数主键建立外键，同时保留 task_event_id 便于展示。

JSON 字段一律用 json.dumps/loads 序列化，绝不写入 Python repr。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from app.agent.runner import RunResult, ToolEvent
from app.repositories import db


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_text(value):
    """把 evidence 的 expected/actual 规范为可存 TEXT 的值（复杂值转 JSON）。"""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


class RepositoryError(Exception):
    """持久化层的明确错误（关联失败、写入失败等）。"""


class RunRepository:
    """最小 Repository：保存一次核验任务并支持按 run_id 读取。"""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or db.get_db_path()
        db.init_db(self.db_path)

    # ---------------------------------------------------------------- 保存
    def save_run(
        self,
        run_result: RunResult,
        delivery: dict,
        model_name: Optional[str] = None,
    ) -> int:
        """在一个事务中保存 runs + tool_events + findings，返回 run_id（runs.id）。

        任一步失败整体回滚并抛 RepositoryError，不留下部分数据。
        """
        if not isinstance(delivery, dict):
            raise RepositoryError("delivery 必须是 dict，无法保存快照")

        conn = db.get_connection(self.db_path)
        try:
            conn.execute("BEGIN")
            run_id = self._insert_run(conn, run_result, delivery, model_name)
            event_pk_by_task_id = self._insert_tool_events(
                conn, run_id, run_result.tool_events
            )
            self._insert_findings(
                conn, run_id, run_result.findings, event_pk_by_task_id
            )
            conn.commit()
            return run_id
        except RepositoryError:
            conn.rollback()
            raise
        except Exception as exc:  # noqa: BLE001 - 任何写入失败都回滚
            conn.rollback()
            raise RepositoryError(f"保存核验任务失败：{exc}") from exc
        finally:
            conn.close()

    def _insert_run(
        self,
        conn: sqlite3.Connection,
        rr: RunResult,
        delivery: dict,
        model_name: Optional[str],
    ) -> int:
        cur = conn.execute(
            """
            INSERT INTO runs (
                delivery_id, delivery_snapshot_json, status, final_status,
                model_mode, model_name, summary, counts_json,
                required_checks_incomplete, steps_used, tool_calls_used,
                duration_ms, model_explanation, required_tools_json,
                created_at, finished_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rr.delivery_id,
                json.dumps(delivery, ensure_ascii=False),
                rr.run_status,
                rr.final_status,
                rr.model_mode,
                model_name,
                rr.summary,
                json.dumps(rr.counts, ensure_ascii=False),
                rr.required_checks_incomplete,
                rr.steps_used,
                rr.tool_calls_used,
                rr.duration_ms,
                rr.model_explanation or None,
                json.dumps(rr.required_tools, ensure_ascii=False),
                _utcnow_iso(),
                _utcnow_iso(),
            ),
        )
        return int(cur.lastrowid)

    def _insert_tool_events(
        self,
        conn: sqlite3.Connection,
        run_id: int,
        events: list[ToolEvent],
    ) -> dict[str, int]:
        """插入工具事件，返回 {task_event_id: 数据库整数主键} 映射。"""
        mapping: dict[str, int] = {}
        for seq, ev in enumerate(events, start=1):
            cur = conn.execute(
                """
                INSERT INTO tool_events (
                    run_id, seq, task_event_id, tool, status,
                    params_json, result_json, error_code, error_message, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    seq,
                    ev.tool_event_id,
                    ev.tool,
                    ev.status,
                    json.dumps(ev.arguments, ensure_ascii=False),
                    json.dumps(ev.result, ensure_ascii=False)
                    if ev.result is not None
                    else None,
                    ev.error_code,
                    ev.error_message,
                    _utcnow_iso(),
                ),
            )
            mapping[ev.tool_event_id] = int(cur.lastrowid)
        return mapping

    def _insert_findings(
        self,
        conn: sqlite3.Connection,
        run_id: int,
        findings: list[dict],
        event_pk_by_task_id: dict[str, int],
    ) -> None:
        for f in findings:
            task_event_id = f.get("tool_event_id")
            # 关联到数据库整数主键；任务内编号找不到对应事件则记 None（可空）
            event_pk = event_pk_by_task_id.get(task_event_id)
            evidence = f.get("evidence") or {}
            conn.execute(
                """
                INSERT INTO findings (
                    run_id, tool_event_id, task_finding_id, task_event_id,
                    severity, certainty, code, message,
                    evidence_source, expected, actual, remediation, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    event_pk,
                    f.get("id"),
                    task_event_id,
                    f.get("severity"),
                    f.get("certainty"),
                    f.get("code"),
                    f.get("message"),
                    evidence.get("source"),
                    _as_text(evidence.get("expected")),
                    _as_text(evidence.get("actual")),
                    f.get("remediation"),
                    _utcnow_iso(),
                ),
            )

    # ---------------------------------------------------------------- 读取
    def get_run(self, run_id: int) -> Optional[dict]:
        """按 run_id 读取任务信息；不存在返回 None。"""
        conn = db.get_connection(self.db_path)
        try:
            row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                return None
            return self._row_to_run(row)
        finally:
            conn.close()

    def get_tool_events(self, run_id: int) -> list[dict]:
        conn = db.get_connection(self.db_path)
        try:
            rows = conn.execute(
                "SELECT * FROM tool_events WHERE run_id = ? ORDER BY seq",
                (run_id,),
            ).fetchall()
            return [self._row_to_event(r) for r in rows]
        finally:
            conn.close()

    def get_findings(self, run_id: int) -> list[dict]:
        conn = db.get_connection(self.db_path)
        try:
            rows = conn.execute(
                "SELECT * FROM findings WHERE run_id = ? ORDER BY id",
                (run_id,),
            ).fetchall()
            return [self._row_to_finding(r) for r in rows]
        finally:
            conn.close()

    def get_full_run(self, run_id: int) -> Optional[dict]:
        """一次读取任务 + 工具事件 + findings，便于生成报告。"""
        run = self.get_run(run_id)
        if run is None:
            return None
        run["tool_events"] = self.get_tool_events(run_id)
        run["findings"] = self.get_findings(run_id)
        return run

    # ---- 行 -> dict（JSON 字段还原） ----
    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["counts"] = json.loads(d.pop("counts_json") or "{}")
        d["required_tools"] = json.loads(d.pop("required_tools_json") or "[]")
        d["delivery_snapshot"] = json.loads(d.pop("delivery_snapshot_json"))
        return d

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["params"] = json.loads(d.pop("params_json") or "{}")
        result_json = d.pop("result_json")
        d["result"] = json.loads(result_json) if result_json else None
        return d

    @staticmethod
    def _row_to_finding(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["evidence"] = {
            "source": d.pop("evidence_source"),
            "expected": d.pop("expected"),
            "actual": d.pop("actual"),
        }
        return d

