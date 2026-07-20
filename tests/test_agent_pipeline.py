import pytest
from types import SimpleNamespace

from app import database
from app.agent_pipeline.evidence_judge import validate_decisions
from app.agent_pipeline.jd_extractor import validate_requirement_quotes
from app.agent_pipeline.orchestrator import run_analysis_pipeline
from app.agent_pipeline.report_writer import write_report
from app.agent_pipeline.retrieval import RuleEvidenceReranker
from app.agent_pipeline.scorer import score_requirements
from app.agent_pipeline.structured_runner import StageOutcome, run_structured
from app.config import settings
from app.schemas.agent_pipeline import (
    EvidenceCandidate,
    EvidenceDecision,
    ReportNarrative,
    JDRequirement,
    ResumeChunk,
    ResumeChunkCandidate,
)
from app.agent_pipeline.evidence_judge import EvidenceDecisionBundle
from app.agent_pipeline.jd_extractor import JDRequirementBundle
from evals.models import EvaluationCase, EvaluationFixture, ExpectedEvidence, ExpectedRequirement
from evals.run_evals import _case_metrics, load_fixtures


def _requirement() -> JDRequirement:
    return JDRequirement(
        id="req-1",
        text="Python",
        skill="Python",
        category="must",
        weight=2,
        source_quote="要求 Python",
    )


def test_jd_source_quote_must_be_in_original_text():
    with pytest.raises(ValueError, match="source_quote"):
        validate_requirement_quotes([_requirement()], "岗位要求 Java")


def test_supported_requires_real_evidence_id():
    with pytest.raises(ValueError, match="evidence_id"):
        EvidenceDecision(
            requirement_id="req-1",
            status="supported",
            evidence_ids=[],
            confidence=0.9,
            rationale="看起来匹配",
        )


def test_decision_rejects_unknown_evidence_id():
    requirement = _requirement()
    candidate = EvidenceCandidate(
        id="evidence-1",
        requirement_id="req-1",
        chunk_id="resume-1",
        snippet="项目中使用 Python",
        lexical_score=0.8,
    )
    decision = EvidenceDecision(
        requirement_id="req-1",
        status="supported",
        evidence_ids=["does-not-exist"],
        confidence=0.9,
        rationale="有证据",
    )
    with pytest.raises(ValueError, match="不存在"):
        validate_decisions([requirement], {"req-1": [candidate]}, [decision])


def test_partial_requires_evidence_id():
    with pytest.raises(ValueError, match="partial"):
        EvidenceDecision(
            requirement_id="req-1",
            status="partial",
            evidence_ids=[],
            confidence=0.5,
            rationale="只有部分相关信息",
        )


def test_decisions_reject_duplicate_requirement_ids():
    requirement = _requirement()
    candidate = EvidenceCandidate(
        id="evidence-1",
        requirement_id="req-1",
        chunk_id="resume-1",
        snippet="项目中使用 Python",
        lexical_score=0.8,
    )
    decision = EvidenceDecision(
        requirement_id="req-1",
        status="supported",
        evidence_ids=[candidate.id],
        confidence=0.9,
        rationale="有证据",
    )
    with pytest.raises(ValueError, match="重复"):
        validate_decisions([requirement], {"req-1": [candidate]}, [decision, decision])


def test_decisions_reject_cross_requirement_evidence():
    first = _requirement()
    second = first.model_copy(update={"id": "req-2", "skill": "FastAPI"})
    first_candidate = EvidenceCandidate(
        id="evidence-1",
        requirement_id="req-1",
        chunk_id="resume-1",
        snippet="项目中使用 Python",
        lexical_score=0.8,
    )
    second_candidate = EvidenceCandidate(
        id="evidence-2",
        requirement_id="req-2",
        chunk_id="resume-2",
        snippet="项目中使用 FastAPI",
        lexical_score=0.8,
    )
    decisions = [
        EvidenceDecision(
            requirement_id="req-1",
            status="supported",
            evidence_ids=[second_candidate.id],
            confidence=0.9,
            rationale="错误跨 requirement 引用",
        ),
        EvidenceDecision(
            requirement_id="req-2",
            status="supported",
            evidence_ids=[second_candidate.id],
            confidence=0.9,
            rationale="有证据",
        ),
    ]
    with pytest.raises(ValueError, match="其他岗位要求"):
        validate_decisions(
            [first, second],
            {"req-1": [first_candidate], "req-2": [second_candidate]},
            decisions,
        )


def test_score_is_deterministic_and_model_cannot_choose_total():
    requirement = _requirement()
    decision = EvidenceDecision(
        requirement_id="req-1",
        status="supported",
        evidence_ids=["evidence-1"],
        confidence=0.9,
        rationale="项目中有 Python",
    )
    result = score_requirements([requirement], [decision], "项目经历：Python 后端服务")
    assert result.score == 85
    assert result.must_coverage == 1
    assert result.project_relevance == 1


def test_rule_reranker_prioritizes_exact_skill_over_expansion_match():
    requirement = JDRequirement(
        id="req-redis",
        text="Redis 缓存能力",
        skill="Redis",
        category="must",
        source_quote="要求 Redis 缓存能力",
    )
    candidates = [
        ResumeChunkCandidate(
            chunk=ResumeChunk(id="resume-2", section="项目经历", content="完成缓存设计"),
            lexical_score=0.40,
        ),
        ResumeChunkCandidate(
            chunk=ResumeChunk(id="resume-1", section="专业技能", content="使用 Redis"),
            lexical_score=0.30,
        ),
    ]
    ranked = RuleEvidenceReranker().rerank(requirement, candidates, top_k=2)
    assert [item.chunk.id for item in ranked] == ["resume-1", "resume-2"]
    assert ranked[0].rerank_score > ranked[1].rerank_score


def test_rule_reranker_has_stable_chunk_id_tiebreaker():
    requirement = _requirement()
    candidates = [
        ResumeChunkCandidate(
            chunk=ResumeChunk(id="resume-b", section="其他", content="普通描述"),
            lexical_score=0.2,
        ),
        ResumeChunkCandidate(
            chunk=ResumeChunk(id="resume-a", section="其他", content="普通描述"),
            lexical_score=0.2,
        ),
    ]
    ranked = RuleEvidenceReranker().rerank(requirement, candidates, top_k=2)
    assert [item.chunk.id for item in ranked] == ["resume-a", "resume-b"]


def test_report_writer_ignores_factual_fields_from_llm(monkeypatch):
    requirement = _requirement()
    candidate = EvidenceCandidate(
        id="evidence-1",
        requirement_id="req-1",
        chunk_id="resume-1",
        snippet="项目中使用 Python",
        lexical_score=0.8,
    )
    decision = EvidenceDecision(
        requirement_id="req-1",
        status="supported",
        evidence_ids=[candidate.id],
        confidence=0.9,
        rationale="有证据",
    )
    score = score_requirements([requirement], [decision], "项目经历：Python 后端服务")
    monkeypatch.setattr(
        settings,
        "deepseek_api_key",
        "test-key",
    )
    responses = iter(
        [
            (
                '{"summary":"模型给出的叙事摘要足够长且不改变事实字段。",'
                '"resume_bullets":["请基于真实项目补充量化结果"],'
                '"matched_skills":["Docker"]}'
            ),
            (
                '{"summary":"修复后的叙事摘要足够长且不改变事实字段。",'
                '"resume_bullets":["请基于真实项目补充量化结果"]}'
            ),
        ]
    )
    monkeypatch.setattr(
        "app.agent_pipeline.structured_runner._call_agent",
        lambda *_args: next(responses),
    )

    report, degraded, outcome = write_report(
        [requirement],
        {"req-1": [candidate]},
        [decision],
        score,
        use_llm=True,
    )

    assert degraded is False
    assert outcome.retry_count == 1
    assert report.score == score.score
    assert report.matched_skills == ["Python"]
    assert report.missing_skills == []
    assert report.requirement_matches[0].status == "supported"
    assert report.resume_bullets == ["请基于真实项目补充量化结果"]


def test_structured_runner_repairs_once_and_returns_outcome(monkeypatch):
    monkeypatch.setattr(settings, "deepseek_api_key", "test-key")
    responses = iter(
        [
            '{"requirements": "invalid"}',
            '{"requirements": []}',
        ]
    )
    monkeypatch.setattr(
        "app.agent_pipeline.structured_runner._call_agent",
        lambda *_args: next(responses),
    )

    outcome = run_structured(
        prompt="test",
        output_model=JDRequirementBundle,
        expected_output="requirements JSON",
        enabled=True,
    )

    assert outcome.value is not None
    assert outcome.retry_count == 1
    assert outcome.validation_error == ""
    assert outcome.degraded is False


def test_structured_runner_summarizes_failed_repair(monkeypatch):
    monkeypatch.setattr(settings, "deepseek_api_key", "test-key")
    monkeypatch.setattr(
        "app.agent_pipeline.structured_runner._call_agent",
        lambda *_args: '{"requirements": "invalid"}',
    )

    outcome = run_structured(
        prompt="candidate resume secret",
        output_model=JDRequirementBundle,
        expected_output="requirements JSON",
        enabled=True,
    )

    assert outcome.value is None
    assert outcome.retry_count == 1
    assert outcome.degraded is True
    assert "candidate resume secret" not in outcome.validation_error
    assert len(outcome.validation_error) < 500


def test_pipeline_success_path_uses_mocked_structured_stages(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "pipeline-success.db")
    monkeypatch.setattr(settings, "deepseek_api_key", "test-key")
    database.init_db()
    session = database.create_copilot_session(None, "Python 后端开发实习")
    created = database.create_copilot_message_and_turn(
        session["id"], "岗位要求 Python，负责后端项目开发和接口维护。" * 4
    )
    assert created is not None
    _, turn = created
    requirement = JDRequirement(
        id="req-1",
        text="Python",
        skill="Python",
        category="must",
        weight=2,
        source_quote="岗位要求 Python",
    )
    candidate = EvidenceCandidate(
        id="evidence-req-1-1",
        requirement_id="req-1",
        chunk_id="resume-1",
        snippet="[项目经历] 使用 Python 开发后端服务",
        lexical_score=0.9,
        rerank_score=0.9,
    )
    decision = EvidenceDecision(
        requirement_id="req-1",
        status="supported",
        evidence_ids=[candidate.id],
        confidence=0.9,
        rationale="有直接证据",
    )
    monkeypatch.setattr(
        "app.agent_pipeline.jd_extractor.run_structured",
        lambda **_: StageOutcome(value=JDRequirementBundle(requirements=[requirement])),
    )
    monkeypatch.setattr(
        "app.agent_pipeline.evidence_judge.run_structured",
        lambda **_: StageOutcome(value=EvidenceDecisionBundle(decisions=[decision])),
    )
    monkeypatch.setattr(
        "app.agent_pipeline.report_writer.run_structured",
        lambda **_: StageOutcome(
            value=ReportNarrative(summary="成功路径的报告叙事结果已经通过结构化校验。")
        ),
    )

    result = run_analysis_pipeline(
        turn_id=turn["id"],
        resume_text="项目经历：使用 Python 开发后端服务。" * 3,
        jd_text="岗位要求 Python，负责后端项目开发和接口维护。" * 4,
        target_role="Python 后端开发实习",
        use_llm=True,
    )

    assert result.degraded is False
    assert result.analysis.matched_skills == ["Python"]
    runs = database.list_analysis_runs(turn["id"])
    stages = database.list_agent_stage_runs(runs[0]["id"])
    assert runs[0]["status"] == "completed"
    assert all(item["status"] == "validated" for item in stages)
    with database.sqlite3.connect(database.DB_PATH) as conn:
        row = conn.execute(
            "SELECT chunks_json, candidates_json, decision_json FROM requirement_evidence WHERE analysis_run_id = ?",
            (runs[0]["id"],),
        ).fetchone()
    assert '"id": "resume-1"' in row[0]
    assert '"chunk_id": "resume-1"' in row[1]
    assert '"evidence_ids": ["evidence-req-1-1"]' in row[2]


def test_pipeline_falls_back_when_llm_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "pipeline.db")
    monkeypatch.setattr(settings, "deepseek_api_key", None)
    database.init_db()
    session = database.create_copilot_session(None, "Python 后端开发实习")
    created = database.create_copilot_message_and_turn(
        session["id"], "岗位要求 Python、FastAPI，负责后端接口开发和项目维护。" * 4
    )
    assert created is not None
    _, turn = created

    result = run_analysis_pipeline(
        turn_id=turn["id"],
        resume_text="项目经历：使用 Python 和 FastAPI 开发后端服务，完成接口设计。" * 3,
        jd_text="岗位要求 Python、FastAPI，负责后端接口开发和项目维护。" * 4,
        target_role="Python 后端开发实习",
        use_llm=True,
    )

    assert result.degraded is True
    assert result.analysis.score >= 0
    runs = database.list_analysis_runs(turn["id"])
    assert runs and runs[0]["status"] == "degraded"
    stages = database.list_agent_stage_runs(runs[0]["id"])
    assert {item["stage"] for item in stages} >= {
        "queued",
        "jd_extracted",
        "evidence_retrieved",
        "evidence_judged",
        "scored",
        "report_generated",
    }
    with database.sqlite3.connect(database.DB_PATH) as conn:
        evidence_count = conn.execute(
            "SELECT COUNT(*) FROM requirement_evidence WHERE analysis_run_id = ?",
            (runs[0]["id"],),
        ).fetchone()[0]
    assert evidence_count == len(result.requirements)


def test_offline_fixtures_validate_with_pydantic():
    fixtures = load_fixtures()
    assert len(fixtures) == 5
    assert {fixture.case.id for fixture in fixtures} == {
        "python-backend-01",
        "java-backend-01",
        "frontend-01",
        "rag-llm-01",
        "synonym-gap-01",
    }


def test_offline_metrics_return_zero_recall_for_empty_candidates():
    fixture = EvaluationFixture(
        case=EvaluationCase(
            id="empty-candidate",
            target_role="Python 后端开发实习",
            resume_text="项目经历：完成后端练习和接口文档整理，记录了学习过程和测试结果。" * 3,
            jd_text="岗位要求 Python 后端开发能力，负责接口实现、服务维护、问题排查和基础测试。" * 2,
        ),
        expected_requirements=[ExpectedRequirement(skill="Python", category="must")],
        expected_evidence=[
            ExpectedEvidence(
                requirement_skill="Python",
                expected_chunk_keywords=["Python"],
                expected_status="supported",
            )
        ],
    )
    requirement = JDRequirement(
        id="req-1",
        text="Python",
        skill="Python",
        category="must",
        source_quote="岗位要求 Python",
    )
    result = SimpleNamespace(requirements=[requirement], decisions=[], degraded=False)

    metrics = _case_metrics(fixture, result, [], {"items": []})

    assert metrics.evidence_recall_at_3 == 0


def test_offline_metrics_false_support_is_zero_without_expected_supported():
    fixture = EvaluationFixture(
        case=EvaluationCase(
            id="no-supported",
            target_role="Python 后端开发实习",
            resume_text="项目经历：完成后端练习和接口文档整理，记录了学习过程和测试结果。" * 3,
            jd_text="岗位要求 Python 后端开发能力，负责接口实现、服务维护、问题排查和基础测试。" * 2,
        ),
        expected_requirements=[ExpectedRequirement(skill="Python", category="must")],
        expected_evidence=[
            ExpectedEvidence(
                requirement_skill="Python",
                expected_chunk_keywords=["后端"],
                expected_status="partial",
            )
        ],
    )
    requirement = JDRequirement(
        id="req-1",
        text="Python",
        skill="Python",
        category="must",
        source_quote="岗位要求 Python",
    )
    candidate = EvidenceCandidate(
        id="evidence-1",
        requirement_id="req-1",
        chunk_id="resume-1",
        snippet="[项目经历] 后端练习",
        lexical_score=0.2,
        rerank_score=0.2,
    )
    decision = EvidenceDecision(
        requirement_id="req-1",
        status="partial",
        evidence_ids=[candidate.id],
        confidence=0.5,
        rationale="只有相关项目描述",
    )
    result = SimpleNamespace(requirements=[requirement], decisions=[decision], degraded=False)
    chain = {
        "items": [
            {
                "requirement": requirement.model_dump(),
                "candidates": [candidate.model_dump()],
            }
        ]
    }

    metrics = _case_metrics(fixture, result, [], chain)

    assert metrics.false_support_rate == 0


def test_offline_metrics_are_deterministic():
    fixtures = load_fixtures()
    fixture = fixtures[0]
    result = SimpleNamespace(requirements=[], decisions=[], degraded=False)
    first = _case_metrics(fixture, result, [], {"items": []})
    second = _case_metrics(fixture, result, [], {"items": []})
    assert first == second
