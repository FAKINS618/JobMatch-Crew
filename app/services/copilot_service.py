"""将 CrewAI 多 Agent 求职匹配包装为可恢复、可渐进展示的副驾回合。"""

import logging

from crewai import Agent, Crew, Process, Task

from app.database import (
    get_turn_context,
    save_copilot_artifact,
    save_copilot_assistant_message,
    save_report,
    update_report_analysis,
    update_copilot_artifact,
    update_copilot_turn,
)
from app.report_parser import extract_json_block
from app.report_renderer import render_markdown_report
from app.jobmatch_crew import run_jobmatch_crew
from app.llm_factory import build_llm
from app.schemas import ActionPlanItem, JobMatchAnalysis, ScoreDimension
from app.services.market_profile_service import SKILL_KEYWORDS


logger = logging.getLogger(__name__)


def _build_fit_strategy(score: int | None, missing_skills: list[str]) -> dict:
    if score is None:
        return {
            "recommendation": "clarify",
            "title": "需要补充信息后再决定",
            "reason": "本次分析没有得到可验证的匹配结论。",
            "primary_action": "ask",
        }
    if score >= 70:
        return {
            "recommendation": "apply_now",
            "title": "建议立即投递",
            "reason": "核心能力已有较充分证据，投递前只需核对简历表述。",
            "primary_action": "accept",
        }
    if score >= 50:
        return {
            "recommendation": "tailor_then_apply",
            "title": "建议优化后投递",
            "reason": "具备基础匹配度，优先补足最关键的简历证据。",
            "primary_action": "create_task",
        }
    return {
        "recommendation": "improve_first",
        "title": "建议先补强再投递",
        "reason": "当前关键技能缺口较多，先完成一个可验证产出更有价值。",
        "primary_action": "create_task",
        "missing_skills": missing_skills[:3],
    }


def _assistant_summary(strategy: dict, missing_skills: list[str]) -> str:
    suffix = (
        f"优先处理：{'、'.join(missing_skills[:3])}。" if missing_skills else ""
    )
    return f"{strategy['title']}。{strategy['reason']}{suffix}"


def _build_rule_based_analysis(resume_text: str, jd_text: str) -> JobMatchAnalysis:
    """先生成秒级、可解释的基线，避免模型失败时用户只得到空白页。"""
    resume_lower = resume_text.lower()
    jd_lower = jd_text.lower()
    required_skills = [skill for skill in SKILL_KEYWORDS if skill.lower() in jd_lower]
    matched_skills = [skill for skill in required_skills if skill.lower() in resume_lower]
    missing_skills = [skill for skill in required_skills if skill not in matched_skills]
    denominator = max(len(required_skills), 1)
    score = round(len(matched_skills) / denominator * 100)
    dimensions = [
        ScoreDimension(
            name=skill,
            score=10 if skill in matched_skills else 0,
            max_score=10,
            evidence=[f"简历文本中出现“{skill}”"] if skill in matched_skills else [],
            suggestion="补充与该能力相关的真实项目产出。" if skill not in matched_skills else "保留并量化该能力的项目证据。",
        )
        for skill in required_skills[:8]
    ]
    summary = (
        f"基于岗位文本中识别出的 {len(required_skills)} 项技术要求，"
        f"当前简历直接覆盖 {len(matched_skills)} 项。"
    )
    if not required_skills:
        summary = "岗位文本未识别出足够的结构化技术要求，已先保留原文等待深度分析。"
    return JobMatchAnalysis(
        score=score,
        summary=summary,
        matched_skills=matched_skills,
        missing_skills=missing_skills,
        score_dimensions=dimensions,
        action_plan=[
            ActionPlanItem(
                day=index + 1,
                task=f"补强 {skill} 的项目或练习证据",
                output=f"一段可写入简历的 {skill} 项目说明或链接",
            )
            for index, skill in enumerate(missing_skills[:3])
        ],
    )


def _run_multi_agent_analysis(
    resume_text: str, jd_text: str, target_role: str
) -> JobMatchAnalysis | None:
    """运行 JD、简历、评分、报告四个 Agent 的顺序协作。"""
    _, raw_result = run_jobmatch_crew(
        resume_text=resume_text,
        jd_text=jd_text,
        target_role=target_role,
    )
    parsed = extract_json_block(raw_result)
    if not parsed:
        raise ValueError("多 Agent 报告没有返回可解析的 JSON")
    return JobMatchAnalysis.model_validate(parsed)


# 保留旧测试和内部调用的兼容名称，实际实现已经是四 Agent 协作。
_run_fast_agent_analysis = _run_multi_agent_analysis


def _run_follow_up_response(resume_text: str, report: dict, question: str) -> str:
    """基于当前活动报告回答追问，不要求用户重复粘贴岗位 JD。"""
    advisor = Agent(
        role="求职副驾追问顾问",
        goal="基于同一份岗位、简历和已有分析回答用户追问，并给出可执行建议",
        backstory="只能使用岗位和简历中的证据；不确定时明确说明，不得虚构经历。",
        llm=build_llm(),
        verbose=False,
    )
    prior_analysis = report.get("parsed_result") or report.get("markdown_report") or "暂无已保存分析"
    task = Task(
        description=f"""
当前目标岗位：{report.get('target_role') or '目标岗位'}

原始岗位 JD：
{str(report.get('jd_text') or '')[:8000]}

候选人简历：
{resume_text[:8000]}

上一轮结构化分析：
{str(prior_analysis)[:10000]}

用户追问：
{question}

请直接用中文回答追问。回答需要引用岗位或简历中的依据，并在结尾给出 1 到 3 个下一步动作。
不要要求用户重新粘贴岗位 JD，不要输出 JSON 或 Markdown 标题。
        """,
        expected_output="面向当前岗位上下文的中文追问回答",
        agent=advisor,
    )
    result = Crew(
        agents=[advisor],
        tasks=[task],
        process=Process.sequential,
        verbose=False,
    ).kickoff()
    answer = str(result).strip()
    if not answer:
        raise ValueError("追问 Agent 没有返回内容")
    return answer


def _evidence_payload(analysis: JobMatchAnalysis) -> dict:
    return {
        "items": [
            {
                "requirement": item.name,
                "score": item.score,
                "max_score": item.max_score,
                "evidence": item.evidence,
                "suggestion": item.suggestion,
                "status": "supported" if item.evidence else "missing_evidence",
            }
            for item in analysis.score_dimensions
        ]
    }


def _action_payload(analysis: JobMatchAnalysis) -> dict:
    return {
        "missing_skills": analysis.missing_skills,
        "resume_bullets": analysis.resume_bullets,
        "action_plan": [
            f"第 {item.day} 天：{item.task}；产出：{item.output}" for item in analysis.action_plan
        ],
    }


def run_copilot_turn(turn_id: int) -> None:
    """执行一个 JD 分析回合，并保存前端可直接渲染的产物卡片。"""
    context = get_turn_context(turn_id)
    if context is None:
        return

    session = context["session"]
    message = context["message"] or {}
    resume = context["resume"]
    jd_text = str(message.get("content") or "").strip()
    turn = context["turn"]
    active_report = context.get("report")

    update_copilot_turn(turn_id, status="running", stage="reading_resume", progress=10)
    if resume is None:
        save_copilot_artifact(
            turn_id,
            "job_brief",
            {
                "title": "请先选择一份已确认的简历版本",
                "summary": "副驾需要基于真实简历证据判断岗位匹配度。",
                "next_question": "你想使用哪一版简历分析这个岗位？",
            },
        )
        save_copilot_assistant_message(
            session["id"], turn_id, "我已收到岗位信息。请先选择一份已确认的简历版本。"
        )
        update_copilot_turn(turn_id, status="completed", stage="awaiting_resume", progress=100)
        return

    if turn.get("input_type") == "follow_up" and active_report is not None:
        update_copilot_turn(
            turn_id,
            status="running",
            stage="follow_up_analysis",
            progress=35,
            report_id=int(active_report["id"]),
        )
        try:
            answer = _run_follow_up_response(resume["raw_text"], active_report, jd_text)
        except Exception:
            logger.exception("Copilot follow-up analysis failed")
            save_copilot_assistant_message(
                session["id"], turn_id, "当前岗位追问暂时没有完成，请稍后重试。原岗位上下文仍已保留。"
            )
            update_copilot_turn(
                turn_id,
                status="failed",
                stage="follow_up_failed",
                progress=100,
                error_message="当前岗位追问失败，原岗位上下文仍已保留。",
                report_id=int(active_report["id"]),
            )
            return
        save_copilot_assistant_message(session["id"], turn_id, answer)
        update_copilot_turn(
            turn_id,
            status="completed",
            stage="follow_up_ready",
            progress=100,
            report_id=int(active_report["id"]),
        )
        return

    if len(jd_text) < 80:
        save_copilot_artifact(
            turn_id,
            "job_brief",
            {
                "title": "岗位信息不足",
                "summary": "请粘贴完整岗位职责和任职要求，或提供公开岗位链接。",
                "next_question": "这个岗位最重要的职责和技术要求是什么？",
            },
        )
        save_copilot_assistant_message(
            session["id"], turn_id, "请补充完整 JD，我再基于简历给出可执行建议。"
        )
        update_copilot_turn(turn_id, status="completed", stage="awaiting_job_details", progress=100)
        return

    job_brief = save_copilot_artifact(
        turn_id,
        "job_brief",
        {
            "title": session["target_role"] or resume["target_role"] or "目标岗位",
            "summary": "已收到岗位信息，正在拆解要求并核对简历中的真实证据。",
        },
        status="working",
    )
    save_copilot_assistant_message(
        session["id"],
        turn_id,
        "我已收到岗位信息，先核对简历证据，再给出是否值得投递的建议。",
    )
    baseline = _build_rule_based_analysis(resume["raw_text"], jd_text)
    report_id = save_report(
        target_role=session["target_role"] or resume["target_role"] or "计算机相关岗位",
        score=baseline.score,
        resume_text=resume["raw_text"],
        jd_text=jd_text,
        markdown_report=render_markdown_report(baseline),
        parsed_result=baseline.model_dump_json(),
        parse_status="copilot_rule_based",
        resume_version_id=resume["id"],
    )
    evidence_map = save_copilot_artifact(turn_id, "evidence_map", _evidence_payload(baseline))
    strategy = _build_fit_strategy(baseline.score, baseline.missing_skills)
    strategy_artifact = save_copilot_artifact(turn_id, "fit_strategy", strategy)
    action_bundle = save_copilot_artifact(turn_id, "action_bundle", _action_payload(baseline))
    update_copilot_artifact(
        job_brief["id"],
        {
            "title": session["target_role"] or resume["target_role"] or "目标岗位",
            "summary": baseline.summary,
            "match_score": baseline.score,
        },
    )
    update_copilot_turn(
        turn_id,
        status="running",
        stage="deepening_analysis",
        progress=60,
        report_id=report_id,
    )

    try:
        analysis = _run_fast_agent_analysis(
            resume["raw_text"],
            jd_text,
            session["target_role"] or resume["target_role"] or "计算机相关岗位",
        )
    except Exception as exc:
        logger.exception("Copilot multi-agent analysis failed")
        analysis = None

    if analysis is None:
        update_copilot_turn(
            turn_id,
            status="completed",
            stage="rule_based_ready",
            progress=100,
            error_message="多 Agent 深度分析暂不可用，已保留基础匹配结果。请检查模型配置或稍后重试。",
        )
        save_copilot_assistant_message(
            session["id"],
            turn_id,
            "我已完成基础证据对比。CrewAI 多 Agent 深度分析暂不可用，当前结果仍可用于决定是否补强或投递。",
        )
        return

    update_copilot_turn(turn_id, status="running", stage="building_recommendation", progress=85)
    strategy = _build_fit_strategy(analysis.score, analysis.missing_skills)

    update_copilot_artifact(
        job_brief["id"],
        {
            "title": session["target_role"] or resume["target_role"] or "目标岗位",
            "summary": analysis.summary,
            "match_score": analysis.score,
        },
    )
    update_copilot_artifact(evidence_map["id"], _evidence_payload(analysis))
    update_copilot_artifact(strategy_artifact["id"], strategy)
    update_copilot_artifact(action_bundle["id"], _action_payload(analysis))
    update_report_analysis(
        report_id,
        score=analysis.score,
        markdown_report=render_markdown_report(analysis),
        parsed_result=analysis.model_dump_json(),
        parse_status="copilot_multi_agent",
        raw_result=None,
    )
    save_copilot_assistant_message(
        session["id"], turn_id, _assistant_summary(strategy, analysis.missing_skills)
    )
    update_copilot_turn(
        turn_id,
        status="completed",
        stage="completed",
        progress=100,
    )
