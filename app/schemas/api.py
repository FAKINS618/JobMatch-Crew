from pydantic import BaseModel, Field, field_validator
from app.schemas.analysis import JobMatchAnalysis
from app.schemas.analysis import JobMarketProfile, MarketResumeMatchAnalysis

# 定义接口请求和响应

# 表示 /api/job-match 的请求体必须包含简历、JD 和目标岗位
class JobMatchRequest(BaseModel):
    resume_text: str = Field(..., min_length=80, description="候选人简历文本")
    jd_text: str = Field(..., min_length=80, description="岗位 JD 文本")
    target_role: str = Field(default="计算机相关岗位")
    resume_version_id: int | None = Field(default=None, gt=0)
    resume_version_id: int | None = Field(default=None, gt=0)

    # 当创建 JobMatchRequest 对象时，要对 resume_text 和 jd_text 这两个字段执行这个校验函数
    @field_validator("resume_text", "jd_text")
    @classmethod # 类方法
    def reject_invalid_text(cls, value: str):
        text = value.strip()

        invalid_values = {
            "测试",
            "test",
            "xxx",
            "无",
            "暂无",
            "随便",
            "占位",
            "string",
        }
        # 拒绝无效的文本
        if text.lower() in invalid_values:
            raise ValueError("请提供真实、完整的简历或岗位 JD 内容")

        if len(set(text)) < 10:
            raise ValueError("输入内容过于简单，请提供更完整的信息")

        return text


class JobMatchResponse(BaseModel):
    report_id: int | None = None
    score: int | None = None
    matched_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    interview_questions: list[str] = Field(default_factory=list)
    action_plan: list[str] = Field(default_factory=list)
    markdown_report: str
    analysis: JobMatchAnalysis | None = None


class JobSearchRequest(BaseModel):
    keyword: str = Field(..., min_length=2, description="岗位关键词")
    city: str = Field(default="", description="城市")
    max_results: int = Field(default=5, ge=1, le=10)


class JobSearchResult(BaseModel):
    title: str | None = None
    url: str | None = None
    content: str | None = None


class JobSearchResponse(BaseModel):
    results: list[JobSearchResult]



class MarketMatchRequest(BaseModel):
    resume_text: str = Field(..., min_length=80, description="候选人简历文本")
    target_role: str = Field(..., min_length=2, description="目标方向")
    city: str = Field(default="", description="城市")
    max_results: int = Field(default=8, ge=3, le=15)
    resume_version_id: int | None = Field(default=None, gt=0)
    resume_version_id: int | None = Field(default=None, gt=0)


class MarketMatchResponse(BaseModel):
    market_profile: JobMarketProfile
    analysis: MarketResumeMatchAnalysis
    markdown_report: str
    report_id: int | None = None
