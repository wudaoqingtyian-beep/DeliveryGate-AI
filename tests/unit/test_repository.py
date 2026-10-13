"""SQLite 持久化层测试（临时数据库，不触碰真实开发库）。

覆盖：建表、RunResult 事务性保存与读取、多 ToolEvent/findings、任务内编号与
run_id 关联、finding 追溯到 tool_event_id 与 evidence.source、跨任务编号不冲突、
model_explanation 空/非空、执行态与交付态分别保存、外键约束、事务回滚不留部分
数据、重连后历史仍在、五场景结果持久化后保持原最终状态。
"""

from __future__ import annotations

import sqlite3

import pytest

from app.agent import run_verification
from app.repositories import RunRepository, RepositoryError, db
from app.repositories.run_repository import RunRepository as RR
from tests.conftest import SCENARIO_EXPECTED_STATUS, load_scenario


@pytest.fixture
def repo(tmp_path):
    return RunRepository(db_path=str(tmp_path / "test.db"))


def _make_run(scenario="scenario_missing"):
    delivery = load_scenario(scenario)
    return delivery, run_verification(delivery)


# ---------------- 初始化 ----------------
def test_init_creates_three_tables(tmp_path):
    db_path = str(tmp_path / "init.db")
    db.init_db(db_path)
    conn = db.get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        names = {r["name"] for r in rows}
        assert {"runs", "tool_events", "findings"}.issubset(names)
    finally:
        conn.close()


def test_init_creates_parent_dir(tmp_path):
    db_path = str(tmp_path / "nested" / "dir" / "test.db")
    db.init_db(db_path)
    conn = db.get_connection(db_path)
    conn.close()  # 能打开即说明目录已创建


def test_foreign_keys_enabled(repo):
    conn = db.get_connection(repo.db_path)
    try:
        row = conn.execute("PRAGMA foreign_keys").fetchone()
        assert row[0] == 1
    finally:
        conn.close()


# ---------------- 保存与读取 ----------------
def test_save_and_read_full_run(repo):
    delivery, res = _make_run("scenario_missing")
    run_id = repo.save_run(res, delivery, model_name="sim-internal")

    run = repo.get_run(run_id)
    assert run is not None
    assert run["final_status"] == res.final_status == "BLOCKED"
    assert run["model_mode"] == "simulation"
    assert run["model_name"] == "sim-internal"
    assert run["status"] == res.run_status
    assert run["delivery_snapshot"] == delivery
    assert run["counts"] == res.counts


def test_save_returns_incrementing_run_ids(repo):
    d1, r1 = _make_run("scenario_ok")
    d2, r2 = _make_run("scenario_missing")
    id1 = repo.save_run(r1, d1)
    id2 = repo.save_run(r2, d2)
    assert id2 == id1 + 1


def test_tool_events_saved_and_ordered(repo):
    delivery, res = _make_run("scenario_missing")
    run_id = repo.save_run(res, delivery)
    events = repo.get_tool_events(run_id)
    assert len(events) == len(res.tool_events)
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert [e["task_event_id"] for e in events] == [
        e.tool_event_id for e in res.tool_events
    ]


def test_findings_saved_with_evidence(repo):
    delivery, res = _make_run("scenario_missing")
    run_id = repo.save_run(res, delivery)
    findings = repo.get_findings(run_id)
    assert len(findings) == len(res.findings)
    for f in findings:
        assert f["evidence"]["source"]
        assert f["code"]
        assert f["severity"] in ("blocker", "warning", "pass")


# ---------------- 追溯与关联 ----------------
def test_finding_traces_to_tool_event_and_source(repo):
    delivery, res = _make_run("scenario_missing")
    run_id = repo.save_run(res, delivery)
    findings = repo.get_findings(run_id)
    events = repo.get_tool_events(run_id)
    event_pks = {e["id"] for e in events}
    # 任务内编号 -> 数据库主键 映射一致
    task_to_pk = {e["task_event_id"]: e["id"] for e in events}
    for f in findings:
        assert f["evidence"]["source"]
        if f["task_event_id"]:
            assert f["tool_event_id"] == task_to_pk[f["task_event_id"]]
            assert f["tool_event_id"] in event_pks


def test_task_internal_ids_do_not_conflict_across_runs(repo):
    # 两个任务各自有 TE-0001/F-0001，但数据库主键不同、互不冲突
    d1, r1 = _make_run("scenario_missing")
    d2, r2 = _make_run("scenario_missing")
    id1 = repo.save_run(r1, d1)
    id2 = repo.save_run(r2, d2)

    e1 = repo.get_tool_events(id1)
    e2 = repo.get_tool_events(id2)
    assert e1[0]["task_event_id"] == "TE-0001"
    assert e2[0]["task_event_id"] == "TE-0001"
    # 数据库主键全局唯一
    assert e1[0]["id"] != e2[0]["id"]

    f1 = repo.get_findings(id1)
    f2 = repo.get_findings(id2)
    assert f1[0]["task_finding_id"] == "F-0001"
    assert f2[0]["task_finding_id"] == "F-0001"
    assert f1[0]["id"] != f2[0]["id"]
    # finding 的外键只关联本任务的事件
    e1_pks = {e["id"] for e in e1}
    e2_pks = {e["id"] for e in e2}
    assert all(f["tool_event_id"] in e1_pks for f in f1)
    assert all(f["tool_event_id"] in e2_pks for f in f2)


def test_get_full_run_returns_everything(repo):
    delivery, res = _make_run("scenario_missing")
    run_id = repo.save_run(res, delivery)
    full = repo.get_full_run(run_id)
    assert full["final_status"] == res.final_status
    assert len(full["tool_events"]) == len(res.tool_events)
    assert len(full["findings"]) == len(res.findings)


def test_get_missing_run_returns_none(repo):
    assert repo.get_run(999) is None
    assert repo.get_full_run(999) is None
    assert repo.get_tool_events(999) == []
    assert repo.get_findings(999) == []


# ---------------- model_explanation 空/非空 ----------------
def test_model_explanation_empty_and_nonempty(repo):
    d1, r1 = _make_run("scenario_ok")   # 模拟模式有解释文本
    id1 = repo.save_run(r1, d1)
    assert repo.get_run(id1)["model_explanation"]

    # 构造一个空解释的 RunResult（复制再清空）
    import copy
    r2 = copy.deepcopy(r1)
    r2.model_explanation = ""
    id2 = repo.save_run(r2, d1)
    assert repo.get_run(id2)["model_explanation"] is None


# ---------------- 执行态 vs 交付态 ----------------
def test_execution_status_and_delivery_status_separate(repo):
    delivery, res = _make_run("scenario_missing")
    run_id = repo.save_run(res, delivery)
    run = repo.get_run(run_id)
    # 执行态 succeeded（流程跑完），交付态 BLOCKED（有阻塞项）——两者不同
    assert run["status"] == "succeeded"
    assert run["final_status"] == "BLOCKED"


# ---------------- 外键约束 ----------------
def test_foreign_key_violation_rejected(tmp_path):
    db_path = str(tmp_path / "fk.db")
    db.init_db(db_path)
    conn = db.get_connection(db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO tool_events (run_id, seq, task_event_id, tool, status) "
                "VALUES (999, 1, 'TE-0001', 'x', 'succeeded')"
            )
    finally:
        conn.close()



# ---------------- 事务回滚：写入失败不留部分数据 ----------------
def test_transaction_rollback_on_finding_failure(repo, monkeypatch):
    delivery, res = _make_run("scenario_missing")  # 有 findings

    # 让 _insert_findings 抛错，验证整个事务回滚（runs/tool_events 也不应留下）
    def boom(*args, **kwargs):
        raise RuntimeError("模拟 findings 写入失败")

    monkeypatch.setattr(RR, "_insert_findings", boom)

    with pytest.raises(RepositoryError):
        repo.save_run(res, delivery)

    # 回滚后应查不到任何 run
    conn = db.get_connection(repo.db_path)
    try:
        n_runs = conn.execute("SELECT COUNT(*) AS c FROM runs").fetchone()["c"]
        n_events = conn.execute("SELECT COUNT(*) AS c FROM tool_events").fetchone()["c"]
        n_findings = conn.execute("SELECT COUNT(*) AS c FROM findings").fetchone()["c"]
    finally:
        conn.close()
    assert n_runs == 0
    assert n_events == 0
    assert n_findings == 0


def test_non_dict_delivery_rejected(repo):
    delivery, res = _make_run("scenario_ok")
    with pytest.raises(RepositoryError):
        repo.save_run(res, "not-a-dict")


# ---------------- 重连后历史仍在 ----------------
def test_history_survives_reconnect(tmp_path):
    db_path = str(tmp_path / "persist.db")
    d, res = _make_run("scenario_missing")
    repo1 = RunRepository(db_path=db_path)
    run_id = repo1.save_run(res, d)

    # 用全新连接读取
    repo2 = RunRepository(db_path=db_path)
    run = repo2.get_run(run_id)
    assert run is not None
    assert run["final_status"] == res.final_status
    assert len(repo2.get_tool_events(run_id)) == len(res.tool_events)
    assert len(repo2.get_findings(run_id)) == len(res.findings)


def test_init_does_not_wipe_existing_data(tmp_path):
    db_path = str(tmp_path / "keep.db")
    d, res = _make_run("scenario_missing")
    repo = RunRepository(db_path=db_path)
    run_id = repo.save_run(res, d)
    # 再次 init（模拟重启）不应删除既有数据
    db.init_db(db_path)
    assert RunRepository(db_path=db_path).get_run(run_id) is not None


# ---------------- 五个场景持久化后保持原最终状态 ----------------
@pytest.mark.parametrize("name,expected", sorted(SCENARIO_EXPECTED_STATUS.items()))
def test_all_scenarios_persist_with_expected_status(repo, name, expected):
    delivery = load_scenario(name)
    res = run_verification(delivery)
    assert res.final_status == expected  # 先确认内存态正确
    run_id = repo.save_run(res, delivery, model_name="sim")
    run = repo.get_run(run_id)
    assert run["final_status"] == expected  # 持久化后仍一致

