"""FastAPI API 单元测试（TestClient + 临时 SQLite + 客户端替身）。

覆盖：健康检查、场景列表、创建执行、任务详情、核验报告、错误映射、
real/simulation 模式隔离（不触真实网络、密钥不泄漏）、持久化与只读查询
不重新执行。全部使用临时数据库，不污染默认库。
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agent.llm_client import (
    ModelClientError,
    OpenAICompatibleClient,
    SimulationClient,
)
from app.main import create_app
from app.repositories import db
from app.services import scenarios as scenario_service
from tests.conftest import SCENARIO_EXPECTED_STATUS

LLM_ENV_VARS = (
    "DELIVERYGATE_LLM_BASE_URL",
    "DELIVERYGATE_LLM_API_KEY",
    "DELIVERYGATE_LLM_MODEL",
)


# ---------------------------------------------------------------- 测试替身
class FailingClient(SimulationClient):
    """real 模式替身：decide 一律抛模型错误（模拟真实请求失败，不触网）。"""

    mode = "real"
    model = "test-model"

    def decide(self, messages, tools, timeout=None):
        raise ModelClientError("模拟模型请求失败")


class CountingFactory:
    """统计客户端构造次数，用于验证只读接口不重新执行。"""

    def __init__(self, inner=None):
        self.calls = 0
        self.inner = inner or SimulationClient()

    def __call__(self, mode):
        self.calls += 1
        return self.inner


@pytest.fixture
def app(tmp_path):
    return create_app(db_path=str(tmp_path / "api_test.db"))


@pytest.fixture
def client(app):
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------- 健康检查
def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "deliverygate-ai"}


# ---------------------------------------------------------------- 场景列表
def test_scenarios_list_five_fixed(client):
    resp = client.get("/api/scenarios")
    assert resp.status_code == 200
    scenarios = resp.json()["scenarios"]
    ids = [s["id"] for s in scenarios]
    assert ids == [
        "scenario_ok",
        "scenario_missing",
        "scenario_env_mismatch",
        "scenario_api_conflict",
        "scenario_coverage_gap",
    ]
    for s in scenarios:
        assert s["expected_status"] == SCENARIO_EXPECTED_STATUS[s["id"]]
        assert s["name"]  # 人读名称存在


def test_scenarios_load_error_returns_500_not_fake_ok(client, tmp_path, monkeypatch):
    # 目录为空（文件全部"丢失"）时必须报错，不得伪造正常列表
    monkeypatch.setattr(scenario_service, "SAMPLES_DIR", tmp_path / "empty")
    resp = client.get("/api/scenarios")
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "SCENARIO_LOAD_ERROR"


# ---------------------------------------------------------------- 创建任务
def test_create_run_simulation_default_mode(client):
    resp = client.post("/api/runs", json={"scenario_id": "scenario_ok"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["final_status"] == "READY"
    assert body["model_mode"] == "simulation"
    assert body["run_status"] == "succeeded"
    assert isinstance(body["run_id"], int)
    assert body["required_checks_incomplete"] == 0


def test_create_run_explicit_simulation_mode(client):
    resp = client.post(
        "/api/runs", json={"scenario_id": "scenario_ok", "mode": "simulation"}
    )
    assert resp.status_code == 200
    assert resp.json()["model_mode"] == "simulation"


def test_create_run_report_shape(client):
    resp = client.post("/api/runs", json={"scenario_id": "scenario_missing"})
    body = resp.json()
    # 对齐 DATA_CONTRACTS.md 第 5 节报告结构 + run_id
    for key in (
        "run_id", "model_mode", "delivery_id", "final_status", "run_status",
        "summary", "counts", "required_checks_incomplete", "findings",
        "tool_events", "model_explanation", "steps_used", "tool_calls_used",
        "duration_ms", "created_at",
    ):
        assert key in body
    assert set(body["counts"]) == {"blockers", "warnings", "pass"}


@pytest.mark.parametrize("name,expected", sorted(SCENARIO_EXPECTED_STATUS.items()))
def test_create_run_five_scenarios_expected_status(client, name, expected):
    resp = client.post("/api/runs", json={"scenario_id": name})
    assert resp.status_code == 200
    body = resp.json()
    assert body["final_status"] == expected
    # 工具始终真实执行：全部四项必需检查完成
    assert body["required_checks_incomplete"] == 0
    executed = {e["tool"] for e in body["tool_events"]}
    assert {"check_required_fields", "compare_environment_config",
            "compare_api_acceptance", "inspect_acceptance_coverage"} <= executed


def test_create_run_missing_scenario_id_rejected(client):
    resp = client.post("/api/runs", json={"mode": "simulation"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_create_run_invalid_mode_rejected(client):
    resp = client.post(
        "/api/runs", json={"scenario_id": "scenario_ok", "mode": "magic"}
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_create_run_unknown_scenario_404(client):
    resp = client.post("/api/runs", json={"scenario_id": "not_a_scenario"})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "SCENARIO_NOT_FOUND"


def test_create_run_path_traversal_rejected(client):
    resp = client.post(
        "/api/runs", json={"scenario_id": "../../etc/passwd"}
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "SCENARIO_NOT_FOUND"


# ---------------------------------------------------------------- real 模式
def test_real_mode_config_missing_503(client, monkeypatch):
    for var in LLM_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    resp = client.post(
        "/api/runs", json={"scenario_id": "scenario_ok", "mode": "real"}
    )
    assert resp.status_code == 503
    body = resp.json()
    assert body["error"]["code"] == "REAL_MODEL_CONFIG_MISSING"
    # 错误信息只含缺失的环境变量名
    for var in LLM_ENV_VARS:
        assert var in body["error"]["message"]


def test_real_mode_config_missing_creates_no_run(client, monkeypatch):
    for var in LLM_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    client.post("/api/runs", json={"scenario_id": "scenario_ok", "mode": "real"})
    # 不得留下任何任务记录，更不得落一个模拟结果
    resp = client.get("/api/runs/1")
    assert resp.status_code == 404


def test_real_mode_factory_failure_503_no_fallback(tmp_path):
    def factory(mode):
        assert mode == "real"
        raise ModelClientError("真实客户端构造失败")

    app = create_app(db_path=str(tmp_path / "t.db"), client_factory=factory)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/api/runs", json={"scenario_id": "scenario_ok", "mode": "real"}
    )
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "REAL_MODEL_CONFIG_MISSING"
    # 未创建任何 run —— 没有把失败伪装成模拟成功
    assert client.get("/api/runs/1").status_code == 404


def test_real_mode_model_error_preserves_evidence(tmp_path):
    # decide 抛错 -> AgentRunner 收尾为 model_error，保留已有证据，绝不回退模拟
    app = create_app(
        db_path=str(tmp_path / "t.db"), client_factory=lambda mode: FailingClient()
    )
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/api/runs", json={"scenario_id": "scenario_ok", "mode": "real"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["model_mode"] == "real"          # 不是 simulation
    assert body["run_status"] == "model_error"
    assert body["final_status"] == "BLOCKED"     # 未完成必需检查 -> BLOCKED
    assert body["required_checks_incomplete"] == 4  # 不把未完成当通过
    # 失败任务已持久化，可查询
    detail = client.get(f"/api/runs/{body['run_id']}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "model_error"  # 执行状态（详情用 status）


def test_api_key_never_in_response(tmp_path, monkeypatch):
    secret = "sk-test-secret-do-not-leak-123"
    monkeypatch.setenv("DELIVERYGATE_LLM_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("DELIVERYGATE_LLM_API_KEY", secret)
    monkeypatch.setenv("DELIVERYGATE_LLM_MODEL", "test-model")

    # 真实客户端 + MockTransport（连接失败），无任何真实网络请求
    def handler(request):
        raise httpx.ConnectError("connection refused")

    http_client = httpx.Client(transport=httpx.MockTransport(handler))

    def factory(mode):
        return OpenAICompatibleClient(
            base_url="https://example.invalid/v1",
            api_key=secret,
            model="test-model",
            http_client=http_client,
        )

    app = create_app(db_path=str(tmp_path / "t.db"), client_factory=factory)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/api/runs", json={"scenario_id": "scenario_ok", "mode": "real"}
    )
    assert resp.status_code == 200
    assert resp.json()["run_status"] == "model_error"
    assert secret not in resp.text  # 密钥绝不出现在响应中


def test_internal_error_returns_generic_500(tmp_path):
    def factory(mode):
        raise RuntimeError("内部实现细节：/secret/path/key=abc")

    app = create_app(db_path=str(tmp_path / "t.db"), client_factory=factory)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/api/runs", json={"scenario_id": "scenario_ok"})
    assert resp.status_code == 500
    body = resp.json()
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert "Traceback" not in resp.text
    assert "secret" not in resp.text and "abc" not in resp.text



# ---------------------------------------------------------------- 任务详情
def test_get_run_detail(client):
    created = client.post("/api/runs", json={"scenario_id": "scenario_missing"}).json()
    resp = client.get(f"/api/runs/{created['run_id']}")
    assert resp.status_code == 200
    body = resp.json()
    # 执行状态与最终交付状态相互独立
    assert body["status"] == "succeeded"
    assert body["final_status"] == "BLOCKED"
    assert body["model_mode"] == "simulation"
    assert body["created_at"]
    assert isinstance(body["findings"], list) and body["findings"]
    assert isinstance(body["tool_events"], list) and body["tool_events"]


def test_get_run_404(client):
    resp = client.get("/api/runs/9999")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "RUN_NOT_FOUND"


def test_get_run_invalid_id_422(client):
    resp = client.get("/api/runs/not-an-int")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


# ---------------------------------------------------------------- 核验报告
def test_get_report_200(client):
    created = client.post("/api/runs", json={"scenario_id": "scenario_env_mismatch"}).json()
    resp = client.get(f"/api/runs/{created['run_id']}/report")
    assert resp.status_code == 200
    report = resp.json()
    # 报告与创建时返回的落库结果一致
    assert report == created


def test_report_trace_chain(client):
    created = client.post("/api/runs", json={"scenario_id": "scenario_missing"}).json()
    report = client.get(f"/api/runs/{created['run_id']}/report").json()

    event_ids = {e["tool_event_id"] for e in report["tool_events"]}
    tool_by_event = {e["tool_event_id"]: e["tool"] for e in report["tool_events"]}
    assert report["findings"]
    for f in report["findings"]:
        assert f["id"] and f["id"].startswith("F-")
        assert f["tool_event_id"] in event_ids          # finding -> TE-xxxx
        assert f["tool"] == tool_by_event[f["tool_event_id"]]  # TE -> 工具名
        assert f["evidence"]["source"]                  # TE -> evidence.source
    # 工具事件 ID 为任务内编号（TE-0001 起），不暴露数据库主键
    assert "TE-0001" in event_ids


def test_report_404(client):
    resp = client.get("/api/runs/9999/report")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "RUN_NOT_FOUND"


def test_report_not_ready_409(tmp_path):
    app = create_app(db_path=str(tmp_path / "t.db"))
    # 直接插入一条未完成任务（第一版同步执行通常不产生该状态，验证 409 契约）
    db.init_db(str(tmp_path / "t.db"))
    conn = db.get_connection(str(tmp_path / "t.db"))
    cur = conn.execute(
        "INSERT INTO runs (delivery_snapshot_json, status, model_mode, created_at)"
        " VALUES ('{}', 'running', 'simulation', '2026-01-01T00:00:00+00:00')"
    )
    run_id = cur.lastrowid
    conn.commit()
    conn.close()

    client = TestClient(app, raise_server_exceptions=False)
    assert client.get(f"/api/runs/{run_id}").status_code == 200  # 详情可读
    resp = client.get(f"/api/runs/{run_id}/report")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "REPORT_NOT_READY"


# ---------------------------------------------------------------- 持久化与隔离
def test_create_run_persists_to_db(client, app):
    created = client.post("/api/runs", json={"scenario_id": "scenario_missing"}).json()
    # 同一应用内 Repository 已将数据写入注入的临时库
    assert app.state.repo.db_path.endswith("api_test.db")
    run = app.state.repo.get_run(created["run_id"])
    assert run is not None
    assert run["final_status"] == "BLOCKED"
    assert run["status"] == "succeeded"
    # 重新连接数据库读取相同结果（跨连接一致）
    from app.repositories import RunRepository

    fresh = RunRepository(db_path=app.state.repo.db_path)
    again = fresh.get_full_run(created["run_id"])
    assert again["final_status"] == run["final_status"]
    assert [f["task_finding_id"] for f in again["findings"]] == [
        f["id"] for f in created["findings"]
    ]


def test_two_runs_have_distinct_task_ids(client):
    r1 = client.post("/api/runs", json={"scenario_id": "scenario_missing"}).json()
    r2 = client.post("/api/runs", json={"scenario_id": "scenario_missing"}).json()
    assert r1["run_id"] != r2["run_id"]
    # 任务内 ID 各自从 TE-0001 / F-0001 编号，互不串用
    assert {e["tool_event_id"] for e in r1["tool_events"]} == {
        e["tool_event_id"] for e in r2["tool_events"]
    }
    d1 = client.get(f"/api/runs/{r1['run_id']}").json()
    d2 = client.get(f"/api/runs/{r2['run_id']}").json()
    assert d1["run_id"] == r1["run_id"]
    assert d2["run_id"] == r2["run_id"]
    assert d1 == client.get(f"/api/runs/{r1['run_id']}").json()  # 读取稳定


def test_detail_and_report_do_not_reexecute(tmp_path):
    factory = CountingFactory()
    app = create_app(db_path=str(tmp_path / "t.db"), client_factory=factory)
    client = TestClient(app, raise_server_exceptions=False)

    created = client.post("/api/runs", json={"scenario_id": "scenario_ok"}).json()
    assert factory.calls == 1  # 创建执行时构造一次客户端

    client.get(f"/api/runs/{created['run_id']}")
    client.get(f"/api/runs/{created['run_id']}/report")
    client.get("/api/scenarios")
    client.get("/api/health")
    assert factory.calls == 1  # 只读查询不重新执行、不重新调用模型

