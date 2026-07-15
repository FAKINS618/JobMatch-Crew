import logging

from crewai import Agent, Crew, Process, Task
from pydantic import ValidationError
from app.config import settings
from app.database import save_job_posts, save_report
from app.llm_factory import build_llm
from app.prompt_loader import load_prompt
from app.report_parser import extract_json_block
from app.schemas import (
    MarketMatchRequest,
    MarketMatchResponse,
    MarketResumeMatchAnalysis,
)
from app.services.market_profile_service import (
    build_market_profile,
    has_sufficient_market_data,
    has_sufficient_trend_data,
)
from app.services.job_recommendation_service import (
    build_job_recommendations,
    build_trend_match,
)


logger = logging.getLogger(__name__)

# 市场匹配服务


def generate_market_match_report(payload: MarketMatchRequest) -> MarketMatchResponse:
    """基于真实岗位搜索结果，分析简历与目标方向市场需求的匹配度。"""
    market_profile, job_posts = build_market_profile(
        target_role=payload.target_role,
        city=payload.city,
        max_results=payload.max_results,
    )
    trend_score, trend_matched_skills, trend_missing_skills = (None, [], [])
    if has_sufficient_trend_data(market_profile):
        trend_score, trend_matched_skills, trend_missing_skills = build_trend_match(
            resume_text=payload.resume_text,
            profile=market_profile,
        )

    if not has_sufficient_market_data(market_profile):
        # 搜索摘要不足以证明岗位仍可投递时，不触发 LLM。这样不会产生
        # “报告完成但字段不完整”的误导，也不会把数据问题归咎于用户简历。
        analysis = _build_insufficient_market_analysis(
            market_profile=market_profile,
            trend_score=trend_score,
            matched_skills=trend_matched_skills,
            missing_skills=trend_missing_skills,
        )
        raw_result = None
        parse_status = "skipped_insufficient_market_data"
        parse_error = None
    else:
        analysis, raw_result, parse_status, parse_error = _run_market_llm_analysis(
            payload=payload,
            market_profile=market_profile,
        )

        # 具体岗位的 A/B/C 分类由后端规则计算，不让 LLM 黑盒决定是否值得投递。
        recommendations = build_job_recommendations(
            resume_text=payload.resume_text,
            posts=job_posts,
            profile=market_profile,
        )
        analysis = analysis.model_copy(update={"job_recommendations": recommendations})

    analysis = analysis.model_copy(
        update={
            "trend_score": trend_score,
            "delivery_score": analysis.score,
        }
    )

    # 此时 analysis 已包含经过校验的模型字段和后端计算的岗位推荐。
    # 保存后，历史报告也能恢复 A/B/C 岗位推荐。
    parsed_result = analysis.model_dump_json()

    markdown_report = render_market_match_report(market_profile, analysis)

    # 市场匹配分析没有用户手动粘贴的 JD，这里把岗位画像 JSON 保存到 jd_text，
    # 方便历史报告仍然能追溯“这次分析基于什么市场数据”。
    report_id = save_report(
        target_role=payload.target_role,
        score=analysis.score,
        resume_text=payload.resume_text,
        jd_text=market_profile.model_dump_json(indent=2),
        markdown_report=markdown_report,
        raw_result=raw_result,
        parsed_result=parsed_result,
        parse_status=parse_status,
        parse_error=parse_error,
        model_name=settings.model,
        resume_version_id=payload.resume_version_id,
    )
    save_job_posts(report_id=report_id, posts=job_posts)
    logger.info("Saved market match report id=%s with %s job posts", report_id, len(job_posts))

    return MarketMatchResponse(
        market_profile=market_profile,
        analysis=analysis,
        markdown_report=markdown_report,
        report_id=report_id,
    )


def _build_insufficient_market_analysis(
    market_profile,
    trend_score: int | None,
    matched_skills: list[str],
    missing_skills: list[str],
) -> MarketResumeMatchAnalysis:
    """构造数据不足时的规则化结果，不把数据质量问题归咎于候选人。"""
    quality_message = (
        market_profile.data_quality.message
        if market_profile.data_quality is not None
        else "岗位样本不足，仅展示技能趋势。"
    )
    summary = (
        f"本次搜索得到 {market_profile.sample_count} 条候选岗位，其中 "
        f"{market_profile.valid_count} 条可确认仍在招聘，"
        f"{market_profile.likely_active_count} 条可能仍可投递，"
        f"{market_profile.unknown_count} 条发布时间或有效性待确认。"
        f"{quality_message}"
    )
    return MarketResumeMatchAnalysis(
        score=None,
        trend_score=trend_score,
        summary=summary,
        matched_market_skills=matched_skills,
        missing_market_skills=missing_skills,
    )


def _run_market_llm_analysis(
    payload: MarketMatchRequest,
    market_profile,
) -> tuple[MarketResumeMatchAnalysis, str, str, str | None]:
    """仅对达到数据质量门槛的市场画像调用模型并校验输出。"""
    analyst = Agent(
        role="计算机求职规划顾问",
        goal="根据简历和确认有效的岗位市场画像分析求职匹配度",
        backstory=load_prompt("market_match_analyst.md"),
        llm=build_llm(),
        verbose=True,
    )
    task = Task(
        description=f"""
候选人简历：

{payload.resume_text}

岗位市场画像：

{market_profile.model_dump_json(indent=2)}

请输出 MarketResumeMatchAnalysis JSON。
        """,
        expected_output="符合 MarketResumeMatchAnalysis 结构的 JSON",
        agent=analyst,
    )
    crew = Crew(
        agents=[analyst],
        tasks=[task],
        process=Process.sequential,
        verbose=True,
    )
    raw_result = str(crew.kickoff())
    parsed = extract_json_block(raw_result)

    if not parsed:
        logger.warning("Market match JSON parse failed")
        return (
            MarketResumeMatchAnalysis(
                score=None,
                summary="市场数据已满足分析条件，但模型未返回可解析的结构化结果，请稍后重试。",
            ),
            raw_result,
            "raw_only",
            "未能从模型输出中解析 JSON",
        )

    try:
        return (
            MarketResumeMatchAnalysis.model_validate(parsed),
            raw_result,
            "success",
            None,
        )
    except ValidationError as exc:
        logger.warning("Market match JSON validation failed: %s", exc)
        return (
            MarketResumeMatchAnalysis(
                score=None,
                summary="市场数据已满足分析条件，但模型输出字段不完整，当前不展示未经校验的结论。",
            ),
            raw_result,
            "validation_failed",
            str(exc),
        )


def render_market_match_report(
    market_profile,
    analysis: MarketResumeMatchAnalysis,
) -> str:
    data_sufficient = has_sufficient_market_data(market_profile)
    delivery_score_text = (
        f"**{analysis.delivery_score}/100**"
        if data_sufficient and analysis.delivery_score is not None
        else "**暂不评分**"
    )
    lines = [
        "# 岗位市场匹配分析报告",
        "",
        f"## 目标方向：{market_profile.target_role}",
        "",
        f"样本岗位数量：{market_profile.sample_count}",
        "",
        f"确认可投岗位：{market_profile.valid_count}",
        "",
        f"可能可投岗位：{market_profile.likely_active_count}",
        "",
        f"趋势参考样本：{market_profile.unknown_count}",
        "",
        f"趋势适配度：{analysis.trend_score if analysis.trend_score is not None else '样本不足，暂不评分'}",
        "",
        f"投递适配度：{delivery_score_text}",
        "",
    ]

    if not data_sufficient:
        lines.extend(
            [
                "## 数据质量提示",
                "",
                f"- {market_profile.data_quality.message if market_profile.data_quality else '岗位样本不足。'}",
                "- 本次仅展示方向相关技能趋势，不生成投递匹配评分或岗位推荐。",
                "",
            ]
        )

    lines.extend(
        [
            "## 匹配总览",
            "",
            analysis.summary,
            "",
            "## 搜索样本中的方向相关技能趋势",
            "",
            _render_list(market_profile.frequent_skills),
            "",
            "## 已覆盖技能",
            "",
            _render_list(analysis.matched_market_skills),
            "",
            "## 缺失技能",
            "",
            _render_list(analysis.missing_market_skills),
            "",
            "## 推荐投递岗位",
            "",
            _render_list(analysis.recommended_roles),
            "",
            "## 简历优化建议",
            "",
            _render_list(analysis.resume_improvement_suggestions),
            "",
            "## 投递策略",
            "",
            _render_list(analysis.delivery_strategy),
            "",
            "## 岗位来源",
            "",
            _render_list(market_profile.source_urls),
        ]
    )

    return "\n".join(lines)


def _render_list(items: list[str]) -> str:
    if not items:
        return "- 暂无"
    return "\n".join(f"- {item}" for item in items)
