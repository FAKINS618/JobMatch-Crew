import json
import sqlite3

import pytest

from app import database
from app.schemas import (
    ActionEvidenceCreate,
    ActionItemUpdate,
    ActionItemsFromReportRequest,
    JobTargetCreate,
    JobTargetUpdate,
)


def _create_market_report() -> int:
    parsed_result = {
        "missing_market_skills": ["Docker", "Redis"],
        "job_recommendations": [
            {
                "title": "Python 后端开发实习",
                "company": "示例公司",
                "url": "https://example.com/jobs/1",
                "level": "A",
                "match_score": 88,
            }
        ],
    }
    return database.save_report(
        target_role="Python 后端开发实习",
        score=88,
        resume_text="Python FastAPI Docker Redis 项目经历。" * 10,
        jd_text="{}",
        markdown_report="# 报告",
        parsed_result=json.dumps(parsed_result),
        parse_status="success",
    )


def _insert_active_job_post(report_id: int) -> None:
    with sqlite3.connect(database.DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO job_posts (report_id, title, company, url, status)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                report_id,
                "Python 后端开发实习",
                "示例公司",
                "https://example.com/jobs/1",
                "active",
            ),
        )
        conn.commit()


def test_workflow_requires_evidence_and_tracks_real_actions(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "workflow.db")
    database.init_db()
    report_id = _create_market_report()
    _insert_active_job_post(report_id)

    target = database.create_job_target(
        JobTargetCreate(
            report_id=report_id,
            url="https://example.com/jobs/1",
            priority="A",
        )
    )
    assert target["status"] == "saved"

    applied_target = database.update_job_target(
        target["id"], JobTargetUpdate(status="applied", note="已通过官网投递")
    )
    assert applied_target["status"] == "applied"
    assert applied_target["applied_at"] is not None

    action = database.create_action_items_from_report(
        report_id, ActionItemsFromReportRequest(skills=["Docker"])
    )[0]
    with pytest.raises(ValueError, match="成果证据"):
        database.update_action_item(action["id"], ActionItemUpdate(status="completed"))

    evidence = database.create_action_evidence(
        action["id"],
        ActionEvidenceCreate(
            evidence_type="link",
            url="https://github.com/example/docker-demo",
            content="Docker 化部署练习",
        ),
    )
    assert evidence is not None
    completed_action = database.update_action_item(
        action["id"], ActionItemUpdate(status="completed")
    )
    assert completed_action["status"] == "completed"
    assert completed_action["evidence_count"] == 1

    summary = database.get_dashboard_summary()
    assert summary["applied_job_count"] == 1
    assert summary["completed_action_count"] == 1
    assert summary["evidence_count"] == 1


def test_job_target_rejects_invalid_status_transition(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "workflow.db")
    database.init_db()
    report_id = _create_market_report()
    _insert_active_job_post(report_id)
    target = database.create_job_target(
        JobTargetCreate(
            report_id=report_id,
            url="https://example.com/jobs/1",
            priority="A",
        )
    )

    with pytest.raises(ValueError, match="不能将岗位状态"):
        database.update_job_target(target["id"], JobTargetUpdate(status="interview"))
