from app.schemas import JobMatchAnalysis


def render_markdown_report(analysis: JobMatchAnalysis) -> str:
    """把结构化分析结果渲染成 Markdown 报告。

    LLM 只负责产出结构化数据，后端负责稳定排版。后续如果要导出
    PDF / DOCX，也可以复用同一个 analysis 对象。
    """
    lines = [
        "# 求职匹配分析报告",
        "",
        "## 1. 匹配总览",
        "",
        f"**匹配评分：{analysis.score}/100**",
        "",
        analysis.summary,
        "",
        "## 2. 已匹配技能",
        "",
        _render_list(analysis.matched_skills),
        "",
        "## 3. 缺失技能",
        "",
        _render_list(analysis.missing_skills),
        "",
        "## 4. 分项评分",
        "",
    ]

    for item in analysis.score_dimensions:
        lines.extend(
            [
                f"### {item.name}：{item.score}/{item.max_score}",
                "",
                "**证据：**",
                "",
                _render_list(item.evidence),
                "",
                f"**建议：** {item.suggestion}",
                "",
            ]
        )

    if analysis.requirement_matches:
        lines.extend(["## 5. 逐条岗位要求证据", ""])
        for item in analysis.requirement_matches:
            status = {
                "supported": "已有直接证据",
                "partial": "语义相关，证据待补强",
                "missing_evidence": "缺少简历证据",
            }.get(item.status, item.status)
            lines.extend(
                [
                    f"### {item.requirement}（{status}，可信度 {round(item.confidence * 100)}%）",
                    "",
                    "**关键词证据：**",
                    "",
                    _render_list(item.keyword_evidence),
                    "",
                    "**语义证据：**",
                    "",
                    _render_list(item.semantic_evidence),
                    "",
                    f"**建议：** {item.suggestion}",
                    "",
                ]
            )

    lines.extend(
        [
            "## 可写入简历的项目 Bullet",
            "",
            _render_list(analysis.resume_bullets),
            "",
            "## 6. 面试高频问题",
            "",
        ]
    )

    for index, item in enumerate(analysis.interview_questions, start=1):
        lines.append(f"{index}. **{item.question}**")
        lines.append(f"   - 考察技能：{item.skill}")
        if item.reason:
            lines.append(f"   - 出题原因：{item.reason}")
        lines.append("")

    lines.extend(["## 7 天补强计划", ""])

    for item in analysis.action_plan:
        lines.append(f"- 第 {item.day} 天：{item.task}")
        if item.output:
            lines.append(f"  - 产出：{item.output}")

    lines.extend(
        [
            "",
            "## 风险点",
            "",
            _render_list(analysis.risk_points),
        ]
    )

    return "\n".join(lines)


def _render_list(items: list[str]) -> str:
    """把字符串列表渲染成 Markdown bullet list。"""
    if not items:
        return "- 暂无"
    return "\n".join(f"- {item}" for item in items)
