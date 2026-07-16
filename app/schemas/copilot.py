"""面向 Vue 求职副驾的会话与分析产物契约。"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


CopilotRole = Literal["user", "assistant"]
TurnStatus = Literal["pending", "running", "completed", "failed"]
ArtifactType = Literal["job_brief", "evidence_map", "fit_strategy", "action_bundle"]
ArtifactDecision = Literal["accept", "reject", "ask", "create_task"]


class CopilotSessionCreate(BaseModel):
    resume_version_id: int | None = Field(default=None, gt=0)
    target_role: str = Field(default="", max_length=120)


class CopilotSessionResponse(BaseModel):
    id: int
    resume_version_id: int | None = None
    active_report_id: int | None = None
    target_role: str = ""
    status: str
    created_at: datetime
    updated_at: datetime


class CopilotMessageCreate(BaseModel):
    content: str = Field(min_length=2, max_length=20000)


class CopilotMessageResponse(BaseModel):
    id: int
    session_id: int
    role: CopilotRole
    content: str
    turn_id: int | None = None
    created_at: datetime


class AnalysisArtifactResponse(BaseModel):
    id: int
    turn_id: int
    artifact_type: ArtifactType
    payload: dict[str, Any]
    status: str
    created_at: datetime


class AnalysisTurnResponse(BaseModel):
    id: int
    session_id: int
    status: TurnStatus
    stage: str = "queued"
    progress: int = Field(default=0, ge=0, le=100)
    error_message: str = ""
    report_id: int | None = None
    parent_turn_id: int | None = None
    input_type: Literal["initial_jd", "follow_up"] = "initial_jd"
    created_at: datetime
    updated_at: datetime
    artifacts: list[AnalysisArtifactResponse] = Field(default_factory=list)


class CopilotSessionDetailResponse(CopilotSessionResponse):
    messages: list[CopilotMessageResponse] = Field(default_factory=list)
    turns: list[AnalysisTurnResponse] = Field(default_factory=list)


class ArtifactDecisionCreate(BaseModel):
    decision: ArtifactDecision
    note: str = Field(default="", max_length=2000)


class ArtifactDecisionResponse(BaseModel):
    id: int
    artifact_id: int
    decision: ArtifactDecision
    note: str = ""
    created_at: datetime
