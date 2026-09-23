from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.limits import MAX_RESUME_TEXT_CHARS, MAX_TARGET_ROLE_CHARS


class ResumeProject(BaseModel):
    name: str
    role: str = ""
    technologies: list[str] = Field(default_factory=list)
    description: str = ""
    achievements: list[str] = Field(default_factory=list)


class ResumeProfile(BaseModel):
    education: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    projects: list[ResumeProject] = Field(default_factory=list)
    internships: list[str] = Field(default_factory=list)
    awards: list[str] = Field(default_factory=list)
    target_roles: list[str] = Field(default_factory=list)
    available_from: str = ""
    parse_notes: list[str] = Field(default_factory=list)


class ResumeParseRequest(BaseModel):
    """调用简历解析接口时的原始输入。"""

    raw_text: str = Field(..., min_length=80, max_length=MAX_RESUME_TEXT_CHARS, description="候选人简历文本")


class ResumeParseResponse(BaseModel):
    """模型解析并经过 Pydantic 校验后的简历档案。"""

    profile: ResumeProfile


class ResumeVersionCreate(BaseModel):
    resume_id: int | None = None
    version_name: str = Field(min_length=2, max_length=50)
    target_role: str = Field(default="", max_length=MAX_TARGET_ROLE_CHARS)
    raw_text: str = Field(min_length=80, max_length=MAX_RESUME_TEXT_CHARS)
    profile: ResumeProfile


class ResumeVersionResponse(BaseModel):
    id: int
    resume_id: int
    version_name: str
    target_role: str
    raw_text: str
    profile: ResumeProfile
    created_at: str | None = None


class ResumeHistoryMessage(BaseModel):
    id: int
    session_id: int
    turn_id: int | None = None
    role: Literal["user", "assistant"]
    content: str
    created_at: str | None = None


class ResumeHistoryArtifact(BaseModel):
    id: int
    turn_id: int
    artifact_type: Literal["job_brief", "evidence_map", "fit_strategy", "action_bundle"]
    payload: dict[str, Any] = Field(default_factory=dict)
    status: str
    created_at: str | None = None


class ResumeReportHistory(BaseModel):
    id: int
    target_role: str
    score: int | None = None
    parse_status: str = ""
    report_summary: str = ""
    created_at: str | None = None
    created_at_local: str | None = None
    job_post_count: int = 0


class ResumeTurnHistory(BaseModel):
    id: int
    session_id: int
    status: str
    stage: str = "queued"
    progress: int = Field(default=0, ge=0, le=100)
    report_id: int | None = None
    input_type: Literal["initial_jd", "follow_up"] = "initial_jd"
    created_at: str | None = None
    updated_at: str | None = None
    artifacts: list[ResumeHistoryArtifact] = Field(default_factory=list)


class ResumeCopilotSessionHistory(BaseModel):
    id: int
    resume_version_id: int | None = None
    target_role: str = ""
    status: str
    created_at: str | None = None
    updated_at: str | None = None
    messages: list[ResumeHistoryMessage] = Field(default_factory=list)
    turns: list[ResumeTurnHistory] = Field(default_factory=list)


class ResumeMarketSearchPreference(BaseModel):
    resume_version_id: int
    auto_search_enabled: bool = False
    city: str = ""
    updated_at: str | None = None


class ResumeMarketSearchPreferenceUpdate(BaseModel):
    auto_search_enabled: bool
    city: str = Field(default="", max_length=120)


class ResumeMarketSearchTrigger(BaseModel):
    id: int
    resume_version_id: int
    source_turn_id: int
    analysis_task_id: int
    report_id: int | None = None
    target_role: str
    city: str = ""
    trigger_mode: Literal["auto", "manual"] = "auto"
    status: Literal["pending", "running", "success", "failed", "skipped"]
    reason: str = ""
    created_at: str | None = None
    updated_at: str | None = None


class ResumeAnalysisHistoryResponse(BaseModel):
    resume_version_id: int
    resume_id: int
    version_name: str
    target_role: str = ""
    created_at: str | None = None
    sessions: list[ResumeCopilotSessionHistory] = Field(default_factory=list)
    reports: list[ResumeReportHistory] = Field(default_factory=list)
    market_search_preference: ResumeMarketSearchPreference
    market_search_triggers: list[ResumeMarketSearchTrigger] = Field(default_factory=list)


class AutoMarketSearchResponse(BaseModel):
    trigger: ResumeMarketSearchTrigger
    task_status: str
    reused: bool = False
