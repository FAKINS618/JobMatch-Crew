"""Pydantic contracts for deterministic offline pipeline evaluation."""

from typing import Literal

from pydantic import BaseModel, Field


class EvaluationCase(BaseModel):
    id: str = Field(min_length=1)
    target_role: str = Field(min_length=2)
    resume_text: str = Field(min_length=80)
    jd_text: str = Field(min_length=80)


class ExpectedRequirement(BaseModel):
    skill: str = Field(min_length=1)
    category: Literal["must", "preferred", "context"]
    must_extract: bool = True


class ExpectedEvidence(BaseModel):
    requirement_skill: str = Field(min_length=1)
    expected_chunk_keywords: list[str] = Field(default_factory=list)
    expected_status: Literal["supported", "partial", "missing_evidence"]


class EvaluationFixture(BaseModel):
    case: EvaluationCase
    expected_requirements: list[ExpectedRequirement] = Field(default_factory=list)
    expected_evidence: list[ExpectedEvidence] = Field(default_factory=list)


class EvaluationMetrics(BaseModel):
    requirement_precision: float = Field(ge=0, le=1)
    requirement_recall: float = Field(ge=0, le=1)
    evidence_recall_at_3: float = Field(ge=0, le=1)
    judge_accuracy: float = Field(ge=0, le=1)
    false_support_rate: float = Field(ge=0, le=1)
    degraded_rate: float = Field(ge=0, le=1)
    stage_latency_ms: dict[str, float] = Field(default_factory=dict)


class CaseEvaluation(BaseModel):
    case_id: str
    degraded: bool
    retrieval_strategy: str = "tfidf"
    extracted_skills: list[str] = Field(default_factory=list)
    metrics: EvaluationMetrics


class EvaluationReport(BaseModel):
    use_llm: bool
    retrieval_strategy: str = "tfidf"
    fallback_count: int = Field(default=0, ge=0)
    case_count: int = Field(ge=0)
    metrics: EvaluationMetrics
    cases: list[CaseEvaluation] = Field(default_factory=list)
