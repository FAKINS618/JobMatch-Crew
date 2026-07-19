"""Orchestrate validated stages and persist every intermediate result."""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass

from app.agent_pipeline.evidence_judge import judge_evidence, retrieve_candidates
from app.agent_pipeline.jd_extractor import extract_requirements
from app.agent_pipeline.report_writer import write_report
from app.agent_pipeline.scorer import score_requirements
from app.database import (
    create_analysis_run,
    save_agent_stage_run,
    save_requirement_evidence,
    update_analysis_run,
)
from app.schemas import JobMatchAnalysis
from app.schemas.agent_pipeline import AgentStage, EvidenceDecision, JDRequirement, ScoringResult


@dataclass
class PipelineResult:
    analysis: JobMatchAnalysis
    run_id: int
    degraded: bool
    requirements: list[JDRequirement]
    decisions: list[EvidenceDecision]
    scoring: ScoringResult


def _json(value: object) -> str:
    def normalize(item: object) -> object:
        if hasattr(item, "model_dump"):
            return normalize(item.model_dump())
        if isinstance(item, list):
            return [normalize(value) for value in item]
        if isinstance(item, dict):
            return {key: normalize(value) for key, value in item.items()}
        return item

    return json.dumps(normalize(value), ensure_ascii=False, default=str)


def run_analysis_pipeline(
    *,
    turn_id: int,
    resume_text: str,
    jd_text: str,
    target_role: str,
    use_llm: bool = True,
    on_stage: Callable[[AgentStage, int], None] | None = None,
) -> PipelineResult:
    run_id = create_analysis_run(turn_id, pipeline_version="m1-evidence-v1")
    degraded = False

    def stage(stage_name: AgentStage, progress: int) -> None:
        update_analysis_run(run_id, status="running", current_stage=stage_name.value)
        if on_stage:
            on_stage(stage_name, progress)

    def save_stage(
        stage_name: AgentStage,
        status: str,
        started: float,
        input_payload: object,
        output_payload: object,
        error: str = "",
    ) -> None:
        save_agent_stage_run(
            run_id=run_id,
            stage=stage_name.value,
            status=status,
            input_json=_json(input_payload),
            output_json=_json(output_payload),
            validation_error=error,
            latency_ms=round((time.perf_counter() - started) * 1000),
        )

    try:
        stage(AgentStage.QUEUED, 5)
        started = time.perf_counter()
        save_stage(AgentStage.QUEUED, "validated", started, {}, {})

        started = time.perf_counter()
        requirements, jd_degraded = extract_requirements(jd_text, target_role, use_llm=use_llm)
        degraded = degraded or jd_degraded
        save_stage(AgentStage.JD_EXTRACTED, "degraded" if jd_degraded else "validated", started, {"target_role": target_role}, requirements)

        stage(AgentStage.EVIDENCE_RETRIEVED, 30)
        started = time.perf_counter()
        candidates = retrieve_candidates(resume_text, requirements)
        save_stage(AgentStage.EVIDENCE_RETRIEVED, "validated", started, {"requirement_count": len(requirements)}, candidates)

        stage(AgentStage.EVIDENCE_JUDGED, 50)
        started = time.perf_counter()
        decisions, judge_degraded = judge_evidence(requirements, candidates, use_llm=use_llm)
        degraded = degraded or judge_degraded
        save_stage(AgentStage.EVIDENCE_JUDGED, "degraded" if judge_degraded else "validated", started, candidates, decisions)
        for requirement in requirements:
            decision = next((item for item in decisions if item.requirement_id == requirement.id), None)
            save_requirement_evidence(
                run_id=run_id,
                requirement_json=_json(requirement.model_dump()),
                candidates_json=_json([item.model_dump() for item in candidates.get(requirement.id, [])]),
                decision_json=_json(decision.model_dump() if decision else None),
            )

        stage(AgentStage.SCORED, 70)
        started = time.perf_counter()
        scoring = score_requirements(requirements, decisions, resume_text)
        save_stage(AgentStage.SCORED, "validated", started, {"requirements": requirements, "decisions": decisions}, scoring)

        stage(AgentStage.REPORT_GENERATED, 85)
        started = time.perf_counter()
        analysis, report_degraded = write_report(
            requirements, candidates, decisions, scoring, use_llm=use_llm
        )
        degraded = degraded or report_degraded
        save_stage(AgentStage.REPORT_GENERATED, "degraded" if report_degraded else "validated", started, {"requirements": requirements, "decisions": decisions, "score": scoring}, analysis)

        stage(AgentStage.VALIDATED, 100)
        update_analysis_run(
            run_id,
            status="degraded" if degraded else "completed",
            current_stage=AgentStage.DEGRADED.value if degraded else AgentStage.VALIDATED.value,
        )
        return PipelineResult(analysis, run_id, degraded, requirements, decisions, scoring)
    except Exception as exc:
        update_analysis_run(run_id, status="failed", current_stage=AgentStage.FAILED.value, error_message=str(exc))
        if on_stage:
            on_stage(AgentStage.FAILED, 100)
        raise
