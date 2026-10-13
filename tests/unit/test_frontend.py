"""前端静态资源挂载与首页测试（阶段 4.3）。

覆盖：首页可访问、CSS/JS 静态资源可访问、不与 /api 冲突、
访问首页不创建模型客户端/不建数据库、前端不含硬编码场景与核验规则、
无 innerHTML 动态注入、JS 语法检查（本机有 node 时执行）。
不发起任何真实模型请求，不污染默认数据库。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.agent.llm_client import SimulationClient
from app.main import STATIC_DIR, create_app


class CountingFactory:
    """统计客户端构造次数：访问首页与静态资源不得构造客户端。"""

    def __init__(self):
        self.calls = 0
        self.inner = SimulationClient()

    def __call__(self, mode):
        self.calls += 1
        return self.inner


@pytest.fixture
def app(tmp_path):
    return create_app(db_path=str(tmp_path / "static_test.db"), client_factory=CountingFactory())


@pytest.fixture
def client(app):
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------- 首页与静态资源
def test_index_returns_html(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    body = resp.text
    assert "DeliveryGate AI" in body
    assert "企业软件交付前置核验" in body
    assert "/static/style.css" in body
    assert "/static/app.js" in body


def test_static_css_served(client):
    resp = client.get("/static/style.css")
    assert resp.status_code == 200
    assert "text/css" in resp.headers["content-type"]
    assert "badge-ready" in resp.text


def test_static_js_served(client):
    resp = client.get("/static/app.js")
    assert resp.status_code == 200
    ctype = resp.headers["content-type"]
    assert "javascript" in ctype or "text/plain" in ctype
    assert "api(" in resp.text


def test_unknown_static_path_is_404(client):
    resp = client.get("/static/no_such_file.js")
    assert resp.status_code == 404


# ---------------------------------------------------------------- 惰性初始化与隔离
def test_index_does_not_construct_model_client(app, client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert app.state.client_factory.calls == 0


def test_index_does_not_create_repository(app, client):
    client.get("/")
    client.get("/static/style.css")
    client.get("/static/app.js")
    assert app.state.repo is None  # 数据库尚未惰性创建


def test_api_routes_unaffected_by_static_mount(client):
    assert client.get("/api/health").status_code == 200
    resp = client.get("/api/scenarios")
    assert resp.status_code == 200
    assert len(resp.json()["scenarios"]) == 5


# ---------------------------------------------------------------- 前端内容安全与约束
def test_frontend_has_no_hardcoded_scenarios():
    """场景列表必须来自 API，不得在前端硬编码场景 id 作为数据来源。"""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    for source in (html, js):
        assert "scenario_ok" not in source
        assert "scenario_missing" not in source
        assert "scenario_env_mismatch" not in source
        assert "scenario_api_conflict" not in source
        assert "scenario_coverage_gap" not in source


def test_frontend_has_no_innerhtml_dynamic_injection():
    """动态内容必须走 textContent/createElement，不使用未转义的 HTML 注入。"""
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert ".innerHTML" not in js
    assert "insertAdjacentHTML" not in js
    assert "document.write" not in js


def test_frontend_has_no_api_key_input():
    """不允许在浏览器输入或存储 API Key。"""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert 'type="password"' not in html
    assert "api_key" not in js.lower()
    assert "apikey" not in js.lower()


def test_frontend_has_no_cdn_or_external_resources():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert "http://" not in html
    assert "https://" not in html


def test_frontend_does_not_recompute_final_status():
    """前端只展示后端 final_status，不实现判定规则。"""
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "required_checks_incomplete" in js  # 仅读取展示
    assert "compute_final_status" not in js
    assert "blockers > 0" not in js


# ---------------------------------------------------------------- JS 语法
@pytest.mark.skipif(shutil.which("node") is None, reason="本机没有 node，无法执行语法检查")
def test_app_js_syntax_valid():
    result = subprocess.run(
        ["node", "--check", str(STATIC_DIR / "app.js")],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
