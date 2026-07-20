"""Evidence-driven intermediate contracts for the copilot pipeline."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.analysis import ActionPlanItem, InterviewQuestion


class AgentStage(StrEnum):
    QUEUED = "queued"
    JD_EXTRACTED = "jd_extracted"
    EVIDENCE_RETRIEVED = "evidence_retrieved"
    EVIDENCE_JUDGED = "evidence_judged"
    SCORED = "scored"
    REPORT_GENERATED = "report_generated"
    VALIDATED = "validated"
    DEGRADED = "degraded"
    FAILED = "failed"


class JDRequirement(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=2, max_length=500)
    skill: str = Field(min_length=1, max_length=100)
    category: Literal["must", "preferred", "context"] = "must"
    weight: int = Field(default=1, ge=1, le=10)
    source_quote: str = Field(min_length=2, max_length=800)


class ResumeChunk(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    section: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=1200)
    start_offset: int = Field(default=0, ge=0)
    end_offset: int = Field(default=0, ge=0)


class ResumeChunkCandidate(BaseModel):
    chunk: ResumeChunk
    lexical_score: float = Field(ge=0, le=1)
    embedding_score: float | None = Field(default=None, ge=0, le=1)
    fusion_score: float | None = Field(default=None, ge=0, le=1)
    rerank_score: float | None = Field(default=None, ge=0, le=1)


class EvidenceCandidate(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    requirement_id: str = Field(min_length=1, max_length=80)
    chunk_id: str = Field(min_length=1, max_length=80)
    snippet: str = Field(min_length=1, max_length=1200)
    lexical_score: float = Field(ge=0, le=1)
    embedding_score: float | None = Field(default=None, ge=0, le=1)
    fusion_score: float | None = Field(default=None, ge=0, le=1)
    rerank_score: float | None = Field(default=None, ge=0, le=1)


class EvidenceDecision(BaseModel):
    requirement_id: str = Field(min_length=1, max_length=80)
    status: Literal["supported", "partial", "missing_evidence"]
    evidence_ids: list[str] = Field(default_factory=list, max_length=3)
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(min_length=2, max_length=500)

    @model_validator(mode="after")
    def validate_evidence_status(self):
        if self.status == "supported" and not self.evidence_ids:
            raise ValueError("supported 必须关联至少一条真实 evidence_id")
        if self.status == "partial" and not self.evidence_ids:
            raise ValueError("partial 必须关联至少一条真实 evidence_id")
        if self.status == "missing_evidence" and self.evidence_ids:
            raise ValueError("missing_evidence 不应关联证据")
        return self


class EvidenceFeedbackCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["confirmed", "rejected", "corrected"]
    corrected_status: Literal["supported", "partial", "missing_evidence"] | None = None
    evidence_ids: list[str] = Field(default_factory=list, max_length=3)
    note: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def validate_feedback(self):
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("evidence_ids 不可重复")
        if self.verdict == "corrected" and self.corrected_status is None:
            raise ValueError("corrected 必须指定 corrected_status")
        if self.verdict != "corrected" and self.corrected_status is not None:
            raise ValueError("confirmed/rejected 不应指定 corrected_status")
        if self.corrected_status == "missing_evidence" and self.evidence_ids:
            raise ValueError("missing_evidence 不允许关联 evidence_ids")
        if self.corrected_status in {"supported", "partial"} and not self.evidence_ids:
            raise ValueError("supported/partial 修正必须关联至少一条 evidence_id")
        return self


class EvidenceFeedbackRequest(EvidenceFeedbackCreate):
    requirement_id: str = Field(min_length=1, max_length=80)


class EvidenceFeedback(BaseModel):
    id: int
    turn_id: int
    analysis_run_id: int
    requirement_id: str = Field(min_length=1, max_length=80)
    verdict: Literal["confirmed", "rejected", "corrected"]
    corrected_status: Literal["supported", "partial", "missing_evidence"] | None = None
    evidence_ids: list[str] = Field(default_factory=list, max_length=3)
    note: str = Field(default="", max_length=300)
    created_at: str

    @model_validator(mode="after")
    def validate_feedback(self):
        EvidenceFeedbackCreate(
            verdict=self.verdict,
            corrected_status=self.corrected_status,
            evidence_ids=self.evidence_ids,
            note=self.note,
        )
        return self


class ReportNarrative(BaseModel):
    """LLM may write narrative fields, but never factual match fields."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=20)
    resume_bullets: list[str] = Field(default_factory=list)
    interview_questions: list[InterviewQuestion] = Field(default_factory=list)
    action_plan: list[ActionPlanItem] = Field(default_factory=list)
    risk_points: list[str] = Field(default_factory=list)


class ScoringResult(BaseModel):
    score: int = Field(ge=0, le=100)
    must_coverage: float = Field(ge=0, le=1)
    preferred_coverage: float = Field(ge=0, le=1)
    project_relevance: float = Field(ge=0, le=1)
    review_required: bool = False
    review_reason: str = ""


class EvidenceChainItem(BaseModel):
    requirement: JDRequirement
    chunks: list[ResumeChunk] = Field(default_factory=list)
    candidates: list[EvidenceCandidate] = Field(default_factory=list)
    decision: EvidenceDecision | None = None
    review: EvidenceFeedback | None = None


class EvidenceChainResponse(BaseModel):
    turn_id: int
    analysis_run_id: int
    status: str
    current_stage: str
    pipeline_version: str
    items: list[EvidenceChainItem] = Field(default_factory=list)
