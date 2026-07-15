from fastapi.testclient import TestClient

from app.main import app


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
