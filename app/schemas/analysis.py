from datetime import datetime, date
from typing import Literal
from pydantic import BaseModel, Field, model_validator


class ScoreDimension(BaseModel):
    """一个评分维度，例如 Python 基础、FastAPI、RAG、Docker 等。"""

    name: str
    score: int = Field(ge=0)
    max_score: int = Field(gt=0)
    evidence: list[str] = Field(default_factory=list)
    suggestion: str = ""

    @model_validator(mode="after")
    def validate_score_range(self):
        if self.score > self.max_score:
            raise ValueError("score 不能大于 max_score")
        return self


class InterviewQuestion(BaseModel):
    """根据岗位和简历短板生成的面试题。"""

    question: str
    skill: str
    reason: str = ""


class ActionPlanItem(BaseModel):
    """补强计划中的一天任务。"""

    day: int = Field(ge=1, le=14)
    task: str
    output: str = ""


class JobMatchAnalysis(BaseModel):
    """最终报告的稳定数据结构。

    LLM 负责生成这些字段，后端负责校验和渲染 Markdown。
    """
    score: int = Field(ge=0, le=100)
    summary: str = Field(min_length=20)
    matched_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    score_dimensions: list[ScoreDimension] = Field(default_factory=list)
    resume_bullets: list[str] = Field(default_factory=list)
    interview_questions: list[InterviewQuestion] = Field(default_factory=list)
    action_plan: list[ActionPlanItem] = Field(default_factory=list)
    risk_points: list[str] = Field(default_factory=list)


class JDAnalysis(BaseModel):
    """预留的 JD 结构化结果，后续可用于岗位画像和报告对比。"""

    job_title: str = ""
    responsibilities: list[str] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    project_requirements: list[str] = Field(default_factory=list)
    interview_topics: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class ResumeAnalysis(BaseModel):
    """预留的简历结构化结果，后续可用于简历版本对比。"""

    skills: list[str] = Field(default_factory=list)
    projects: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class MatchScoreAnalysis(BaseModel):
    """预留的匹配评分结构，后续可把中间 Agent 也结构化。"""

    score: int = Field(ge=0, le=100)
    dimensions: list[ScoreDimension] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    matched_skills: list[str] = Field(default_factory=list)


class JobPost(BaseModel):
    """单条岗位搜索结果。

    这个模型不是直接给用户看的，而是作为“岗位市场画像”的原始样本。
    后续保存到 job_posts 表后，可以追溯某份报告到底参考了哪些岗位。
    """

    title: str = ""
    company: str = ""
    url: str = ""
    content: str = ""
    source: str = ""

    published_at: date | None = None  # 岗位发布时间，搜索源能提取到时再填充。
    deadline_at: date | None = None  # 投递截止时间，用于识别已过期岗位。
    fetched_at: datetime = Field(default_factory=datetime.now)  # 系统实际抓取时间。

    status: str = "unknown"  # active / likely_active / expired / unknown
    freshness_score: float = Field(default=0.5, ge=0, le=1)  # 时效性分数，越接近 1 越新。
    invalid_reason: str = ""  # 标记为无效或未知时的原因，方便调试和前端展示。
    relevance_score: float = Field(default=0, ge=0, le=100)
    verification_status: str = "not_checked"  # verified / likely / unavailable / skipped
    verification_reason: str = ""

class JobRecommendation(BaseModel):
    """ 具体岗位分级推荐
        不只显示市场匹配分,而是输出每个岗位的 A 可立即投递 / B 补强后投递 / C 暂不建议
     """
    title: str
    company: str = ""
    url: str
    level: Literal["A", "B", "C"]
    match_score: int = Field(ge=0, le=100)
    matched_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    reason: str
    freshness_label: str = "时效待确认"


class MarketDataQuality(BaseModel):
    level: Literal["high", "medium", "low"]
    active_job_count: int = 0
    source_domain_count: int = 0
    message: str = ""

class JobMarketProfile(BaseModel):
    """岗位市场画像。

    这是给 LLM 和前端使用的聚合结果，不直接塞入所有岗位全文。
    sample_count / valid_count / expired_count 用于说明本次分析的数据可信度。
    """

    target_role: str
    sample_count: int = 0
    valid_count: int = 0
    likely_active_count: int = 0
    relevant_count: int = 0
    expired_count: int = 0
    unknown_count: int = 0
    freshness_level: str = "unknown"
    fetched_at: datetime = Field(default_factory=datetime.now)

    frequent_skills: list[str] = Field(default_factory=list)
    frequent_tools: list[str] = Field(default_factory=list)
    project_requirements: list[str] = Field(default_factory=list)
    common_responsibilities: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)

    source_domain_count: int = 0
    data_quality: MarketDataQuality | None = None


class MarketResumeMatchAnalysis(BaseModel):
    # score 保持兼容旧报告，等同于投递适配度；趋势适配度和投递适配度分开表达。
    score: int | None = Field(default=None, ge=0, le=100)
    trend_score: int | None = Field(default=None, ge=0, le=100)
    delivery_score: int | None = Field(default=None, ge=0, le=100)
    summary: str = Field(min_length=20)
    matched_market_skills: list[str] = Field(default_factory=list)
    missing_market_skills: list[str] = Field(default_factory=list)
    recommended_roles: list[str] = Field(default_factory=list)
    resume_improvement_suggestions: list[str] = Field(default_factory=list)
    delivery_strategy: list[str] = Field(default_factory=list)

    job_recommendations: list[JobRecommendation] = Field(default_factory=list)
