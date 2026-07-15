import logging
import json

from app.report_renderer import render_markdown_report
from app.database import save_report
from app.jobmatch_crew import run_jobmatch_crew
from app.report_parser import extract_json_block, normalize_string_list
from app.schemas import JobMatchAnalysis, JobMatchRequest, JobMatchResponse


logger = logging.getLogger(__name__)


def generate_job_match_report(payload: JobMatchRequest) -> JobMatchResponse:
    """运行多 Agent 分析流程，并把生成结果保存到本地数据库。

    这是后端最核心的容错链路：
    1. 理想情况：直接拿到 JobMatchAnalysis，渲染 Markdown 并保存。
    2. 当前兼容方案：从 LLM 原始文本中提取 JSON，再用 Pydantic 本地校验。
    3. 兜底方案：如果结构化校验失败，尽量读取旧版 JSON 字段。
    4. 最低兜底：如果 JSON 都提取失败，保存原始输出，避免接口直接崩溃。

    这样做是为了应对 LLM 输出不稳定、模型接口不支持 response_format 等情况。
    """
    analysis, raw_result = run_jobmatch_crew(
        resume_text=payload.resume_text,
        jd_text=payload.jd_text,
        target_role=payload.target_role,
    )

    # 预留主路径：如果未来换成支持 response_format 的模型，
    # run_jobmatch_crew 可以直接返回 JobMatchAnalysis。
    if analysis is not None:
        markdown_report = render_markdown_report(analysis)

        report_id = save_report(
            target_role=payload.target_role,
            score=analysis.score,
            resume_text=payload.resume_text,
            jd_text=payload.jd_text,
            markdown_report=markdown_report,
            raw_result=raw_result,
            parsed_result=analysis.model_dump_json(),
            parse_status="pydantic_success",
            resume_version_id=payload.resume_version_id,
        )

        return JobMatchResponse(
            report_id=report_id,
            score=analysis.score,
            matched_skills=analysis.matched_skills,
            missing_skills=analysis.missing_skills,
            interview_questions=[item.question for item in analysis.interview_questions],
            action_plan=[
                f"第 {item.day} 天：{item.task}；产出：{item.output}"
                for item in analysis.action_plan
            ],
            markdown_report=markdown_report,
            analysis=analysis,
        )

    # 当前主路径：模型输出 JSON 文本，本地负责提取和校验。
    parsed = extract_json_block(raw_result)

    if parsed:
        try:
            analysis = JobMatchAnalysis.model_validate(parsed)
        except Exception as exc:
            logger.warning("Fallback JSON does not match JobMatchAnalysis: %s", exc)
        else:
            markdown_report = render_markdown_report(analysis)
            report_id = save_report(
                target_role=payload.target_role,
                score=analysis.score,
                resume_text=payload.resume_text,
                jd_text=payload.jd_text,
                markdown_report=markdown_report,
                raw_result=raw_result,
                parsed_result=analysis.model_dump_json(),
                parse_status="fallback_pydantic_success",
                resume_version_id=payload.resume_version_id,
            )
            logger.info("Saved fallback-pydantic report id=%s", report_id)

            return JobMatchResponse(
                report_id=report_id,
                score=analysis.score,
                matched_skills=analysis.matched_skills,
                missing_skills=analysis.missing_skills,
                interview_questions=[
                    item.question for item in analysis.interview_questions
                ],
                action_plan=[
                    f"第 {item.day} 天：{item.task}；产出：{item.output}"
                    for item in analysis.action_plan
                ],
                markdown_report=markdown_report,
                analysis=analysis,
            )

    # raw-only 兜底：没有任何可解析 JSON 时，不向用户暴露原始模型文本。
    # raw_result 仍会保存到数据库，方便开发时排查模型输出问题。
    if not parsed:
        markdown_report = build_degraded_markdown_report()
        report_id = save_report(
            target_role=payload.target_role,
            score=None,
            resume_text=payload.resume_text,
            jd_text=payload.jd_text,
            markdown_report=markdown_report,
            raw_result=raw_result,
            parse_status="raw_only",
            resume_version_id=payload.resume_version_id,
        )
        logger.info("Saved raw-only report id=%s", report_id)
        return JobMatchResponse(report_id=report_id, markdown_report=markdown_report)

    # 旧版 JSON 虽然被提取到，但没有通过 JobMatchAnalysis 校验。这里渲染
    # 可控的降级报告，而不是把整段 JSON 或模型解释文本当作 Markdown 返给前端。
    markdown_report = build_degraded_markdown_report(parsed)
    score = parsed.get("score") if isinstance(parsed.get("score"), int) else None

    # 旧版 fallback：兼容以前模型输出的 score/matched_skills/markdown_report 字段。
    report_id = save_report(
        target_role=payload.target_role,
        score=score,
        resume_text=payload.resume_text,
        jd_text=payload.jd_text,
        markdown_report=markdown_report,
        raw_result=raw_result,
        parsed_result=json.dumps(parsed, ensure_ascii=False),
        parse_status="fallback_json",
        resume_version_id=payload.resume_version_id,
    )
    logger.info("Saved fallback-json report id=%s", report_id)

    return JobMatchResponse(
        report_id=report_id,
        score=score,
        matched_skills=normalize_string_list(parsed.get("matched_skills", [])),
        missing_skills=normalize_string_list(parsed.get("missing_skills", [])),
        interview_questions=normalize_string_list(
            parsed.get("interview_questions", [])
        ),
        action_plan=normalize_string_list(parsed.get("action_plan", [])),
        markdown_report=markdown_report,
        analysis=None,
    )


def build_degraded_markdown_report(parsed: dict | None = None) -> str:
    """把可识别的旧字段渲染成安全的降级报告。

    原始 LLM 文本可能是半截 JSON、模型思考内容或不符合 UI 契约的字段，
    因而只保留已经识别的摘要、技能和行动项；原文本仍保存于数据库调试字段。
    """
    if not parsed:
        return (
            "# 求职匹配分析报告\n\n"
            "> 本次模型输出未能完成结构化解析，系统未展示原始内容。\n\n"
            "请检查输入后重新生成报告。"
        )

    lines = [
        "# 求职匹配分析报告（降级结果）",
        "",
        "> 本次模型输出字段不完整，以下内容仅展示系统可安全识别的部分。",
        "",
    ]

    score = parsed.get("score")
    if isinstance(score, int):
        lines.extend([f"**参考评分：{score}/100**", ""])

    summary = parsed.get("summary")
    if isinstance(summary, str) and summary.strip():
        lines.extend(["## 匹配总览", "", summary.strip(), ""])

    sections = [
        ("已匹配技能", normalize_string_list(parsed.get("matched_skills", []))),
        ("待补强技能", normalize_string_list(parsed.get("missing_skills", []))),
        ("面试问题", normalize_string_list(parsed.get("interview_questions", []))),
        ("行动计划", normalize_string_list(parsed.get("action_plan", []))),
    ]
    for title, items in sections:
        if items:
            lines.extend([f"## {title}", ""])
            lines.extend(f"- {item}" for item in items)
            lines.append("")

    if len(lines) == 4:
        lines.extend(["系统无法从本次输出中恢复可靠结论，请重新生成报告。", ""])

    return "\n".join(lines)
