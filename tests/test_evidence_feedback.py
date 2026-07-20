import json

import pytest
from fastapi.testclient import TestClient

from app import database
from app.agent_pipeline.orchestrator import run_analysis_pipeline
from app.main import app
from app.schemas.agent_pipeline import EvidenceFeedbackCreate
from evals.export_reviewed_feedback import export_reviewed_feedback


def test_feedback_contract_validates_verdict_and_status():
    assert EvidenceFeedbackCreate(verdict="confirmed")
    assert EvidenceFeedbackCreate(verdict="rejected")
    assert EvidenceFeedbackCreate(
        verdict="corrected",
        corrected_status="partial",
        evidence_ids=["evidence-1"],
    )
    with pytest.raises(ValueError, match="corrected_status"):
        EvidenceFeedbackCreate(
            verdict="corrected", evidence_ids=[]
        )
    with pytest.raises(ValueError, match="不应指定"):
        EvidenceFeedbackCreate(
            verdict="confirmed", corrected_status="partial"
        )
    with pytest.raises(ValueError, match="不允许"):
        EvidenceFeedbackCreate(
            verdict="corrected",
            corrected_status="missing_evidence",
            evidence_ids=["evidence-1"],
        )


def _prepared_turn(tmp_path):
    database.DB_PATH = tmp_path / "feedback.db"
    database.init_db()
    session = database.create_copilot_session(None, "Python 后端开发实习")
    created = database.create_copilot_message_and_turn(
        session["id"], "岗位要求 Python、FastAPI，负责接口开发和维护。" * 4
    )
    assert created is not None
    _, turn = created
    run_analysis_pipeline(
        turn_id=turn["id"],
        resume_text="项目经历：使用 Python 和 FastAPI 开发后端接口。" * 3,
        jd_text="岗位要求 Python、FastAPI，负责接口开发和维护。" * 4,
        target_role="Python 后端开发实习",
        use_llm=False,
    )
    chain = database.get_analysis_evidence_chain(turn["id"])
    assert chain and chain["items"]
    return turn, chain


def test_feedback_api_rejects_cross_requirement_evidence(tmp_path):
    turn, chain = _prepared_turn(tmp_path)
    items = chain["items"]
    first = items[0]
    second = next(item for item in items[1:] if item["candidates"])
    response = TestClient(app).post(
        f"/api/v1/copilot/turns/{turn['id']}/evidence-feedback",
        json={
            "requirement_id": first["requirement"]["id"],
            "verdict": "corrected",
            "corrected_status": "supported",
            "evidence_ids": [second["candidates"][0]["id"]],
        },
    )
    assert response.status_code == 422


def test_latest_review_is_returned_without_raw_fields(tmp_path):
    turn, chain = _prepared_turn(tmp_path)
    item = next(item for item in chain["items"] if item["candidates"])
    endpoint = f"/api/v1/copilot/turns/{turn['id']}/evidence-feedback"
    client = TestClient(app)
    first = client.post(
        endpoint,
        json={
            "requirement_id": item["requirement"]["id"],
            "verdict": "rejected",
            "evidence_ids": [],
            "note": "候选片段需要人工核对",
        },
    )
    assert first.status_code == 201
    second = client.post(
        endpoint,
        json={
            "requirement_id": item["requirement"]["id"],
            "verdict": "corrected",
            "corrected_status": "partial",
            "evidence_ids": [item["candidates"][0]["id"]],
            "note": "只有相关项目描述",
        },
    )
    assert second.status_code == 201

    response = client.get(f"/api/v1/copilot/turns/{turn['id']}/evidence")
    assert response.status_code == 200
    payload = response.json()
    reviewed = next(
        value for value in payload["items"]
        if value["requirement"]["id"] == item["requirement"]["id"]
    )
    assert reviewed["review"]["verdict"] == "corrected"
    assert reviewed["review"]["note"] == "只有相关项目描述"
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "raw_output" not in serialized
    assert "resume_text" not in serialized
    assert "jd_text" not in serialized


def test_feedback_returns_409_before_evidence_chain_exists(tmp_path):
    database.DB_PATH = tmp_path / "pending-feedback.db"
    database.init_db()
    session = database.create_copilot_session(None, "Python")
    created = database.create_copilot_message_and_turn(session["id"], "岗位要求 Python" * 20)
    assert created is not None
    response = TestClient(app).post(
        f"/api/v1/copilot/turns/{created[1]['id']}/evidence-feedback",
        json={"requirement_id": "req-1", "verdict": "confirmed"},
    )
    assert response.status_code == 409


def test_export_reviewed_feedback_is_sanitized(tmp_path):
    turn, chain = _prepared_turn(tmp_path)
    item = next(item for item in chain["items"] if item["candidates"])
    database.create_evidence_feedback(
        turn_id=turn["id"],
        analysis_run_id=chain["analysis_run_id"],
        requirement_id=item["requirement"]["id"],
        verdict="rejected",
        corrected_status=None,
        evidence_ids=[item["candidates"][0]["id"]],
        note="需要人工重新标注",
    )
    output = tmp_path / "reviewed.json"
    exported = export_reviewed_feedback(database_path=database.DB_PATH, output_path=output)
    assert len(exported) == 1
    serialized = output.read_text(encoding="utf-8")
    assert "使用 Python 和 FastAPI 开发后端接口" not in serialized
    assert "岗位要求 Python、FastAPI" not in serialized
    assert "needs_manual_label" in serialized
