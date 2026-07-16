from app import database
from app.schemas import (
    ActionItemsFromReportRequest,
    JobMatchAnalysis,
    ResumeProfile,
    ResumeVersionCreate,
)
from app.services.copilot_service import (
    _build_rule_based_analysis,
    _evidence_payload,
    run_copilot_turn,
)
from app.report_renderer import render_markdown_report


def test_copilot_turn_requests_resume_before_running_llm(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "copilot.db")
    database.init_db()
    session = database.create_copilot_session(None, "Python 后端开发实习")
    created = database.create_copilot_message_and_turn(
        session["id"], "Python 后端开发实习，负责 API 开发与数据处理。" * 10
    )
    assert created is not None
    _, turn = created

    run_copilot_turn(turn["id"])

    completed = database.get_copilot_turn(turn["id"])
    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["artifacts"][0]["artifact_type"] == "job_brief"
    assert "简历版本" in completed["artifacts"][0]["payload"]["title"]
    session_detail = database.get_copilot_session(session["id"])
    assert session_detail is not None
    assert session_detail["messages"][-1]["role"] == "assistant"


def test_rule_analysis_separates_keyword_and_semantic_evidence():
    analysis = _build_rule_based_analysis(
        "使用 Python 开发后端服务，完成缓存设计和容器化部署。",
        "岗位要求 Python、Redis 和 Docker，负责后端接口开发。",
    )

    matches = {item.requirement: item for item in analysis.requirement_matches}
    assert matches["Python"].status == "supported"
    assert matches["Python"].keyword_evidence
    assert matches["Redis"].status == "partial"
    assert matches["Redis"].semantic_evidence
    assert matches["Docker"].status == "partial"
    assert matches["Docker"].semantic_evidence
    assert "Redis" not in analysis.matched_skills
    payload = _evidence_payload(analysis)
    redis_item = next(item for item in payload["items"] if item["requirement"] == "Redis")
    assert redis_item["semantic_evidence"]
    assert redis_item["keyword_evidence"] == []
    report = render_markdown_report(analysis)
    assert "逐条岗位要求证据" in report
    assert "语义证据" in report


def test_copilot_returns_rule_based_evidence_when_agent_is_unavailable(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "copilot.db")
    monkeypatch.setattr(
        "app.services.copilot_service._run_fast_agent_analysis",
        lambda *_args: None,
    )
    database.init_db()
    resume = database.create_resume_version(
        ResumeVersionCreate(
            version_name="后端实习版",
            target_role="Python 后端开发实习",
            raw_text="Python FastAPI MySQL 项目经历。" * 20,
            profile=ResumeProfile(skills=["Python", "FastAPI", "MySQL"]),
        )
    )
    session = database.create_copilot_session(resume["id"], "Python 后端开发实习")
    created = database.create_copilot_message_and_turn(
        session["id"], "岗位需要 Python、FastAPI、MySQL、Redis、Docker，负责后端接口开发。" * 10
    )
    assert created is not None
    _, turn = created

    run_copilot_turn(turn["id"])

    completed = database.get_copilot_turn(turn["id"])
    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["stage"] == "rule_based_ready"
    assert completed["report_id"] is not None
    assert {item["artifact_type"] for item in completed["artifacts"]} == {
        "job_brief",
        "evidence_map",
        "fit_strategy",
        "action_bundle",
    }
    action_items = database.create_action_items_from_report(
        completed["report_id"], ActionItemsFromReportRequest(skills=["Redis", "Docker"])
    )
    assert [item["skill"] for item in action_items] == ["Redis", "Docker"]


def test_copilot_multi_agent_result_updates_the_same_report(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "copilot_multi_agent.db")
    analysis = JobMatchAnalysis(
        score=82,
        summary="候选人的后端项目证据覆盖了岗位的大部分核心要求。",
        matched_skills=["Python", "FastAPI"],
    )
    monkeypatch.setattr(
        "app.services.copilot_service._run_fast_agent_analysis",
        lambda *_args: analysis,
    )
    database.init_db()
    resume = database.create_resume_version(
        ResumeVersionCreate(
            version_name="多 Agent 测试版",
            target_role="Python 后端开发实习",
            raw_text="Python FastAPI MySQL 项目经历。" * 20,
            profile=ResumeProfile(skills=["Python", "FastAPI"]),
        )
    )
    session = database.create_copilot_session(resume["id"], "Python 后端开发实习")
    created = database.create_copilot_message_and_turn(
        session["id"], "岗位需要 Python、FastAPI 和 MySQL，负责后端接口开发与维护。" * 10
    )
    assert created is not None
    _, turn = created

    run_copilot_turn(turn["id"])

    completed = database.get_copilot_turn(turn["id"])
    assert completed is not None
    assert completed["stage"] == "completed"
    assert completed["report_id"] is not None
    report = database.get_report(completed["report_id"])
    assert report is not None
    assert report["parse_status"] == "copilot_multi_agent"
    assert report["score"] == 82


def test_copilot_follow_up_reuses_active_report_context(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "copilot_follow_up.db")
    monkeypatch.setattr(
        "app.services.copilot_service._run_fast_agent_analysis",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        "app.services.copilot_service._run_follow_up_response",
        lambda _resume_text, report, question: (
            f"已基于 {report['target_role']} 的分析回答：{question}"
        ),
    )
    database.init_db()
    resume = database.create_resume_version(
        ResumeVersionCreate(
            version_name="追问测试版",
            target_role="Python 后端开发实习",
            raw_text="Python FastAPI MySQL 项目经历。" * 20,
            profile=ResumeProfile(skills=["Python", "FastAPI", "MySQL"]),
        )
    )
    session = database.create_copilot_session(resume["id"], "Python 后端开发实习")
    initial = database.create_copilot_message_and_turn(
        session["id"],
        "岗位需要 Python、FastAPI、MySQL、Redis、Docker，负责后端接口开发与维护。" * 10,
    )
    assert initial is not None
    _, initial_turn = initial
    run_copilot_turn(initial_turn["id"])

    completed_initial = database.get_copilot_turn(initial_turn["id"])
    assert completed_initial is not None
    assert completed_initial["stage"] == "rule_based_ready"
    assert completed_initial["report_id"] is not None

    follow_up = database.create_copilot_message_and_turn(
        session["id"], "你还有什么建议"
    )
    assert follow_up is not None
    _, follow_up_turn = follow_up
    assert follow_up_turn["input_type"] == "follow_up"
    assert follow_up_turn["parent_turn_id"] == initial_turn["id"]
    assert follow_up_turn["report_id"] == completed_initial["report_id"]

    run_copilot_turn(follow_up_turn["id"])

    completed_follow_up = database.get_copilot_turn(follow_up_turn["id"])
    assert completed_follow_up is not None
    assert completed_follow_up["status"] == "completed"
    assert completed_follow_up["stage"] == "follow_up_ready"
    assert completed_follow_up["report_id"] == completed_initial["report_id"]
    detail = database.get_copilot_session(session["id"])
    assert detail is not None
    assert detail["active_report_id"] == completed_initial["report_id"]
    assert (
        detail["messages"][-1]["content"]
        == "已基于 Python 后端开发实习 的分析回答：你还有什么建议"
    )
