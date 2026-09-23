"""Read-only task orchestration for the user-facing workspace."""

import json

from app import database


def get_next_actions() -> list[dict]:
    """Derive next actions from existing facts without writing task rows."""
    with database.connect_db() as conn:
        actions: list[dict] = []

        def add(
            action_id: str,
            action_type: str,
            priority: str,
            title: str,
            description: str,
            entity_type: str,
            entity_id: int,
            route: str,
            action_label: str,
            due_at: str | None = None,
            updated_at: str | None = None,
        ) -> None:
            actions.append({
                "id": action_id,
                "action_type": action_type,
                "priority": priority,
                "title": title,
                "description": description,
                "entity_type": entity_type,
                "entity_id": int(entity_id),
                "route": route,
                "action_label": action_label,
                "due_at": due_at,
                "due_at_local": database.format_datetime_for_display(due_at),
                "updated_at": updated_at,
                "updated_at_local": database.format_datetime_for_display(updated_at),
            })

        suggestions = conn.execute(
            """
            SELECT rs.report_id, rs.resume_version_id, r.target_role,
                   MAX(rs.updated_at) AS updated_at,
                   SUM(CASE WHEN rs.status = 'pending' THEN 1 ELSE 0 END) AS pending_count,
                   SUM(CASE WHEN rs.status IN ('accepted', 'edited') THEN 1 ELSE 0 END) AS confirmed_count,
                   EXISTS(SELECT 1 FROM resume_versions v WHERE v.source_report_id = rs.report_id) AS has_new_version
            FROM resume_suggestions rs
            JOIN reports r ON r.id = rs.report_id
            GROUP BY rs.report_id, rs.resume_version_id, r.target_role
            """
        ).fetchall()
        for row in suggestions:
            role = row["target_role"] or "当前岗位"
            route = f"/resumes/review?resume={row['resume_version_id']}&report={row['report_id']}"
            if row["pending_count"]:
                add(
                    f"resume-suggestions:{row['report_id']}", "resume_suggestions", "high",
                    "确认简历建议", f"报告“{role}”有 {row['pending_count']} 条建议等待确认。",
                    "report", row["report_id"], route, "查看建议", updated_at=row["updated_at"],
                )
            elif row["confirmed_count"] and not row["has_new_version"]:
                add(
                    f"resume-version:{row['report_id']}", "resume_version_create", "high",
                    "创建优化后的简历版本", f"报告“{role}”的建议已确认，请提交完整简历文本创建新版本。",
                    "resume_version", row["resume_version_id"], route, "创建版本", updated_at=row["updated_at"],
                )

        saved_targets = conn.execute(
            """
            SELECT id, title, deadline_at, updated_at FROM job_targets
            WHERE status = 'saved' AND source_status = 'active'
            ORDER BY CASE WHEN deadline_at IS NULL THEN 1 ELSE 0 END, deadline_at, updated_at DESC
            """
        ).fetchall()
        for row in saved_targets:
            deadline = row["deadline_at"]
            add(
                f"job-target-apply:{row['id']}", "job_target_apply", "high" if deadline else "medium",
                f"处理投递：{row['title'] or '未命名岗位'}",
                f"岗位截止日期为 {database.format_datetime_for_display(deadline)}，请决定是否记录为已投递。" if deadline else "岗位已确认可投，请决定是否记录为已投递。",
                "job_target", row["id"], f"/pipeline?target={row['id']}", "查看投递", deadline, row["updated_at"],
            )

        follow_ups = conn.execute(
            """
            SELECT jt.id, jt.title, jt.updated_at FROM job_targets jt
            WHERE jt.status = 'applied'
              AND (jt.updated_at <= datetime('now', '-7 days')
                   OR NOT EXISTS (SELECT 1 FROM application_events e WHERE e.job_target_id = jt.id))
            ORDER BY jt.updated_at
            """
        ).fetchall()
        for row in follow_ups:
            add(
                f"job-target-follow-up:{row['id']}", "job_target_follow_up", "medium",
                f"跟进投递：{row['title'] or '未命名岗位'}", "这次投递较久没有新记录，请补充进展或下一步。",
                "job_target", row["id"], f"/pipeline?target={row['id']}", "记录进展", updated_at=row["updated_at"],
            )

        review_targets = conn.execute(
            """
            SELECT jt.id, jt.title, jt.updated_at FROM job_targets jt
            WHERE jt.status IN ('interview', 'offer', 'rejected')
              AND NOT EXISTS (SELECT 1 FROM interview_reviews ir WHERE ir.job_target_id = jt.id)
            ORDER BY jt.updated_at DESC
            """
        ).fetchall()
        for row in review_targets:
            add(
                f"interview-review:{row['id']}", "interview_review", "high",
                f"记录面试复盘：{row['title'] or '未命名岗位'}", "岗位已进入面试或面试后阶段，请记录真实经历。",
                "job_target", row["id"], f"/pipeline?target={row['id']}", "填写复盘", updated_at=row["updated_at"],
            )

        reviews = conn.execute(
            """
            SELECT ir.id, ir.job_target_id, ir.missing_skills_json, ir.updated_at, jt.title
            FROM interview_reviews ir JOIN job_targets jt ON jt.id = ir.job_target_id
            WHERE ir.actions_confirmed_at IS NULL ORDER BY ir.updated_at DESC
            """
        ).fetchall()
        for row in reviews:
            try:
                skills = [skill.strip() for skill in json.loads(row["missing_skills_json"] or "[]") if isinstance(skill, str) and skill.strip()]
            except json.JSONDecodeError:
                skills = []
            if skills:
                add(
                    f"interview-actions:{row['id']}", "interview_actions", "high",
                    f"确认面试能力缺口：{row['title'] or '未命名岗位'}",
                    f"面试复盘发现待补能力：{'、'.join(skills[:4])}。确认后才会加入行动计划。",
                    "interview_review", row["id"], f"/pipeline?target={row['job_target_id']}", "确认行动项", updated_at=row["updated_at"],
                )

        tasks = conn.execute(
            "SELECT id, status, updated_at, error_message FROM analysis_tasks WHERE status IN ('pending', 'running', 'failed') ORDER BY updated_at DESC"
        ).fetchall()
        for row in tasks:
            failed = row["status"] == "failed"
            add(
                f"analysis-task:{row['id']}", "analysis_retry" if failed else "analysis_progress", "high" if failed else "low",
                "重试市场分析" if failed else "查看市场分析进度",
                row["error_message"] if failed and row["error_message"] else "市场岗位分析仍在处理，可稍后查看岗位收件箱。",
                "analysis_task", row["id"], "/inbox", "重新搜索" if failed else "查看进度", updated_at=row["updated_at"],
            )

        turns = conn.execute(
            """
            SELECT t.id, t.status, t.error_message, t.updated_at, s.target_role
            FROM analysis_turns t JOIN copilot_sessions s ON s.id = t.session_id
            WHERE t.status IN ('pending', 'running', 'failed') ORDER BY t.updated_at DESC
            """
        ).fetchall()
        for row in turns:
            failed = row["status"] == "failed"
            add(
                f"analysis-turn:{row['id']}", "analysis_retry" if failed else "analysis_progress", "high" if failed else "low",
                "重试岗位分析" if failed else "查看岗位分析进度",
                row["error_message"] if failed and row["error_message"] else f"“{row['target_role'] or '当前岗位'}”的分析尚未完成。",
                "analysis_turn", row["id"], f"/copilot?turn={row['id']}", "重新分析" if failed else "查看分析", updated_at=row["updated_at"],
            )

    rank = {"high": 0, "medium": 1, "low": 2}
    actions.sort(key=lambda item: (rank[item["priority"]], item["due_at"] is None, item["due_at"] or "9999-12-31", item["updated_at"] or ""))
    return actions[:50]
