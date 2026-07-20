"""Resume evidence retrieval and constrained evidence judgement."""

import json
import re

from pydantic import BaseModel, Field

from app.agent_pipeline.retrieval import (
    EvidenceReranker,
    ResumeRetriever,
    RuleEvidenceReranker,
    TfidfResumeRetriever,
)
from app.agent_pipeline.structured_runner import StageOutcome, run_structured
from app.schemas.agent_pipeline import (
    EvidenceCandidate,
    EvidenceDecision,
    JDRequirement,
    ResumeChunk,
)


class EvidenceDecisionBundle(BaseModel):
    decisions: list[EvidenceDecision] = Field(default_factory=list, max_length=20)


def retrieve_candidates(
    resume_text: str,
    requirements: list[JDRequirement],
    top_k: int = 3,
    *,
    retriever: ResumeRetriever | None = None,
    reranker: EvidenceReranker | None = None,
) -> tuple[dict[str, list[EvidenceCandidate]], dict[str, ResumeChunk]]:
    candidates: dict[str, list[EvidenceCandidate]] = {}
    resume_chunks: dict[str, ResumeChunk] = {}
    retriever = retriever or TfidfResumeRetriever()
    reranker = reranker or RuleEvidenceReranker()
    for requirement in requirements:
        retrieved = retriever.retrieve(requirement, resume_text, top_k=8)
        ranked = reranker.rerank(requirement, retrieved, top_k=top_k)
        requirement_candidates: list[EvidenceCandidate] = []
        for index, candidate in enumerate(ranked):
            resume_chunk = resume_chunks.setdefault(
                candidate.chunk.id,
                ResumeChunk(
                    id=candidate.chunk.id,
                    section=candidate.chunk.section,
                    content=candidate.chunk.content,
                ),
            )
            candidate_id = f"evidence-{requirement.id}-{index + 1}"
            requirement_candidates.append(
                EvidenceCandidate(
                    id=candidate_id,
                    requirement_id=requirement.id,
                    chunk_id=resume_chunk.id,
                    snippet=f"[{resume_chunk.section}] {resume_chunk.content}",
                    lexical_score=candidate.lexical_score,
                    rerank_score=candidate.rerank_score,
                )
            )
        candidates[requirement.id] = requirement_candidates
    validate_candidate_chunks(candidates, resume_chunks)
    return candidates, resume_chunks


def validate_candidate_chunks(
    candidates: dict[str, list[EvidenceCandidate]],
    resume_chunks: dict[str, ResumeChunk],
) -> None:
    chunk_ids = set(resume_chunks)
    for items in candidates.values():
        for candidate in items:
            if candidate.chunk_id not in chunk_ids:
                raise ValueError(f"candidate {candidate.id} 引用了不存在的 ResumeChunk")


def rule_judge(
    requirements: list[JDRequirement],
    candidates: dict[str, list[EvidenceCandidate]],
) -> list[EvidenceDecision]:
    decisions: list[EvidenceDecision] = []
    for requirement in requirements:
        items = candidates.get(requirement.id, [])
        direct = [item for item in items if re.search(re.escape(requirement.skill), item.snippet, re.IGNORECASE)]
        if direct:
            decisions.append(
                EvidenceDecision(
                    requirement_id=requirement.id,
                    status="supported",
                    evidence_ids=[direct[0].id],
                    confidence=0.92,
                    rationale="简历片段中存在岗位技能的直接证据。",
                )
            )
        elif items:
            decisions.append(
                EvidenceDecision(
                    requirement_id=requirement.id,
                    status="partial",
                    evidence_ids=[items[0].id],
                    confidence=min(0.84, 0.5 + items[0].rerank_score * 0.5),
                    rationale="检索到相关项目语义，但没有直接技能名称证据。",
                )
            )
        else:
            decisions.append(
                EvidenceDecision(
                    requirement_id=requirement.id,
                    status="missing_evidence",
                    confidence=0.2,
                    rationale="简历中没有召回到可验证证据。",
                )
            )
    return decisions


def validate_decisions(
    requirements: list[JDRequirement],
    candidates: dict[str, list[EvidenceCandidate]],
    decisions: list[EvidenceDecision],
) -> None:
    requirement_ids = {item.id for item in requirements}
    candidate_map = {item.id: item for items in candidates.values() for item in items}
    candidate_ids = set(candidate_map)
    decision_ids = {item.requirement_id for item in decisions}
    if len(decisions) != len(requirements) or len(decision_ids) != len(decisions):
        raise ValueError("Evidence Judge 不得重复裁决同一 requirement")
    if decision_ids != requirement_ids:
        raise ValueError("Evidence Judge 必须为每条岗位要求返回一个 decision")
    for decision in decisions:
        if decision.requirement_id not in requirement_ids:
            raise ValueError(f"未知 requirement_id：{decision.requirement_id}")
        if any(evidence_id not in candidate_ids for evidence_id in decision.evidence_ids):
            raise ValueError(f"{decision.requirement_id} 引用了不存在的 evidence_id")
        if any(
            candidate_map[evidence_id].requirement_id != decision.requirement_id
            for evidence_id in decision.evidence_ids
        ):
            raise ValueError(f"{decision.requirement_id} 引用了其他岗位要求的 evidence_id")


def judge_evidence(
    requirements: list[JDRequirement],
    candidates: dict[str, list[EvidenceCandidate]],
    *,
    use_llm: bool,
) -> tuple[list[EvidenceDecision], bool, StageOutcome]:
    candidate_payload = {
        key: [item.model_dump() for item in value] for key, value in candidates.items()
    }
    prompt = f"""
    你是受限证据裁决 Agent。只能使用下面列出的岗位要求和候选简历片段。
    不得读取或想象完整简历，不得生成不存在的 evidence_id。
    没有明确事实时只能输出 partial 或 missing_evidence。
    只输出 {{\"decisions\": [...]}}。

    requirements={json.dumps([item.model_dump() for item in requirements], ensure_ascii=False)}
    candidates={json.dumps(candidate_payload, ensure_ascii=False)}
    """
    try:
        outcome = run_structured(
            prompt=prompt,
            output_model=EvidenceDecisionBundle,
            expected_output="包含 decisions 数组的 JSON 对象",
            enabled=use_llm,
        )
        if outcome.value is None:
            return rule_judge(requirements, candidates), True, outcome
        validate_decisions(requirements, candidates, outcome.value.decisions)
        return outcome.value.decisions, outcome.degraded, outcome
    except ValueError as error:
        fallback = StageOutcome(
            value=None,
            retry_count=outcome.retry_count if "outcome" in locals() else 0,
            validation_error=f"evidence validation: {type(error).__name__}: {str(error)[:400]}",
            degraded=True,
        )
        return rule_judge(requirements, candidates), True, fallback
