from fastapi.testclient import TestClient
from pathlib import Path
from uuid import uuid4
import pytest

from app.main import app
from app import database


# 测基础 API：不调用大模型、不联网，只验证 FastAPI 应用能正常加载。
client = TestClient(app)


def test_health_api():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_roles_api():
    response = client.get("/api/roles")

    assert response.status_code == 200
    roles = response.json()["roles"]
    assert "AI 应用开发实习" in roles
    assert "Java 后端开发实习" in roles
    assert "前端开发实习" in roles


def test_reports_api():
    response = client.get("/api/reports")

    assert response.status_code == 200
    assert "reports" in response.json()
    if response.json()["reports"]:
        assert "job_post_count" in response.json()["reports"][0]
        assert "created_at_local" in response.json()["reports"][0]


def test_dashboard_api_initializes_workflow_tables():
    """应用启动后，闭环总览接口应可在空数据状态下正常返回。"""
    with TestClient(app) as lifespan_client:
        response = lifespan_client.get("/api/dashboard/summary")

    assert response.status_code == 200
    assert response.json()["saved_job_count"] >= 0


def test_copilot_session_api():
    with TestClient(app) as lifespan_client:
        response = lifespan_client.post(
            "/api/v1/copilot/sessions", json={"target_role": "Python 后端开发实习"}
        )

    assert response.status_code == 201
    assert response.json()["target_role"] == "Python 后端开发实习"


def test_data_export_api_returns_portable_backup():
    with TestClient(app) as lifespan_client:
        response = lifespan_client.get("/api/data/export")

    assert response.status_code == 200
    assert response.json()["format"] == "cs-jobmate-json-backup"
    assert "reports" in response.json()["tables"]


def test_analysis_task_retry_persists_payload_and_limits_attempts(monkeypatch):
    db_path = Path("tmp") / f"task-{uuid4().hex}.db"
    monkeypatch.setattr(database.settings, "database_path", db_path)
    monkeypatch.setattr(database, "DB_PATH", db_path)
    database.init_db()
    task_id = database.create_analysis_task("market_match", {"request": {"target_role": "Python"}})
    database.update_analysis_task(task_id, status="failed", progress=100)
    assert database.retry_analysis_task(task_id)["attempt_count"] == 1
    assert database.get_analysis_task_payload(task_id)["request"]["target_role"] == "Python"
    for _ in range(2):
        database.update_analysis_task(task_id, status="failed", progress=100)
        database.retry_analysis_task(task_id)
    database.update_analysis_task(task_id, status="failed", progress=100)
    with pytest.raises(ValueError, match="最大重试"):
        database.retry_analysis_task(task_id)
