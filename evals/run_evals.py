"""Run the evidence pipeline against anonymized fixtures without touching project DB."""

import argparse
import json
import tempfile
import time
from pathlib import Path

from app import database
from app.agent_pipeline.orchestrator import run_analysis_pipeline
from app.agent_pipeline.retrieval import TfidfResumeRetriever, build_resume_retriever
from app.config import settings
from app.schemas.agent_pipeline import EvidenceCandidate
from evals.models import (
    CaseEvaluation,
    EvaluationFixture,
    EvaluationMetrics,
    EvaluationReport,
)


ROOT = Path(__file__).resolve().parent
FIXTURES_DIR = ROOT / "fixtures"
EXPECTED_DIR = ROOT / "expected"
RESULTS_DIR = ROOT / "results"


def load_fixtures() -> list[EvaluationFixture]:
    fixtures: list[EvaluationFixture] = []
    for fixture_path in sorted(FIXTURES_DIR.glob("*.json")):
        expected_path = EXPECTED_DIR / fixture_path.name
        fixture_data = json.loads(fixture_path.read_text(encoding="utf-8"))
        expected_data = json.loads(expected_path.read_text(encoding="utf-8"))
        fixtures.append(
            EvaluationFixture(
                case=fixture_data,
                expected_requirements=expected_data["requirements"],
                expected_evidence=expected_data["evidence"],
            )
        )
    return fixtures


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _case_metrics(fixture: EvaluationFixture, result, stage_rows, evidence_chain) -> EvaluationMetrics:
    expected_required = {
        item.skill.casefold()
        for item in fixture.expected_requirements
        if item.must_extract
    }
    extracted = {item.skill.casefold() for item in result.requirements}
    true_positive = len(expected_required & extracted)
    false_positive = len(extracted - expected_required)
    false_negative = len(expected_required - extracted)

    evidence_items = []
    chain_items = evidence_chain.get("items", []) if evidence_chain else []
    for requirement in fixture.expected_evidence:
        actual_requirement = next(
            (item for item in result.requirements if item.skill.casefold() == requirement.requirement_skill.casefold()),
            None,
        )
        if actual_requirement is None:
            evidence_items.append((requirement, None, []))
            continue
        decision = next(
            (item for item in result.decisions if item.requirement_id == actual_requirement.id),
            None,
        )
        chain_item = next(
            (
                item
                for item in chain_items
                if item.get("requirement", {}).get("id") == actual_requirement.id
            ),
            None,
        )
        candidates = [
            EvidenceCandidate.model_validate(item)
            for item in (chain_item or {}).get("candidates", [])
        ]
        evidence_items.append((requirement, decision, candidates))

    recall_hits = sum(
        bool(expected.expected_chunk_keywords)
        and any(
            keyword.casefold() in candidate.snippet.casefold()
            for candidate in candidates[:3]
            for keyword in expected.expected_chunk_keywords
        )
        for expected, _decision, candidates in evidence_items
    )
    recall_denominator = sum(
        bool(expected.expected_chunk_keywords) for expected, _decision, _candidates in evidence_items
    )
    judge_pairs = [
        (expected, decision)
        for expected, decision, _candidates in evidence_items
        if decision is not None
    ]
    judge_correct = sum(
        decision.status == expected.expected_status for expected, decision in judge_pairs
    )
    false_support_denominator = sum(
        expected.expected_status != "supported" for expected, _decision in judge_pairs
    )
    false_support_count = sum(
        expected.expected_status != "supported" and decision.status == "supported"
        for expected, decision in judge_pairs
    )
    latencies: dict[str, float] = {}
    for row in stage_rows:
        if row["latency_ms"] is not None:
            latencies[row["stage"]] = float(row["latency_ms"])
    return EvaluationMetrics(
        requirement_precision=_ratio(true_positive, true_positive + false_positive),
        requirement_recall=_ratio(true_positive, true_positive + false_negative),
        evidence_recall_at_3=_ratio(recall_hits, recall_denominator),
        judge_accuracy=_ratio(judge_correct, len(judge_pairs)),
        false_support_rate=_ratio(false_support_count, false_support_denominator),
        degraded_rate=float(result.degraded),
        stage_latency_ms=latencies,
    )


def _aggregate(case_metrics: list[EvaluationMetrics]) -> EvaluationMetrics:
    if not case_metrics:
        return EvaluationMetrics(
            requirement_precision=0,
            requirement_recall=0,
            evidence_recall_at_3=0,
            judge_accuracy=0,
            false_support_rate=0,
            degraded_rate=0,
        )
    fields = (
        "requirement_precision",
        "requirement_recall",
        "evidence_recall_at_3",
        "judge_accuracy",
        "false_support_rate",
        "degraded_rate",
    )
    values = {field: sum(getattr(item, field) for item in case_metrics) / len(case_metrics) for field in fields}
    stages = sorted({stage for item in case_metrics for stage in item.stage_latency_ms})
    values["stage_latency_ms"] = {
        stage: sum(item.stage_latency_ms.get(stage, 0) for item in case_metrics) / len(case_metrics)
        for stage in stages
    }
    return EvaluationMetrics(**values)


def _print_report(report: EvaluationReport) -> None:
    metrics = report.metrics
    print(
        f"retrieval={report.retrieval_strategy} fallback_count={report.fallback_count}"
    )
    print("case_count  precision  recall  evidence@3  judge_acc  false_support  degraded")
    print(
        f"{report.case_count:10d}  {metrics.requirement_precision:9.2%}"
        f"  {metrics.requirement_recall:6.2%}  {metrics.evidence_recall_at_3:10.2%}"
        f"  {metrics.judge_accuracy:9.2%}  {metrics.false_support_rate:13.2%}"
        f"  {metrics.degraded_rate:8.2%}"
    )
    print("stage_latency_ms=" + json.dumps(metrics.stage_latency_ms, ensure_ascii=False, sort_keys=True))


def run_evaluation(*, use_llm: bool = False, retrieval: str = "tfidf") -> EvaluationReport:
    fixtures = load_fixtures()
    if retrieval not in {"tfidf", "hybrid"}:
        raise ValueError("retrieval 必须是 tfidf 或 hybrid")
    if retrieval == "tfidf":
        selected_retriever = TfidfResumeRetriever()
    else:
        if not settings.embedding_enabled:
            selected_retriever = TfidfResumeRetriever("tfidf_fallback")
            print("embedding 未配置或不可用，hybrid 评测受控回退到 tfidf_fallback")
        else:
            selected_retriever = build_resume_retriever(settings)
            if getattr(selected_retriever, "last_strategy", "tfidf") == "tfidf_fallback":
                print("embedding 未配置或不可用，hybrid 评测受控回退到 tfidf_fallback")
    previous_db_path = database.DB_PATH
    case_results: list[CaseEvaluation] = []
    try:
        with tempfile.TemporaryDirectory(
            prefix="jobmatch-evals-", ignore_cleanup_errors=True
        ) as temp_dir:
            database.DB_PATH = Path(temp_dir) / "evaluation.db"
            database.init_db()
            try:
                for fixture in fixtures:
                    session = database.create_copilot_session(None, fixture.case.target_role)
                    created = database.create_copilot_message_and_turn(
                        session["id"], fixture.case.jd_text
                    )
                    if created is None:
                        raise RuntimeError(f"无法创建评测回合：{fixture.case.id}")
                    _, turn = created
                    started = time.perf_counter()
                    result = run_analysis_pipeline(
                        turn_id=turn["id"],
                        resume_text=fixture.case.resume_text,
                        jd_text=fixture.case.jd_text,
                        target_role=fixture.case.target_role,
                        use_llm=use_llm,
                        retriever=selected_retriever,
                    )
                    elapsed_ms = round((time.perf_counter() - started) * 1000)
                    stage_rows = database.list_agent_stage_runs(result.run_id)
                    evidence_chain = database.get_analysis_evidence_chain(turn["id"])
                    metrics = _case_metrics(fixture, result, stage_rows, evidence_chain)
                    metrics.stage_latency_ms["total"] = float(elapsed_ms)
                    case_results.append(
                        CaseEvaluation(
                            case_id=fixture.case.id,
                            degraded=result.degraded,
                            retrieval_strategy=result.retrieval_strategy,
                            extracted_skills=[item.skill for item in result.requirements],
                            metrics=metrics,
                        )
                    )
            finally:
                database.DB_PATH = previous_db_path
    finally:
        database.DB_PATH = previous_db_path
    report = EvaluationReport(
        use_llm=use_llm,
        retrieval_strategy=retrieval,
        fallback_count=sum(
            item.retrieval_strategy == "tfidf_fallback" for item in case_results
        ),
        case_count=len(case_results),
        metrics=_aggregate([item.metrics for item in case_results]),
        cases=case_results,
    )
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    result_name = "latest.json" if retrieval == "tfidf" else "latest-hybrid.json"
    (RESULTS_DIR / result_name).write_text(
        report.model_dump_json(indent=2), encoding="utf-8"
    )
    _print_report(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run offline JobMatch evidence evaluation")
    parser.add_argument("--use-llm", action="store_true", help="enable configured LLM stages")
    parser.add_argument(
        "--retrieval",
        choices=("tfidf", "hybrid"),
        default="tfidf",
        help="retrieval strategy (default: tfidf)",
    )
    args = parser.parse_args()
    run_evaluation(use_llm=args.use_llm, retrieval=args.retrieval)


if __name__ == "__main__":
    main()
