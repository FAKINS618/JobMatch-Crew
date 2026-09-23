"""求职闭环中的岗位、行动任务和仪表盘数据契约。"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.resume import ResumeProfile


JobTargetStatus = Literal[
    "saved",
    "applied",
    "written_test",
    "interview",
    "offer",
    "rejected",
    "withdrawn",
]
JobPriority = Literal["A", "B", "C"]
ActionItemStatus = Literal["todo", "in_progress", "completed", "cancelled"]
ActionPriority = Literal["high", "medium", "low"]
EvidenceType = Literal["link", "note", "resume_version"]
ResumeSuggestionStatus = Literal["pending", "accepted", "rejected", "edited"]
InterviewPerformance = Literal["strong", "mixed", "needs_work"]
InterviewResult = Literal["pending", "passed", "failed", "unknown"]


class JobPostConfirmationResponse(BaseModel):
    id: int
    report_id: int
    title: str = ""
    company: str = ""
    url: str
    status: str
    verification_status: str = ""
    verification_reason: str = ""
    deadline_at: date | None = None

class JobTargetCreate(BaseModel):
    """从一份市场报告中的已验证岗位创建投递目标。"""

    report_id: int = Field(gt=0)
    url: str = Field(min_length=8, max_length=2048)
    priority: JobPriority
    note: str = Field(default="", max_length=2000)


class JobTargetUpdate(BaseModel):
    """用户可维护投递状态、备注和人工确认的截止日期。"""

    status: JobTargetStatus | None = None
    note: str | None = Field(default=None, max_length=2000)
    deadline_at: date | None = None


class JobTargetResponse(BaseModel):
    id: int
    report_id: int
    job_post_id: int
    resume_version_id: int | None = None
    title: str
    company: str = ""
    url: str
    priority: JobPriority
    match_score: int | None = None
    source_status: str
    status: JobTargetStatus
    note: str = ""
    deadline_at: date | None = None
    applied_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ApplicationEventCreate(BaseModel):
    """补充记录投递过程中的不可变事件，不会改变岗位状态。"""

    event_type: Literal[
        "saved", "applied", "written_test", "interview", "offer", "rejected", "withdrawn",
        "status:saved", "status:applied", "status:written_test", "status:interview",
        "status:offer", "status:rejected", "status:withdrawn", "note",
    ]
    occurred_at: datetime | None = None
    note: str = Field(default="", max_length=2000)


class ApplicationEventResponse(BaseModel):
    id: int
    job_target_id: int
    event_type: str
    occurred_at: datetime
    note: str = ""


class ActionItemsFromReportRequest(BaseModel):
    """仅把报告中的技能缺口转为可执行任务。"""

    skills: list[str] = Field(default_factory=list, max_length=10)
    resume_version_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def normalize_skills(self):
        self.skills = list(dict.fromkeys(skill.strip() for skill in self.skills if skill.strip()))
        return self


class ActionItemUpdate(BaseModel):
    status: ActionItemStatus | None = None
    priority: ActionPriority | None = None
    due_date: date | None = None
    title: str | None = Field(default=None, min_length=2, max_length=200)


class ActionItemResponse(BaseModel):
    id: int
    report_id: int
    resume_version_id: int | None = None
    action_type: str
    skill: str = ""
    title: str
    priority: ActionPriority
    status: ActionItemStatus
    expected_output: str
    due_date: date | None = None
    evidence_count: int = 0
    source_report_id: int | None = None
    source_job_target_id: int | None = None
    source_job_title: str = ""
    source_interview_review_id: int | None = None
    source_type: str = "report"
    source_turn_id: int | None = None
    archived_at: str | None = None
    created_at_local: str | None = None
    updated_at_local: str | None = None
    created_at: datetime
    updated_at: datetime


class ActionEvidenceCreate(BaseModel):
    evidence_type: EvidenceType
    content: str = Field(default="", max_length=3000)
    url: str | None = Field(default=None, max_length=2048)
    resume_version_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def require_evidence_content(self):
        if self.evidence_type == "link" and not self.url:
            raise ValueError("链接类型证据必须提供 url")
        if self.evidence_type == "note" and not self.content.strip():
            raise ValueError("说明类型证据必须提供内容")
        if self.evidence_type == "resume_version" and self.resume_version_id is None:
            raise ValueError("简历版本证据必须提供 resume_version_id")
        return self


class ActionEvidenceResponse(BaseModel):
    id: int
    action_item_id: int
    evidence_type: EvidenceType
    content: str = ""
    url: str | None = None
    resume_version_id: int | None = None
    created_at: datetime


class DashboardSummary(BaseModel):
    saved_job_count: int = 0
    applied_job_count: int = 0
    interview_job_count: int = 0
    offer_job_count: int = 0
    todo_action_count: int = 0
    in_progress_action_count: int = 0
    completed_action_count: int = 0
    evidence_count: int = 0

class ResumeSuggestionResponse(BaseModel):
    id: int
    report_id: int
    resume_version_id: int
    suggestion_type: str
    source_context: str = ""
    suggested_text: str
    edited_text: str = ""
    status: ResumeSuggestionStatus = "pending"
    created_at: str | None = None
    updated_at: str | None = None
    confirmed_at: str | None = None


class ResumeSuggestionUpdate(BaseModel):
    status: ResumeSuggestionStatus
    edited_text: str = Field(default="", max_length=4000)


class ResumeVersionFromSuggestionsCreate(BaseModel):
    report_id: int = Field(gt=0)
    source_resume_version_id: int = Field(gt=0)
    suggestion_ids: list[int] = Field(default_factory=list, max_length=30)
    version_name: str = Field(min_length=2, max_length=50)
    target_role: str = Field(default="", max_length=120)
    raw_text: str = Field(min_length=80)
    profile: ResumeProfile


class JobTargetTimelineResponse(BaseModel):
    target: JobTargetResponse
    events: list[ApplicationEventResponse] = Field(default_factory=list)
    interview_reviews: list["InterviewReviewResponse"] = Field(default_factory=list)


class InterviewReviewCreate(BaseModel):
    round_number: int = Field(default=1, ge=1, le=20)
    occurred_at: datetime | None = None
    questions: list[str] = Field(default_factory=list, max_length=30)
    performance: InterviewPerformance = "mixed"
    feedback: str = Field(default="", max_length=4000)
    result: InterviewResult = "unknown"
    missing_skills: list[str] = Field(default_factory=list, max_length=20)
    conclusion: str = Field(default="", max_length=4000)


class InterviewReviewUpdate(BaseModel):
    round_number: int | None = Field(default=None, ge=1, le=20)
    occurred_at: datetime | None = None
    questions: list[str] | None = Field(default=None, max_length=30)
    performance: InterviewPerformance | None = None
    feedback: str | None = Field(default=None, max_length=4000)
    result: InterviewResult | None = None
    missing_skills: list[str] | None = Field(default=None, max_length=20)
    conclusion: str | None = Field(default=None, max_length=4000)


class InterviewReviewResponse(BaseModel):
    id: int
    job_target_id: int
    report_id: int
    round_number: int
    occurred_at: str | None = None
    questions: list[str] = Field(default_factory=list)
    performance: InterviewPerformance
    feedback: str = ""
    result: InterviewResult
    missing_skills: list[str] = Field(default_factory=list)
    conclusion: str = ""
    actions_confirmed_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class InterviewActionConfirm(BaseModel):
    skills: list[str] = Field(default_factory=list, max_length=20)

NextActionType = Literal[
    "resume_suggestions",
    "resume_version_create",
    "job_target_apply",
    "job_target_follow_up",
    "interview_review",
    "interview_actions",
    "analysis_progress",
    "analysis_retry",
]
NextActionEntityType = Literal["report", "resume_version", "job_target", "interview_review", "analysis_task", "analysis_turn"]
NextActionPriority = Literal["high", "medium", "low"]


class NextActionResponse(BaseModel):
    """只读的下一步任务，不包含原始简历、JD 或内部任务数据。"""

    id: str
    action_type: NextActionType
    priority: NextActionPriority
    title: str
    description: str
    entity_type: NextActionEntityType
    entity_id: int
    route: str
    action_label: str
    due_at: str | None = None
    due_at_local: str | None = None
    updated_at: str | None = None
    updated_at_local: str | None = None
