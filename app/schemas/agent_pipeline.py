"""Evidence-driven intermediate contracts for the copilot pipeline."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


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


class EvidenceCandidate(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    requirement_id: str = Field(min_length=1, max_length=80)
    chunk_id: str = Field(min_length=1, max_length=80)
    snippet: str = Field(min_length=1, max_length=1200)
    lexical_score: float = Field(ge=0, le=1)
    embedding_score: float | None = Field(default=None, ge=0, le=1)
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
        if self.status == "missing_evidence" and self.evidence_ids:
            raise ValueError("missing_evidence 不应关联证据")
        return self


class ScoringResult(BaseModel):
    score: int = Field(ge=0, le=100)
    must_coverage: float = Field(ge=0, le=1)
    preferred_coverage: float = Field(ge=0, le=1)
    project_relevance: float = Field(ge=0, le=1)
    review_required: bool = False
    review_reason: str = ""

