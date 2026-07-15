"""Streamlit 页面模块。
1. 工作台：提交一次新的 JD 或市场匹配分析；
2. 结果渲染：优先显示结论，再按需查看细节；
3. 资料区：查看历史报告、岗位来源和岗位技能图谱。
"""
from __future__ import annotations
import json
from typing import Any
import streamlit as st
from api_client import (
    create_action_evidence,
    create_action_items_from_report,
    create_job_match_report,
    create_job_target,
    create_market_match_task,
    get_dashboard_summary,
    get_report_detail,
    get_task_detail,
    list_action_items,
    list_job_targets,
    list_reports,
    list_resume_versions,
    parse_resume,
    save_resume_version,
    search_jobs,
    update_action_item,
    update_job_target,
)
from ui_helpers import format_tags, get_role_core_skills


# session_state 会在 Streamlit 每次重跑时保留。
# 使用固定 key 保存“最近一次结果”和“正在执行的异步任务”，结果才能始终出现在页面顶部。
WORKBENCH_RESULT_KEY = "workbench_result"
MARKET_TASK_ID_KEY = "market_task_id"
MARKET_TASK_DETAIL_KEY = "market_task_detail"
HISTORY_DETAIL_KEY = "history_report_detail"
CUSTOM_ROLE_OPTION = "其他计算机岗位（自定义）"
COPILOT_MESSAGES_KEY = "copilot_messages"
COPILOT_RESUME_KEY = "copilot_resume_text"
COPILOT_JD_KEY = "copilot_jd_text"
COPILOT_RESULT_KEY = "copilot_result"


def _classify_copilot_message(text: str) -> str:
    """用可解释规则区分用户粘贴的简历、JD 和求职意向。"""
    normalized = text.lower()
    jd_markers = ("岗位职责", "任职要求", "岗位要求", "职位描述", "招聘", "jd", "job description")
    resume_markers = ("项目经历", "教育经历", "工作经历", "实习经历", "专业技能", "github", "gpa")
    if any(marker in normalized for marker in jd_markers):
        return "jd"
    if any(marker in normalized for marker in resume_markers) or len(text) >= 300:
        return "resume"
    return "intent"


def _infer_preferences(text: str, roles: list[str]) -> tuple[str | None, str | None]:
    """从自然语言输入中提取已知岗位方向和常见城市，不做不确定推断。"""
    role = next((item for item in roles if item.lower() in text.lower()), None)
    city = next(
        (item for item in ("北京", "上海", "深圳", "杭州", "广州", "成都", "南京", "武汉", "不限") if item in text),
        None,
    )
    return role, city


def _init_workbench_state() -> None:
    """初始化工作台需要的会话状态，避免首次访问时 KeyError。"""
    st.session_state.setdefault(WORKBENCH_RESULT_KEY, None)
    st.session_state.setdefault(MARKET_TASK_ID_KEY, None)
    st.session_state.setdefault(MARKET_TASK_DETAIL_KEY, None)


def render_job_preferences(roles: list[str]) -> tuple[str, str]:
    """在主流程中收集求职意向，而不是把关键输入藏在侧栏。

    target_role 同时驱动岗位搜索、技能画像、知识检索和后续投递建议；
    自定义方向允许项目覆盖内置画像之外的计算机岗位。
    """
    st.subheader("求职意向")
    st.caption("先选择目标方向与城市，系统将据此搜索岗位并生成补强建议。")

    role_col, city_col = st.columns([3, 2])
    role_options = [*roles, CUSTOM_ROLE_OPTION]
    with role_col:
        selected_role = st.selectbox(
            "目标岗位方向",
            role_options,
            key="selected_role_option",
        )
        if selected_role == CUSTOM_ROLE_OPTION:
            target_role = st.text_input(
                "自定义岗位方向",
                placeholder="例如：C++ 后端开发实习、Android 开发实习",
                key="custom_target_role",
            ).strip()
        else:
            target_role = selected_role
    with city_col:
        city = st.text_input("目标城市", key="target_city").strip()

    # 侧栏只读取这两个状态值，避免侧栏与主页面拥有两套会互相覆盖的输入。
    st.session_state["target_role"] = target_role or "计算机相关岗位"
    return st.session_state["target_role"], city


def render_copilot_workspace(roles: list[str]) -> None:
    """以一条自然语言输入承接简历、JD 和求职意向，再调用既有智能体。"""
    st.session_state.setdefault(COPILOT_MESSAGES_KEY, [])
    st.session_state.setdefault(COPILOT_RESUME_KEY, "")
    st.session_state.setdefault(COPILOT_JD_KEY, "")
    st.session_state.setdefault(COPILOT_RESULT_KEY, None)

    st.subheader("AI 求职助手")
    st.caption("直接发简历、岗位 JD 或求职目标；助手会识别上下文并组织下一步。")

    resume_text = st.session_state[COPILOT_RESUME_KEY]
    jd_text = st.session_state[COPILOT_JD_KEY]
    resume_col, jd_col, intent_col = st.columns(3)
    resume_col.metric("简历上下文", "已识别" if resume_text else "等待输入")
    jd_col.metric("岗位上下文", "已识别" if jd_text else "等待输入")
    intent_col.metric("当前方向", st.session_state.get("target_role") or "待确认")

    with st.expander("补充文件或清空本次对话", expanded=False):
        uploaded_file = st.file_uploader(
            "上传简历（txt / md）",
            type=["txt", "md"],
            key="copilot_resume_file",
        )
        if uploaded_file is not None:
            upload_token = f"{uploaded_file.name}:{uploaded_file.size}"
            if st.session_state.get("copilot_upload_token") != upload_token:
                st.session_state[COPILOT_RESUME_KEY] = uploaded_file.getvalue().decode(
                    "utf-8", errors="ignore"
                )
                st.session_state["copilot_upload_token"] = upload_token
                st.session_state[COPILOT_MESSAGES_KEY].append(
                    {"role": "assistant", "content": "我已读取简历文件。现在可以继续粘贴目标 JD，或让我做市场岗位分析。"}
                )
                st.rerun()
        if st.button("开始新的对话", key="clear_copilot_context"):
            for key in (
                COPILOT_MESSAGES_KEY,
                COPILOT_RESUME_KEY,
                COPILOT_JD_KEY,
                COPILOT_RESULT_KEY,
                "copilot_upload_token",
            ):
                st.session_state.pop(key, None)
            st.rerun()

    messages = st.session_state[COPILOT_MESSAGES_KEY]
    if not messages:
        with st.chat_message("assistant"):
            st.write("把简历、招聘 JD 或目标意向直接发给我。我会先识别内容，再启动匹配、市场搜索或下一步行动。")
    for message in messages:
        with st.chat_message(message["role"]):
            st.write(message["content"])

    user_message = st.chat_input("例如：这是我的简历… / 这是岗位 JD… / 我想在北京找 Python 后端实习")
    if user_message:
        text = user_message.strip()
        if not text:
            st.stop()
        messages.append({"role": "user", "content": text})
        message_type = _classify_copilot_message(text)
        role, city = _infer_preferences(text, roles)
        if role:
            st.session_state["target_role"] = role
            st.session_state["selected_role_option"] = role
        if city:
            st.session_state["target_city"] = "" if city == "不限" else city

        if message_type == "resume":
            st.session_state[COPILOT_RESUME_KEY] = text
            reply = "我已把这段内容识别为简历。继续发目标岗位 JD，我会做逐条证据匹配；也可以直接说“帮我找市场岗位”。"
        elif message_type == "jd":
            st.session_state[COPILOT_JD_KEY] = text
            reply = "我已把这段内容识别为岗位 JD。若简历已就绪，可以立即启动多智能体匹配。"
        else:
            detected = []
            if role:
                detected.append(f"目标方向已更新为“{role}”")
            if city:
                detected.append(f"目标城市已更新为“{city}”")
            reply = "；".join(detected) if detected else "我记录了你的求职意向。现在发送简历或 JD，我会继续补齐分析上下文。"
        messages.append({"role": "assistant", "content": reply})
        st.rerun()

    resume_text = st.session_state[COPILOT_RESUME_KEY]
    jd_text = st.session_state[COPILOT_JD_KEY]
    target_role = st.session_state.get("target_role") or roles[0]
    city = st.session_state.get("target_city", "")
    action_col, secondary_col = st.columns(2)
    with action_col:
        can_match = len(resume_text.strip()) >= 80 and len(jd_text.strip()) >= 80
        if st.button(
            "启动 JD 智能体匹配",
            type="primary",
            use_container_width=True,
            disabled=not can_match,
            key="copilot_start_job_match",
        ):
            try:
                with st.spinner("智能体正在提取证据、评估匹配度并生成行动建议..."):
                    result = create_job_match_report(
                        {
                            "resume_text": resume_text,
                            "jd_text": jd_text,
                            "target_role": target_role,
                        }
                    )
            except Exception as exc:
                st.error(f"智能体匹配失败：{exc}")
            else:
                st.session_state[COPILOT_RESULT_KEY] = {"kind": "job", "data": result}
                messages.append(
                    {"role": "assistant", "content": "匹配报告已生成。我已把技能缺口、简历修改和下一步行动整理在下方。"}
                )
                st.rerun()
    with secondary_col:
        can_search = len(resume_text.strip()) >= 80
        if st.button(
            "让智能体探索市场岗位",
            use_container_width=True,
            disabled=not can_search,
            key="copilot_start_market_match",
        ):
            try:
                task = create_market_match_task(
                    {
                        "resume_text": resume_text,
                        "target_role": target_role,
                        "city": city,
                        "max_results": 8,
                    }
                )
            except Exception as exc:
                st.error(f"市场分析任务创建失败：{exc}")
            else:
                st.session_state[MARKET_TASK_ID_KEY] = task["task_id"]
                st.session_state[MARKET_TASK_DETAIL_KEY] = task
                messages.append(
                    {"role": "assistant", "content": "我已开始检索和验证岗位。完成后会把可投岗位与补强优先级带回这里。"}
                )
                st.rerun()

    _render_market_task_status()
    market_result = st.session_state.get(WORKBENCH_RESULT_KEY)
    if market_result and market_result.get("kind") == "market":
        _render_market_match_result(
            market_result.get("data") or {}, download_key="copilot_market_match_download"
        )
    result = st.session_state.get(COPILOT_RESULT_KEY)
    if result and result.get("kind") == "job":
        _render_job_match_result(
            result.get("data") or {}, download_key="copilot_job_match_download"
        )


def _as_string_list(value: Any) -> list[str]:
    """将后端返回的未知列表值安全转换成可展示的字符串列表。"""
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item]


def _load_json_object(value: Any) -> dict[str, Any]:
    """解析数据库保存的 JSON 字符串；解析失败时返回空字典而不是中断页面。"""
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}

    return parsed if isinstance(parsed, dict) else {}


def _show_list(items: list[str], empty_text: str = "暂无") -> None:
    """统一展示短文本列表，避免每个页面重复写空状态判断。"""
    if not items:
        st.caption(empty_text)
        return
    for item in items:
        st.markdown(f"- {item}")


def _score_label(score: Any) -> str:
    """保留 0 分，避免 `score or` 把 0 误显示为“未解析”。"""
    return str(score) if score is not None else "未解析"


def _is_complete_job_analysis(analysis: dict[str, Any]) -> bool:
    """判断结果能否作为结构化报告展示，避免把原始 JSON 当作用户报告。"""
    return isinstance(analysis.get("score"), int) and bool(analysis.get("summary"))


def _safe_markdown_report(value: Any, fallback_message: str) -> str:
    """阻止旧记录中的原始 JSON 直接出现在面向用户的完整报告页。"""
    report = str(value or "").strip()
    if _looks_like_raw_model_json(report):
        return f"# 报告暂不可用\n\n{fallback_message}"
    return report


def _looks_like_raw_model_json(value: Any) -> bool:
    """识别旧版 fallback 保存的 JSON 文本，防止它被 st.markdown 直接渲染。"""
    report = str(value or "").strip()
    return not report or report.startswith("{") or report.startswith("```json")


def _render_degraded_job_report(data: dict[str, Any], download_key: str) -> None:
    """展示可理解的降级状态，原始模型输出只保存在后端调试字段中。"""
    st.subheader("本次简历匹配结果")
    st.warning("本次结果未完成结构化校验，已隐藏原始模型输出。请重新生成报告。")
    markdown_report = _safe_markdown_report(
        data.get("markdown_report"),
        "系统没有获得可展示的结构化结论，本次报告仅保留了调试记录。",
    )
    st.markdown(markdown_report)
    st.download_button(
        label="下载 Markdown 报告",
        data=markdown_report,
        file_name="jobmatch_report.md",
        mime="text/markdown",
        use_container_width=True,
        key=download_key,
    )


def _render_job_match_result(data: dict[str, Any], download_key: str) -> None:
    """把简历 + JD 分析结果组织为“概览优先、详情按需展开”的布局。"""
    analysis = data.get("analysis") or {}
    if not isinstance(analysis, dict):
        analysis = {}

    if not _is_complete_job_analysis(analysis) or _looks_like_raw_model_json(
        data.get("markdown_report")
    ):
        _render_degraded_job_report(data, download_key=download_key)
        return

    matched_skills = _as_string_list(
        analysis.get("matched_skills") or data.get("matched_skills")
    )
    missing_skills = _as_string_list(
        analysis.get("missing_skills") or data.get("missing_skills")
    )
    score = analysis.get("score", data.get("score"))
    report_id = data.get("report_id")

    st.subheader("本次简历匹配结果")
    metric_col1, metric_col2, metric_col3, metric_col4 = st.columns(4)
    metric_col1.metric("匹配评分", _score_label(score))
    metric_col2.metric("已匹配技能", len(matched_skills))
    metric_col3.metric("待补强技能", len(missing_skills))
    metric_col4.metric(
        "面试题数量",
        len(analysis.get("interview_questions") or data.get("interview_questions") or []),
    )

    # 结果详情使用标签页承载。用户打开页面先看到结论，不必先滚过一整段 Markdown 报告。
    overview_tab, score_tab, resume_tab, interview_tab, plan_tab, report_tab = st.tabs(
        ["核心结论", "评分依据", "简历优化", "面试准备", "行动计划", "完整报告"]
    )

    with overview_tab:
        summary = analysis.get("summary")
        if summary:
            st.markdown("#### 核心结论")
            st.write(summary)

        matched_col, missing_col = st.columns(2)
        with matched_col:
            st.markdown("#### 已覆盖技能")
            st.markdown(format_tags(matched_skills))
        with missing_col:
            st.markdown("#### 优先补强")
            st.markdown(format_tags(missing_skills))

        risk_points = _as_string_list(analysis.get("risk_points"))
        if risk_points:
            st.markdown("#### 风险点")
            _show_list(risk_points)

    with score_tab:
        score_dimensions = analysis.get("score_dimensions") or []
        if not score_dimensions:
            st.info("本次报告暂未返回分项评分，请查看完整报告。")
        else:
            for item in score_dimensions:
                if not isinstance(item, dict):
                    continue
                name = item.get("name", "未命名维度")
                dimension_score = item.get("score", 0)
                max_score = item.get("max_score", 1) or 1
                st.markdown(f"**{name}：{dimension_score}/{max_score}**")
                st.progress(min(float(dimension_score) / float(max_score), 1.0))

                evidence = _as_string_list(item.get("evidence"))
                if evidence:
                    st.caption("评分依据")
                    _show_list(evidence)
                if item.get("suggestion"):
                    st.info(str(item["suggestion"]))

    with resume_tab:
        st.markdown("#### 可直接改写的简历 Bullet")
        _show_list(_as_string_list(analysis.get("resume_bullets")))

    with interview_tab:
        questions = analysis.get("interview_questions") or []
        if not questions:
            _show_list(_as_string_list(data.get("interview_questions")))
        for item in questions:
            if isinstance(item, dict):
                st.markdown(f"- **{item.get('question', '')}**")
                st.caption(
                    f"考察技能：{item.get('skill', '')}；"
                    f"原因：{item.get('reason', '')}"
                )

    with plan_tab:
        action_plan = analysis.get("action_plan") or []
        if action_plan and isinstance(action_plan[0], dict):
            for item in action_plan:
                st.markdown(
                    f"- 第 {item.get('day')} 天：{item.get('task')}；"
                    f"产出：{item.get('output', '')}"
                )
        else:
            _show_list(_as_string_list(data.get("action_plan")))
        if report_id and missing_skills:
            selected_skills = st.multiselect(
                "选择要加入成长计划的技能",
                missing_skills,
                key=f"job_gap_skills_{report_id}_{download_key}",
            )
            if st.button(
                "加入成长计划",
                key=f"create_job_actions_{report_id}_{download_key}",
                disabled=not selected_skills,
            ):
                try:
                    created_items = create_action_items_from_report(
                        int(report_id), {"skills": selected_skills}
                    )
                except Exception as exc:
                    st.error(f"创建成长任务失败：{exc}")
                else:
                    st.success(f"已加入 {len(created_items)} 项成长任务。")

    with report_tab:
        markdown_report = str(data.get("markdown_report") or "")
        st.markdown(markdown_report)
        st.download_button(
            label="下载 Markdown 报告",
            data=markdown_report,
            file_name="jobmatch_report.md",
            mime="text/markdown",
            use_container_width=True,
            key=download_key,
        )


def _is_market_data_sufficient(profile: dict[str, Any]) -> bool:
    """与后端一致：满足样本量、技能和数据质量门槛才展示市场评分。"""
    quality = profile.get("data_quality") or {}
    return (
        int(profile.get("valid_count") or 0) >= 3
        and len(_as_string_list(profile.get("frequent_skills"))) >= 3
        and quality.get("level") in {"high", "medium"}
    )


def _is_trend_data_sufficient(profile: dict[str, Any]) -> bool:
    """趋势评分允许使用方向相关但日期待确认的岗位样本。"""
    return (
        int(profile.get("relevant_count") or 0) >= 3
        and len(_as_string_list(profile.get("frequent_skills"))) >= 3
    )


def _market_quality_label(profile: dict[str, Any]) -> str:
    """展示岗位数据质量，不把未知发布时间误说成中等可信度。"""
    quality = profile.get("data_quality") or {}
    labels = {
        "high": "数据充足",
        "medium": "有限可用",
        "low": "样本不足",
    }
    return labels.get(str(quality.get("level")), "样本不足")


def _job_status_label(value: Any) -> str:
    """把岗位状态转换为中文，原始状态值仍保留在数据库中用于后续统计。"""
    labels = {
        "active": "有效",
        "likely_active": "可能可投，需确认",
        "expired": "已失效",
        "unknown": "时效待确认",
    }
    return labels.get(str(value), "时效待确认")


def _job_freshness_label(value: Any) -> str:
    """将数值时效分转成用户可理解的等级，而不是直接显示 0.5 之类的内部评分。"""
    try:
        score = float(value)
    except (TypeError, ValueError):
        return "时效待确认"

    if score >= 0.8:
        return "较新"
    if score >= 0.5:
        return "时效待确认"
    return "时效偏低"


def _render_market_match_result(data: dict[str, Any], download_key: str) -> None:
    """展示市场匹配结果和岗位样本，突出数据时效性与来源追溯。"""
    profile = data.get("market_profile") or {}
    analysis = data.get("analysis") or {}
    job_posts = data.get("job_posts") or []
    report_id = data.get("report_id")
    data_sufficient = _is_market_data_sufficient(profile)
    trend_sufficient = _is_trend_data_sufficient(profile)

    st.subheader("本次岗位市场匹配结果")
    metric_col1, metric_col2, metric_col3, metric_col4 = st.columns(4)
    metric_col1.metric(
        "趋势适配度",
        _score_label(analysis.get("trend_score")) if trend_sufficient else "暂不评分",
    )
    metric_col2.metric(
        "投递适配度",
        _score_label(analysis.get("delivery_score")) if data_sufficient else "暂不评分",
    )
    metric_col3.metric("搜索候选", profile.get("sample_count", len(job_posts)))
    metric_col4.metric("确认可投", profile.get("valid_count", 0))
    st.caption(
        f"方向相关：{profile.get('relevant_count', 0)} | "
        f"可能可投：{profile.get('likely_active_count', 0)} | "
        f"数据质量：{_market_quality_label(profile)} | "
        f"已失效：{profile.get('expired_count', 0)}"
    )

    if not data_sufficient:
        quality = profile.get("data_quality") or {}
        st.warning(
            f"样本不足：{quality.get('message') or '未获取到足够的确认可投岗位。'}"
            "投递适配度和 A/B/C 推荐暂不生成。"
        )

    overview_tab, recommendation_tab, gap_tab, strategy_tab, source_tab, report_tab  = st.tabs(
        ["市场结论", "岗位推荐","技能差距", "投递策略", "岗位来源", "完整报告"]
    )

    with recommendation_tab:
        recommendations = analysis.get("job_recommendations", [])

        if not recommendations:
            likely_posts = [
                post for post in job_posts
                if isinstance(post, dict) and post.get("status") == "likely_active"
            ]
            if likely_posts:
                st.info("以下岗位可能仍可投递，请打开原链接确认招聘状态后再投递。")
                for post in likely_posts:
                    title = post.get("title") or "未命名岗位"
                    st.markdown(f"#### 待确认 | {title}")
                    st.caption(post.get("verification_reason") or "详情页需人工确认")
                    if post.get("url"):
                        st.markdown(f"[打开岗位原链接确认]({post['url']})")
            else:
                st.info("本次没有足够的确认可投岗位，暂不提供 A/B/C 投递推荐。")
        else:
            for item in recommendations:
                level = item.get("level", "C")
                st.markdown(
                    f"### {level} 类 | {item.get('title', '未命名岗位')} "
                    f"| {item.get('match_score', 0)} 分"
                )
                st.caption(item.get("freshness_label", "时效待确认"))
                st.write(item.get("reason", ""))
                st.markdown(f"已覆盖：{format_tags(item.get('matched_skills', []))}")
                st.markdown(f"待补强：{format_tags(item.get('missing_skills', []))}")
                if item.get("url"):
                    st.markdown(f"[查看岗位原链接]({item['url']})")
                if report_id and item.get("url"):
                    if st.button(
                        "加入投递管道",
                        key=f"create_target_{report_id}_{item.get('url')}",
                    ):
                        try:
                            create_job_target(
                                {
                                    "report_id": int(report_id),
                                    "url": item["url"],
                                    "priority": level,
                                }
                            )
                        except Exception as exc:
                            st.error(f"加入投递管道失败：{exc}")
                        else:
                            st.success("岗位已加入投递管道。")

    with overview_tab:
        if analysis.get("summary"):
            st.markdown("#### 市场数据说明")
            st.write(analysis["summary"])
        trend_title = "方向相关技能趋势" if not data_sufficient else "市场高频技能"
        st.markdown(f"#### {trend_title}")
        st.markdown(format_tags(_as_string_list(profile.get("frequent_skills"))))
        if trend_sufficient:
            st.markdown("#### 已覆盖技能")
            st.markdown(
                format_tags(_as_string_list(analysis.get("matched_market_skills")))
            )
        else:
            st.info("方向相关岗位样本仍不足，暂不计算趋势适配度。")

    with gap_tab:
        if not trend_sufficient:
            st.info("需获得足够的方向相关岗位样本后，才能生成趋势技能缺口。")
        else:
            st.markdown("#### 趋势待补强技能")
            st.markdown(
                format_tags(_as_string_list(analysis.get("missing_market_skills")))
            )
            if data_sufficient:
                st.markdown("#### 简历优化建议")
                _show_list(_as_string_list(analysis.get("resume_improvement_suggestions")))
            missing_skills = _as_string_list(analysis.get("missing_market_skills"))
            if report_id and missing_skills:
                selected_skills = st.multiselect(
                    "选择要加入成长计划的技能",
                    missing_skills,
                    key=f"market_gap_skills_{report_id}_{download_key}",
                )
                if st.button(
                    "加入成长计划",
                    key=f"create_actions_{report_id}_{download_key}",
                    disabled=not selected_skills,
                ):
                    try:
                        created_items = create_action_items_from_report(
                            int(report_id), {"skills": selected_skills}
                        )
                    except Exception as exc:
                        st.error(f"创建成长任务失败：{exc}")
                    else:
                        st.success(f"已加入 {len(created_items)} 项成长任务。")

    with strategy_tab:
        if not data_sufficient:
            st.info("当前数据不足以支撑投递策略，请先在岗位来源中确认招聘状态。")
        else:
            st.markdown("#### 推荐投递方向")
            _show_list(_as_string_list(analysis.get("recommended_roles")))
            st.markdown("#### 投递策略")
            _show_list(_as_string_list(analysis.get("delivery_strategy")))

    with source_tab:
        fetched_at = profile.get("fetched_at")
        if fetched_at:
            st.caption(f"数据抓取时间：{fetched_at}")

        if not job_posts:
            source_urls = _as_string_list(profile.get("source_urls"))
            if source_urls:
                for url in source_urls:
                    st.markdown(f"- [查看岗位来源]({url})")
            else:
                st.info("本次分析没有可展示的岗位来源。")

        for post in job_posts:
            if not isinstance(post, dict):
                continue
            title = post.get("title") or "未命名岗位"
            with st.expander(title):
                st.caption(
                    f"状态：{_job_status_label(post.get('status'))} | "
                    f"时效：{_job_freshness_label(post.get('freshness_score'))} | "
                    f"相关度：{post.get('relevance_score', 0)} | "
                    f"来源：{post.get('source', 'unknown')}"
                )
                if post.get("verification_reason"):
                    st.caption(f"验证：{post['verification_reason']}")
                content = post.get("content") or ""
                if content:
                    st.write(str(content)[:600])
                if post.get("url"):
                    st.markdown(f"[查看岗位来源]({post['url']})")

    with report_tab:
        markdown_report = _safe_markdown_report(
            data.get("markdown_report"),
            "市场报告未能完成结构化解析，请重新发起分析。",
        )
        st.markdown(markdown_report)
        st.download_button(
            label="下载 Markdown 报告",
            data=markdown_report,
            file_name="market_match_report.md",
            mime="text/markdown",
            use_container_width=True,
            key=download_key,
        )


def _restore_market_result(report_detail: dict[str, Any]) -> dict[str, Any] | None:
    """从数据库的报告详情恢复市场分析的结构化展示数据。

    市场任务完成后，任务表只保存 report_id。这里读取 reports 表的
    `jd_text`（市场画像 JSON）和 `parsed_result`（LLM 校验后的 JSON），
    让异步任务完成后的页面仍能展示仪表盘，而不只是长 Markdown。
    """
    profile = _load_json_object(report_detail.get("jd_text"))
    analysis = _load_json_object(report_detail.get("parsed_result"))
    if not profile or not analysis:
        return None

    return {
        "market_profile": profile,
        "analysis": analysis,
        "markdown_report": report_detail.get("markdown_report", ""),
        "job_posts": report_detail.get("job_posts", []),
        "report_id": report_detail.get("id"),
    }


def _render_market_task_status() -> None:
    """渲染市场任务的可刷新状态，并在成功后把数据库报告恢复成工作台结果。"""
    task_id = st.session_state.get(MARKET_TASK_ID_KEY)
    if not task_id:
        return

    task = st.session_state.get(MARKET_TASK_DETAIL_KEY) or {}
    st.subheader("岗位市场分析任务")
    status_col, progress_col = st.columns([1, 3])
    status_col.metric("当前状态", task.get("status") or "pending")
    progress = int(task.get("progress") or 0)
    progress_col.progress(progress, text=f"任务进度：{progress}%")
    st.caption(f"任务 ID：{task_id}")

    if st.button("刷新任务状态", use_container_width=True, key="refresh_market_task"):
        try:
            task = get_task_detail(int(task_id))
        except Exception as exc:
            st.error(f"任务状态查询失败：{exc}")
            return
        st.session_state[MARKET_TASK_DETAIL_KEY] = task

    # 无论本次是否点击刷新，都用 session 中最新的任务状态继续渲染。
    task = st.session_state.get(MARKET_TASK_DETAIL_KEY) or {}
    status = task.get("status") or "pending"

    if status == "failed":
        st.error(task.get("error_message") or "任务执行失败")
        return

    if status == "success" and task.get("report_id"):
        try:
            report_detail = get_report_detail(int(task["report_id"]))
        except Exception as exc:
            st.error(f"读取分析报告失败：{exc}")
            return

        restored_result = _restore_market_result(report_detail)
        if restored_result is None:
            # 结构化字段缺失时仍保留一份可查看的完整报告，避免任务成功后页面空白。
            restored_result = {
                "market_profile": {},
                "analysis": {"score": report_detail.get("score")},
                "markdown_report": report_detail.get("markdown_report", ""),
                "job_posts": report_detail.get("job_posts", []),
            }

        st.session_state[WORKBENCH_RESULT_KEY] = {
            "kind": "market",
            "data": restored_result,
        }
        st.session_state[MARKET_TASK_ID_KEY] = None
        st.session_state[MARKET_TASK_DETAIL_KEY] = None
        st.rerun()


def _render_current_workbench_output() -> None:
    """优先渲染任务状态或上一份结果，保证用户提交后无需向下找报告。"""
    _render_market_task_status()

    result = st.session_state.get(WORKBENCH_RESULT_KEY)
    if not result:
        return

    if st.button("开始新的分析", key="clear_workbench_result"):
        st.session_state[WORKBENCH_RESULT_KEY] = None
        st.rerun()

    kind = result.get("kind")
    data = result.get("data") or {}
    if kind == "market":
        _render_market_match_result(
            data,
            download_key="workbench_market_report_download",
        )
    else:
        _render_job_match_result(
            data,
            download_key="workbench_jobmatch_report_download",
        )

    st.divider()


def _load_uploaded_resume_into_state() -> None:
    """在上传回调中更新简历文本，保证上传控件可以放在文本框下方。

    Streamlit 禁止在同一次运行中先创建 text_area、再直接修改对应的
    session_state。使用 on_change 回调后，内容会在组件创建前写入下一次重跑，
    既满足底部工具栏布局，也不会覆盖用户手动编辑的简历。
    """
    uploaded_file = st.session_state.get("workbench_resume_file")
    if uploaded_file is None:
        return

    upload_token = f"{uploaded_file.name}:{uploaded_file.size}"
    if st.session_state.get("workbench_upload_token") == upload_token:
        return

    try:
        st.session_state["workbench_resume_text"] = uploaded_file.getvalue().decode(
            "utf-8",
            errors="ignore",
        )
        st.session_state["workbench_upload_token"] = upload_token
        st.session_state["workbench_upload_message"] = "简历文件已读取，可继续编辑。"
    except Exception as exc:
        st.session_state["workbench_upload_error"] = f"简历文件读取失败：{exc}"


def render_workbench(
    target_role: str,
    city: str,
) -> None:
    """渲染统一的求职分析入口。

    用户只需要维护一份简历；通过分析方式切换“手动 JD 匹配”和“联网市场匹配”。
    """
    _init_workbench_state()
    _render_current_workbench_output()

    st.subheader("开始分析")
    st.caption(f"目标方向：{target_role} | 目标城市：{city or '不限'}")

    # 功能选择器位于 form 外部。这样用户切换选项时会立即重跑页面，
    # 不会再出现“选了市场岗位但仍展示 JD 输入框”的错位状态。
    mode = st.radio(
        "选择分析功能",
        ["简历 + 岗位 JD", "简历 + 市场岗位"],
        horizontal=True,
        key="workbench_analysis_mode",
    )

    try:
        resume_versions = list_resume_versions()
    except Exception as exc:
        resume_versions = []
        st.warning(f"读取已保存简历版本失败：{exc}")

    version_options: list[dict[str, Any] | None] = [None, *resume_versions]
    selected_version = st.selectbox(
        "用于本次分析的简历版本",
        version_options,
        format_func=lambda item: (
            "手动输入简历"
            if item is None
            else f"{item.get('version_name')} | {item.get('target_role') or '未设置方向'}"
        ),
        key="workbench_resume_version_choice",
    )
    selected_version_id = selected_version.get("id") if selected_version else None
    if selected_version_id and st.session_state.get("loaded_resume_version_id") != selected_version_id:
        st.session_state["workbench_resume_text"] = selected_version.get("raw_text", "")
        st.session_state["loaded_resume_version_id"] = selected_version_id
    elif selected_version is None:
        st.session_state["loaded_resume_version_id"] = None

    resume_text = st.text_area(
        "简历内容",
        height=300,
        placeholder="粘贴简历内容，或在下方上传 txt / md 文件...",
        key="workbench_resume_text",
    )

    jd_text = ""
    max_results = 8
    if mode == "简历 + 岗位 JD":
        jd_text = st.text_area(
            "岗位 JD",
            height=260,
            placeholder="粘贴目标岗位的职责和要求...",
            key="workbench_jd_text",
        )
    else:
        max_results = st.slider(
            "岗位样本数量",
            min_value=3,
            max_value=15,
            value=8,
            key="workbench_market_sample_count",
        )

    # 这个工具栏模拟 AI 应用的 composer：输入区下方左侧放简历上传，
    # 右侧放唯一的开始按钮。上传文件会写回上方文本框，分析仍需用户主动触发。
    upload_col, action_col = st.columns([4, 1])
    with upload_col:
        st.file_uploader(
            "上传简历（txt / md）",
            type=["txt", "md"],
            key="workbench_resume_file",
            on_change=_load_uploaded_resume_into_state,
        )
    with action_col:
        st.markdown("<div style='height: 1.9rem;'></div>", unsafe_allow_html=True)
        submit_label = "开始 JD 匹配" if mode == "简历 + 岗位 JD" else "开始市场分析"
        submitted = st.button(
            submit_label,
            type="primary",
            use_container_width=True,
            key="start_workbench_analysis",
        )

    if st.session_state.pop("workbench_upload_message", None):
        st.success("简历文件已读取，可继续编辑。")
    upload_error = st.session_state.pop("workbench_upload_error", None)
    if upload_error:
        st.error(upload_error)

    if not submitted:
        return

    if len(resume_text.strip()) < 80:
        st.warning("请提供不少于 80 字的简历内容。")
        return

    if mode == "简历 + 岗位 JD":
        if len(jd_text.strip()) < 80:
            st.warning("请提供不少于 80 字的岗位 JD 内容。")
            return
        try:
            with st.spinner("正在生成结构化匹配报告..."):
                result = create_job_match_report(
                {
                    "resume_text": resume_text,
                    "jd_text": jd_text,
                    "target_role": target_role,
                    "resume_version_id": (
                        selected_version_id
                        if selected_version
                        and resume_text == selected_version.get("raw_text", "")
                        else None
                    ),
                }
                )
        except Exception as exc:
            st.error(f"报告生成失败：{exc}")
            return

        st.session_state[WORKBENCH_RESULT_KEY] = {"kind": "job", "data": result}
        st.rerun()

    try:
        # 市场匹配包含联网搜索和 LLM 分析，使用后端异步任务避免前端请求长时间阻塞。
        task = create_market_match_task(
            {
                "resume_text": resume_text,
                "target_role": target_role,
                "city": city,
                "max_results": max_results,
                "resume_version_id": (
                    selected_version_id
                    if selected_version
                    and resume_text == selected_version.get("raw_text", "")
                    else None
                ),
            }
        )
    except Exception as exc:
        st.error(f"市场分析任务创建失败：{exc}")
        return

    st.session_state[WORKBENCH_RESULT_KEY] = None
    st.session_state[MARKET_TASK_ID_KEY] = task["task_id"]
    st.session_state[MARKET_TASK_DETAIL_KEY] = task
    st.rerun()


def _render_saved_report_detail(report_detail: dict[str, Any]) -> None:
    """复用工作台渲染器展示历史记录，避免新旧报告拥有两套不同界面。"""
    # 不能仅根据 job_posts 是否为空判断报告类型：搜索没有命中岗位时，
    # 市场报告同样可能没有样本。只要能恢复市场画像和市场分析，就按市场报告展示。
    restored_market = _restore_market_result(report_detail)
    if restored_market:
        _render_market_match_result(
            restored_market,
            download_key=f"history_market_report_download_{report_detail.get('id', 'unknown')}",
        )
        return

    analysis = _load_json_object(report_detail.get("parsed_result"))
    if analysis:
        _render_job_match_result(
            {
                "report_id": report_detail.get("id"),
                "score": report_detail.get("score"),
                "analysis": analysis,
                "markdown_report": report_detail.get("markdown_report", ""),
            },
            download_key=f"history_jobmatch_report_download_{report_detail.get('id', 'unknown')}",
        )
        return

    st.subheader("历史报告详情")
    st.warning("该历史记录没有可恢复的结构化数据，已隐藏原始模型输出。")
    st.markdown(
        _safe_markdown_report(
            report_detail.get("markdown_report"),
            "该历史记录无法安全恢复为用户报告，请重新生成一次分析。",
        )
    )


def render_reports_tab() -> None:
    """渲染历史报告列表，并让选中的报告在页面顶部完整显示。"""
    selected_detail = st.session_state.get(HISTORY_DETAIL_KEY)
    if selected_detail:
        if st.button("返回历史报告列表", key="back_to_history_list"):
            st.session_state[HISTORY_DETAIL_KEY] = None
            st.rerun()
        _render_saved_report_detail(selected_detail)
        return

    st.subheader("历史报告")
    try:
        reports = list_reports().get("reports", [])
    except Exception as exc:
        st.error(f"读取历史报告失败：{exc}")
        return

    if not reports:
        st.info("暂无历史报告。完成一次分析后，记录会保存在这里。")
        return

    selected_report = st.selectbox(
        "选择历史报告",
        reports,
        format_func=lambda item: (
            f"#{item.get('id')} | {item.get('target_role')} | "
            f"分数：{item.get('score')} | "
            f"{item.get('created_at_local') or item.get('created_at')}"
        ),
    )

    if st.button("查看报告详情", type="primary", use_container_width=True):
        try:
            st.session_state[HISTORY_DETAIL_KEY] = get_report_detail(
                int(selected_report["id"])
            )
        except Exception as exc:
            st.error(f"读取报告详情失败：{exc}")
            return
        st.rerun()


def _render_search_tool(default_keyword: str, city: str) -> None:
    """岗位搜索作为资料区工具使用，不再和核心分析入口并列。"""
    with st.form("job_search_form"):
        keyword = st.text_input("岗位关键词", value=default_keyword)
        st.caption(f"搜索城市：{city or '不限'}")
        max_results = st.slider("搜索结果数量", 1, 10, 5, key="explore_search_count")
        submitted = st.form_submit_button("搜索岗位", type="primary")

    if not submitted:
        return

    try:
        with st.spinner("正在联网搜索岗位..."):
            results = search_jobs(
                {"keyword": keyword, "city": city, "max_results": max_results}
            ).get("results", [])
    except Exception as exc:
        st.error(f"搜索失败：{exc}")
        return

    if not results:
        st.info("暂未搜索到岗位结果，请调整关键词后再试。")
        return

    for index, item in enumerate(results, start=1):
        title = item.get("title") or "未命名岗位"
        with st.expander(f"{index}. {title}"):
            st.write(item.get("content") or "暂无岗位描述")
            if item.get("url"):
                st.markdown(f"[查看原链接]({item['url']})")


def _render_role_map(role_info: dict[str, Any]) -> None:
    """展示当前目标方向的预设技能图谱，作为分析前的自查资料。"""
    if not role_info:
        st.info("暂无岗位技能图谱。")
        return

    skill_col, project_col, priority_col = st.columns(3)
    with skill_col:
        st.markdown("#### 核心技能")
        st.markdown(format_tags(get_role_core_skills(role_info)))
    with project_col:
        st.markdown("#### 推荐项目关键词")
        _show_list(_as_string_list(role_info.get("project_keywords")))
    with priority_col:
        st.markdown("#### 学习优先级")
        _show_list(_as_string_list(role_info.get("learning_priority")))


def render_explore_tab(
    target_role: str,
    city: str,
    role_info: dict[str, Any],
) -> None:
    """将联网岗位搜索和岗位技能图谱归为同一个资料区。"""
    st.subheader("岗位探索")
    search_tab, role_map_tab = st.tabs(["联网岗位搜索", "岗位技能图谱"])
    with search_tab:
        _render_search_tool(default_keyword=target_role, city=city)
    with role_map_tab:
        _render_role_map(role_info)


def render_dashboard() -> None:
    """展示只由用户真实投递与任务操作构成的闭环总览。"""
    st.subheader("求职总览")
    try:
        summary = get_dashboard_summary()
    except Exception as exc:
        st.error(f"读取求职总览失败：{exc}")
        return

    job_col1, job_col2, job_col3, job_col4 = st.columns(4)
    job_col1.metric("待投岗位", summary.get("saved_job_count", 0))
    job_col2.metric("已投递", summary.get("applied_job_count", 0))
    job_col3.metric("面试中", summary.get("interview_job_count", 0))
    job_col4.metric("Offer", summary.get("offer_job_count", 0))

    action_col1, action_col2, action_col3, action_col4 = st.columns(4)
    action_col1.metric("待办任务", summary.get("todo_action_count", 0))
    action_col2.metric("进行中", summary.get("in_progress_action_count", 0))
    action_col3.metric("已完成", summary.get("completed_action_count", 0))
    action_col4.metric("成果证据", summary.get("evidence_count", 0))


def render_job_pipeline() -> None:
    """维护收藏到 Offer 的投递状态，并由后端记录状态事件。"""
    st.subheader("投递管道")
    try:
        targets = list_job_targets()
    except Exception as exc:
        st.error(f"读取投递管道失败：{exc}")
        return

    if not targets:
        st.info("暂无投递目标。请在市场分析的岗位推荐中加入目标岗位。")
        return

    status_options = [
        "saved",
        "applied",
        "written_test",
        "interview",
        "offer",
        "rejected",
        "withdrawn",
    ]
    status_labels = {
        "saved": "待投递",
        "applied": "已投递",
        "written_test": "笔试",
        "interview": "面试",
        "offer": "Offer",
        "rejected": "未通过",
        "withdrawn": "已撤回",
    }
    for target in targets:
        title = target.get("title") or "未命名岗位"
        company = target.get("company") or "未知公司"
        with st.expander(
            f"{target.get('priority', 'C')} 类 | {title} | {company} | "
            f"{status_labels.get(target.get('status'), target.get('status'))}",
            expanded=target.get("status") in {"saved", "applied"},
        ):
            st.caption(
                f"匹配分：{target.get('match_score', '未评分')} | "
                f"创建时间：{target.get('created_at_local') or target.get('created_at')}"
            )
            if target.get("url"):
                st.markdown(f"[查看岗位原链接]({target['url']})")
            if target.get("note"):
                st.write(target["note"])
            with st.form(f"job_target_update_{target['id']}"):
                current_status = target.get("status", "saved")
                next_status = st.selectbox(
                    "投递状态",
                    status_options,
                    index=status_options.index(current_status),
                    format_func=lambda value: status_labels[value],
                )
                note = st.text_area(
                    "备注",
                    value=target.get("note", ""),
                    key=f"target_note_{target['id']}",
                    height=80,
                )
                submitted = st.form_submit_button("保存状态", type="primary")
            if submitted:
                try:
                    update_job_target(
                        int(target["id"]), {"status": next_status, "note": note}
                    )
                except Exception as exc:
                    st.error(f"状态更新失败：{exc}")
                else:
                    st.rerun()


def render_action_plan() -> None:
    """管理技能补强任务，完成前必须提交成果证据。"""
    st.subheader("成长计划")
    try:
        items = list_action_items()
    except Exception as exc:
        st.error(f"读取成长计划失败：{exc}")
        return

    if not items:
        st.info("暂无成长任务。请在分析报告的技能差距中创建任务。")
        return

    status_options = ["todo", "in_progress", "completed", "cancelled"]
    status_labels = {
        "todo": "待开始",
        "in_progress": "进行中",
        "completed": "已完成",
        "cancelled": "已取消",
    }
    for item in items:
        with st.expander(
            f"{item.get('skill') or '通用'} | {item.get('title')} | "
            f"{status_labels.get(item.get('status'), item.get('status'))}",
            expanded=item.get("status") in {"todo", "in_progress"},
        ):
            st.caption(
                f"优先级：{item.get('priority')} | 已提交证据：{item.get('evidence_count', 0)}"
            )
            st.write(f"预期产出：{item.get('expected_output')}")
            with st.form(f"action_evidence_{item['id']}"):
                evidence_url = st.text_input("成果链接", key=f"evidence_url_{item['id']}")
                evidence_note = st.text_area(
                    "成果说明", key=f"evidence_note_{item['id']}", height=80
                )
                evidence_submitted = st.form_submit_button("提交成果证据")
            if evidence_submitted:
                evidence_type = "link" if evidence_url.strip() else "note"
                try:
                    create_action_evidence(
                        int(item["id"]),
                        {
                            "evidence_type": evidence_type,
                            "url": evidence_url.strip() or None,
                            "content": evidence_note.strip(),
                        },
                    )
                except Exception as exc:
                    st.error(f"提交成果证据失败：{exc}")
                else:
                    st.rerun()

            with st.form(f"action_update_{item['id']}"):
                current_status = item.get("status", "todo")
                next_status = st.selectbox(
                    "任务状态",
                    status_options,
                    index=status_options.index(current_status),
                    format_func=lambda value: status_labels[value],
                )
                updated = st.form_submit_button("更新任务状态", type="primary")
            if updated:
                try:
                    update_action_item(int(item["id"]), {"status": next_status})
                except Exception as exc:
                    st.error(f"任务更新失败：{exc}")
                else:
                    st.rerun()


def render_resume_center() -> None:
    """提供“解析-确认-保存”的简历版本流程，保存动作始终由用户触发。"""
    st.subheader("简历中心")
    st.caption("先确认结构化信息，再保存为可追溯的简历版本。")
    raw_text = st.text_area(
        "简历内容",
        key="resume_center_raw_text",
        height=260,
        placeholder="粘贴不少于 80 字的简历内容...",
    )
    if st.button("解析简历", type="primary", key="parse_resume_profile"):
        if len(raw_text.strip()) < 80:
            st.warning("请提供不少于 80 字的简历内容。")
        else:
            try:
                with st.spinner("正在提取简历结构化信息..."):
                    parsed = parse_resume(raw_text.strip())
            except Exception as exc:
                st.error(f"简历解析失败：{exc}")
            else:
                st.session_state["resume_center_profile_json"] = json.dumps(
                    parsed.get("profile", {}), ensure_ascii=False, indent=2
                )

    profile_json = st.text_area(
        "确认后的结构化档案",
        value=st.session_state.get("resume_center_profile_json", "{}"),
        height=260,
        key="resume_center_profile_json",
    )
    version_col, role_col = st.columns(2)
    with version_col:
        version_name = st.text_input("版本名称", placeholder="例如：Python 后端实习 v1")
    with role_col:
        target_role = st.text_input("适用岗位方向", key="resume_center_target_role")
    if st.button("确认并保存简历版本", key="save_resume_version", type="primary"):
        try:
            profile = json.loads(profile_json)
            if not isinstance(profile, dict):
                raise ValueError("结构化档案必须是 JSON 对象")
            saved = save_resume_version(
                {
                    "version_name": version_name.strip(),
                    "target_role": target_role.strip(),
                    "raw_text": raw_text.strip(),
                    "profile": profile,
                }
            )
        except (ValueError, json.JSONDecodeError) as exc:
            st.error(f"简历版本校验失败：{exc}")
        except Exception as exc:
            st.error(f"保存简历版本失败：{exc}")
        else:
            st.success(f"已保存简历版本：{saved.get('version_name')}")

    try:
        versions = list_resume_versions()
    except Exception as exc:
        st.error(f"读取简历版本失败：{exc}")
        return
    if versions:
        st.markdown("#### 已保存版本")
        for version in versions:
            st.markdown(
                f"- **{version.get('version_name')}** | "
                f"{version.get('target_role') or '未设置方向'} | "
                f"{version.get('created_at') or ''}"
            )
