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
