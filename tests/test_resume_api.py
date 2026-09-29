from fastapi.testclient import TestClient

from app.main import app
from app.services.resume_parser_service import parse_resume_to_profile


client = TestClient(app)


def test_resume_versions_endpoint_returns_a_list(monkeypatch):
    monkeypatch.setattr("app.api.resumes.list_resume_versions", lambda: [])

    response = client.get("/api/resumes/versions")

    assert response.status_code == 200
    assert response.json() == []


def test_resume_parse_rejects_short_input_without_calling_llm():
    response = client.post("/api/resumes/parse", json={"raw_text": "过短"})

    assert response.status_code == 422


def test_resume_parser_normalizes_common_project_shapes(monkeypatch):
    monkeypatch.setattr(
        "app.services.resume_parser_service.call_resume_parser_llm",
        lambda _raw_text: '''{
            "skills": "Python",
            "projects": ["求职分析平台", {"title": "RAG 问答", "technologies": "FastAPI"}]
        }''',
    )

    profile = parse_resume_to_profile("Python 项目经历。" * 20)

    assert profile.skills == ["Python"]
    assert [item.name for item in profile.projects] == ["求职分析平台", "RAG 问答"]
    assert profile.projects[1].technologies == ["FastAPI"]


def test_resume_version_returns_not_found_for_unknown_resume(monkeypatch):
    def raise_not_found(_payload):
        raise ValueError("指定的简历不存在")

    monkeypatch.setattr("app.api.resumes.create_resume_version", raise_not_found)
    payload = {
        "resume_id": 999,
        "version_name": "后端实习版",
        "target_role": "Python 后端开发实习",
        "raw_text": "Python FastAPI MySQL Docker 项目经历。" * 5,
        "profile": {"skills": ["Python", "FastAPI"]},
    }

    response = client.post("/api/resumes/versions", json=payload)

    assert response.status_code == 404


def test_resume_version_diff_returns_changed_sections(monkeypatch):
    monkeypatch.setattr(
        "app.api.resumes.get_resume_version_diff",
        lambda _from_id, _to_id: {
            "from_version_id": 1,
            "to_version_id": 2,
            "sections": [{"section": "skills", "before": ["Python"], "after": ["Python", "Redis"]}],
            "raw_text_changed": True,
        },
    )

    response = client.get("/api/resumes/versions/1/diff/2")

    assert response.status_code == 200
    assert response.json()["sections"][0]["section"] == "skills"
    assert response.json()["raw_text_changed"] is True
