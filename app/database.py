import sqlite3
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.config import settings
from app.schemas import JobPost
from app.schemas.resume import ResumeProfile, ResumeVersionCreate
from app.schemas.workflow import (
    ActionEvidenceCreate,
    ActionItemUpdate,
    ActionItemsFromReportRequest,
    ApplicationEventCreate,
    JobTargetCreate,
    JobTargetUpdate,
)

DB_PATH = settings.database_path
DISPLAY_TIMEZONE = ZoneInfo("Asia/Shanghai")


# 这些字段用于追踪 LLM 输出质量：原始输出、解析结果、解析状态和耗时等。
REPORT_EXTRA_COLUMNS = {
    "raw_result": "TEXT",
    "parsed_result": "TEXT",
    "parse_status": "TEXT",
    "parse_error": "TEXT",
    "model_name": "TEXT",
    "latency_ms": "INTEGER",
    "resume_version_id": "INTEGER",
}

JOB_POST_EXTRA_COLUMNS = {
    "relevance_score": "REAL",
    "verification_status": "TEXT",
    "verification_reason": "TEXT",
}

SESSION_EXTRA_COLUMNS = {
    "active_report_id": "INTEGER",
}

TURN_EXTRA_COLUMNS = {
    "stage": "TEXT NOT NULL DEFAULT 'queued'",
    "progress": "INTEGER NOT NULL DEFAULT 0",
    "parent_turn_id": "INTEGER",
    "input_type": "TEXT NOT NULL DEFAULT 'initial_jd'",
}


def format_datetime_for_display(value: str | None) -> str | None:
    """将 SQLite 保存的 UTC 时间转换为北京时间展示。

    SQLite 的 CURRENT_TIMESTAMP 是 UTC。旧记录没有时区信息，因此统一按 UTC
    解释；排序仍使用原始 UTC 字段，展示时才附加 Asia/Shanghai 时区。
    """
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(DISPLAY_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")


def _with_display_times(row: dict) -> dict:
    """保留原始 UTC 字段，并增加专门给前端使用的本地时间字段。"""
    result = dict(row)
    for field in ("created_at", "updated_at"):
        if field in result:
            result[f"{field}_local"] = format_datetime_for_display(result[field])
    return result


def init_db() -> None:
    """初始化本地 SQLite 数据库。
    reports 表保存最终分析报告；
    job_posts 表保存某次市场匹配分析参考过的岗位样本。
    """
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_role TEXT NOT NULL,
                score INTEGER,
                resume_text TEXT NOT NULL,
                jd_text TEXT NOT NULL,
                markdown_report TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS job_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_id INTEGER,
                title TEXT,
                company TEXT,
                url TEXT,
                content TEXT,
                source TEXT,
                published_at TEXT,
                deadline_at TEXT,
                fetched_at TEXT,
                status TEXT,
                freshness_score REAL,
                invalid_reason TEXT,
                FOREIGN KEY (report_id) REFERENCES reports(id)
            )
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_job_posts_report_id
                ON job_posts(report_id)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS analysis_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_type TEXT NOT NULL,
            status TEXT NOT NULL,
            progress INTEGER DEFAULT 0,
            report_id INTEGER,
            error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS resumes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            display_name TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS resume_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            resume_id INTEGER NOT NULL,
            version_name TEXT NOT NULL,
            target_role TEXT,
            raw_text TEXT NOT NULL,
            profile_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (resume_id) REFERENCES resumes(id)
    )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_resume_versions_resume_id
            ON resume_versions(resume_id)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS job_targets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_id INTEGER NOT NULL,
                job_post_id INTEGER NOT NULL,
                resume_version_id INTEGER,
                title TEXT NOT NULL,
                company TEXT NOT NULL DEFAULT '',
                url TEXT NOT NULL,
                priority TEXT NOT NULL,
                match_score INTEGER,
                source_status TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'saved',
                note TEXT NOT NULL DEFAULT '',
                deadline_at TEXT,
                applied_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(report_id, url),
                FOREIGN KEY (report_id) REFERENCES reports(id),
                FOREIGN KEY (job_post_id) REFERENCES job_posts(id),
                FOREIGN KEY (resume_version_id) REFERENCES resume_versions(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS application_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_target_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                occurred_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                note TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (job_target_id) REFERENCES job_targets(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS action_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_id INTEGER NOT NULL,
                resume_version_id INTEGER,
                action_type TEXT NOT NULL,
                skill TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL,
                priority TEXT NOT NULL DEFAULT 'medium',
                status TEXT NOT NULL DEFAULT 'todo',
                expected_output TEXT NOT NULL,
                due_date TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(report_id, skill, title),
                FOREIGN KEY (report_id) REFERENCES reports(id),
                FOREIGN KEY (resume_version_id) REFERENCES resume_versions(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS action_evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action_item_id INTEGER NOT NULL,
                evidence_type TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                url TEXT,
                resume_version_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (action_item_id) REFERENCES action_items(id),
                FOREIGN KEY (resume_version_id) REFERENCES resume_versions(id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_job_targets_status ON job_targets(status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_job_target ON application_events(job_target_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_action_items_status ON action_items(status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_action_evidence_item ON action_evidence(action_item_id)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS copilot_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                resume_version_id INTEGER,
                active_report_id INTEGER,
                target_role TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (resume_version_id) REFERENCES resume_versions(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS analysis_turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                input_message_id INTEGER,
                status TEXT NOT NULL DEFAULT 'pending',
                stage TEXT NOT NULL DEFAULT 'queued',
                progress INTEGER NOT NULL DEFAULT 0,
                error_message TEXT NOT NULL DEFAULT '',
                report_id INTEGER,
                parent_turn_id INTEGER,
                input_type TEXT NOT NULL DEFAULT 'initial_jd',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES copilot_sessions(id),
                FOREIGN KEY (report_id) REFERENCES reports(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS copilot_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                turn_id INTEGER,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES copilot_sessions(id),
                FOREIGN KEY (turn_id) REFERENCES analysis_turns(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS analysis_artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                turn_id INTEGER NOT NULL,
                artifact_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'ready',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (turn_id) REFERENCES analysis_turns(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS artifact_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                artifact_id INTEGER NOT NULL,
                decision TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (artifact_id) REFERENCES analysis_artifacts(id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_copilot_messages_session ON copilot_messages(session_id, id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_analysis_turns_session ON analysis_turns(session_id, id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_analysis_artifacts_turn ON analysis_artifacts(turn_id, id)"
        )


        _ensure_report_columns(conn)
        _ensure_job_post_columns(conn)
        _ensure_session_columns(conn)
        _ensure_turn_columns(conn)
        conn.commit()


def _ensure_report_columns(conn: sqlite3.Connection) -> None:
    """SQLite 没有自动迁移系统，这里用轻量 ALTER TABLE 兼容旧表。"""
    existing_columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(reports)").fetchall()
    }

    for column_name, column_type in REPORT_EXTRA_COLUMNS.items():
        if column_name not in existing_columns:
            conn.execute(
                f"ALTER TABLE reports ADD COLUMN {column_name} {column_type}"
            )


def _ensure_job_post_columns(conn: sqlite3.Connection) -> None:
    """为旧 job_posts 表补充岗位相关性与详情验证追溯字段。"""
    existing_columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(job_posts)").fetchall()
    }

    for column_name, column_type in JOB_POST_EXTRA_COLUMNS.items():
        if column_name not in existing_columns:
            conn.execute(
                f"ALTER TABLE job_posts ADD COLUMN {column_name} {column_type}"
            )


def _ensure_session_columns(conn: sqlite3.Connection) -> None:
    """兼容早期副驾会话表，补充当前活动报告字段。"""
    existing_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(copilot_sessions)").fetchall()
    }
    for column_name, column_type in SESSION_EXTRA_COLUMNS.items():
        if column_name not in existing_columns:
            conn.execute(
                f"ALTER TABLE copilot_sessions ADD COLUMN {column_name} {column_type}"
            )


def _ensure_turn_columns(conn: sqlite3.Connection) -> None:
    """兼容早期副驾回合表，补充可展示的阶段与进度字段。"""
    existing_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(analysis_turns)").fetchall()
    }
    for column_name, column_type in TURN_EXTRA_COLUMNS.items():
        if column_name not in existing_columns:
            conn.execute(
                f"ALTER TABLE analysis_turns ADD COLUMN {column_name} {column_type}"
            )


def save_report(
    target_role: str,
    score: int | None,
    resume_text: str,
    jd_text: str,
    markdown_report: str,
    raw_result: str | None = None,
    parsed_result: str | None = None,
    parse_status: str = "unknown",
    parse_error: str | None = None,
    model_name: str | None = None,
    latency_ms: int | None = None,
    resume_version_id: int | None = None,
) -> int:
    """保存一次分析报告。

    markdown_report 面向用户展示；raw_result 和 parsed_result 面向调试与后续评测。
    """
    with sqlite3.connect(DB_PATH) as conn:
        _ensure_resume_version_exists(conn, resume_version_id)
        cursor = conn.execute(
            """
            INSERT INTO reports (
                target_role,
                score,
                resume_text,
                jd_text,
                markdown_report,
                raw_result,
                parsed_result,
                parse_status,
                parse_error,
                model_name,
                latency_ms,
                resume_version_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                target_role,
                score,
                resume_text,
                jd_text,
                markdown_report,
                raw_result,
                parsed_result,
                parse_status,
                parse_error,
                model_name,
                latency_ms,
                resume_version_id,
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def update_report_analysis(
    report_id: int,
    *,
    score: int | None,
    markdown_report: str,
    parsed_result: str,
    parse_status: str,
    raw_result: str | None = None,
) -> None:
    """将后续 Agent 汇总结果回写到同一份报告，保持报告 ID 和任务关联不变。"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            UPDATE reports
            SET score = ?, markdown_report = ?, parsed_result = ?, parse_status = ?,
                raw_result = COALESCE(?, raw_result)
            WHERE id = ?
            """,
            (score, markdown_report, parsed_result, parse_status, raw_result, report_id),
        )
        conn.commit()


def save_job_posts(report_id: int, posts: list[JobPost]) -> None:
    """保存一次报告关联的岗位搜索结果。

    report_id 来自 save_report() 返回值，用它把"报告"和"参考岗位样本"关联起来。
    """
    if not posts:
        return

    with sqlite3.connect(DB_PATH) as conn:
        conn.executemany(
            """
            INSERT INTO job_posts (
                report_id,
                title,
                company,
                url,
                content,
                source,
                published_at,
                deadline_at,
                fetched_at,
                status,
                freshness_score,
                invalid_reason,
                relevance_score,
                verification_status,
                verification_reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    report_id,
                    post.title,
                    post.company,
                    post.url,
                    post.content,
                    post.source,
                    post.published_at.isoformat() if post.published_at else None,
                    post.deadline_at.isoformat() if post.deadline_at else None,
                    post.fetched_at.isoformat() if post.fetched_at else None,
                    post.status,
                    post.freshness_score,
                    post.invalid_reason,
                    post.relevance_score,
                    post.verification_status,
                    post.verification_reason,
                )
                for post in posts
            ],
        )
        conn.commit()


def list_reports() -> list[dict]:
    """查询历史报告摘要列表，前端列表页不返回简历/JD 大文本。"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT
                id,
                target_role,
                score,
                parse_status,
                parse_error,
                model_name,
                latency_ms,
                created_at,
                (SELECT COUNT(*) FROM job_posts WHERE job_posts.report_id = reports.id) AS job_post_count
            FROM reports
            ORDER BY created_at DESC
            """
        ).fetchall()

        return [_with_display_times(dict(row)) for row in rows]


def get_report(report_id: int) -> dict | None:
    """查询单个报告详情，包含完整报告和调试字段。"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT
                id,
                target_role,
                score,
                resume_text,
                jd_text,
                markdown_report,
                raw_result,
                parsed_result,
                parse_status,
                parse_error,
                model_name,
                latency_ms,
                resume_version_id,
                created_at,
                (SELECT COUNT(*) FROM job_posts WHERE job_posts.report_id = reports.id) AS job_post_count
            FROM reports
            WHERE id = ?
            """,
            (report_id,),
        ).fetchone()

        if row is None:
            return None

        return _with_display_times(dict(row))

def list_job_posts(report_id: int) -> list[dict]:
    """查询某次报告关联的岗位搜索结果。

    前端或调试时可以用它查看：模型分析时到底参考了哪些岗位，
    以及每个岗位的 status 和 freshness_score。
    """
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT
                id,
                report_id,
                title,
                company,
                url,
                content,
                source,
                published_at,
                deadline_at,
                fetched_at,
                status,
                freshness_score,
                invalid_reason,
                relevance_score,
                verification_status,
                verification_reason
            FROM job_posts
            WHERE report_id = ?
            ORDER BY freshness_score DESC, id ASC
            """,
            (report_id,),
        ).fetchall()

        return [dict(row) for row in rows]


def create_analysis_task(task_type: str) -> int:
    """创建异步分析任务，返回 task_id。"""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            """
            INSERT INTO analysis_tasks (task_type, status, progress)
            VALUES (?, ?, ?)
            """,
            (task_type, "pending", 0),
        )
        conn.commit()
        return int(cursor.lastrowid)


def update_analysis_task(
    task_id: int,
    status: str | None = None,
    progress: int | None = None,
    report_id: int | None = None,
    error_message: str | None = None,
) -> None:
    """更新任务状态。后台任务执行过程中会调用这个函数。"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            UPDATE analysis_tasks
            SET
                status = COALESCE(?, status),
                progress = COALESCE(?, progress),
                report_id = COALESCE(?, report_id),
                error_message = COALESCE(?, error_message),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, progress, report_id, error_message, task_id),
        )
        conn.commit()


def get_analysis_task(task_id: int) -> dict | None:
    """查询任务详情，供前端轮询。"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT id, task_type, status, progress, report_id,
                   error_message, created_at, updated_at
            FROM analysis_tasks
            WHERE id = ?
            """,
            (task_id,),
        ).fetchone()

        return _with_display_times(dict(row)) if row else None

def create_resume_version(payload: ResumeVersionCreate) -> dict:
    """保存一份用户确认后的简历版本。"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        if payload.resume_id is None:
            cursor = conn.execute(
                "INSERT INTO resumes (display_name) VALUES (?)",
                (payload.version_name,),
            )
            resume_id = int(cursor.lastrowid)
        else:
            row = conn.execute(
                "SELECT id FROM resumes WHERE id = ?",
                (payload.resume_id,),
            ).fetchone()

            if row is None:
                raise ValueError("resume_id 不存在")

            resume_id = payload.resume_id
            conn.execute(
                """
                UPDATE resumes
                SET updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (resume_id,),
            )

        cursor = conn.execute(
            """
            INSERT INTO resume_versions (
                resume_id, version_name, target_role, raw_text, profile_json
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                resume_id,
                payload.version_name,
                payload.target_role,
                payload.raw_text,
                payload.profile.model_dump_json(),
            ),
        )
        version_id = int(cursor.lastrowid)
        row = conn.execute(
            """
            SELECT id, resume_id, version_name, target_role,
                   raw_text, profile_json, created_at
            FROM resume_versions
            WHERE id = ?
            """,
            (version_id,),
        ).fetchone()
        conn.commit()

        return _resume_version_row_to_dict(row)


def list_resume_versions() -> list[dict]:
    """查询全部简历版本，供前端选择。"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT
                id, resume_id, version_name, target_role,
                raw_text, profile_json, created_at
            FROM resume_versions
            ORDER BY created_at DESC, id DESC
            """
        ).fetchall()

    return [_resume_version_row_to_dict(row) for row in rows]


def _resume_version_row_to_dict(row: sqlite3.Row) -> dict:
    """将 SQLite 行恢复成 API 可直接返回的简历版本对象。"""
    return {
        "id": row["id"],
        "resume_id": row["resume_id"],
        "version_name": row["version_name"],
        "target_role": row["target_role"] or "",
        "raw_text": row["raw_text"],
        "profile": ResumeProfile.model_validate_json(row["profile_json"]),
        "created_at": format_datetime_for_display(row["created_at"]),
    }


def _ensure_resume_version_exists(conn: sqlite3.Connection, resume_version_id: int | None) -> None:
    if resume_version_id is None:
        return
    if conn.execute(
        "SELECT 1 FROM resume_versions WHERE id = ?", (resume_version_id,)
    ).fetchone() is None:
        raise ValueError("resume_version_id 不存在")


def _job_target_row_to_dict(row: sqlite3.Row) -> dict:
    result = _with_display_times(dict(row))
    result["applied_at"] = format_datetime_for_display(result["applied_at"])
    return result


def create_job_target(payload: JobTargetCreate) -> dict:
    """从报告中的确认有效岗位创建投递目标，重复创建时返回已有记录。"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        post = conn.execute(
            """
            SELECT id, title, company, url, deadline_at, status
            FROM job_posts
            WHERE report_id = ? AND url = ?
            """,
            (payload.report_id, payload.url),
        ).fetchone()
        if post is None:
            raise ValueError("岗位不属于该报告，无法加入投递管道")
        if post["status"] != "active":
            raise ValueError("仅确认仍可投递的岗位可以加入投递管道")

        report = conn.execute(
            "SELECT parsed_result, resume_version_id FROM reports WHERE id = ?",
            (payload.report_id,),
        ).fetchone()
        if report is None:
            raise ValueError("报告不存在")

        try:
            analysis = json.loads(report["parsed_result"] or "{}")
        except json.JSONDecodeError:
            analysis = {}
        recommendation = next(
            (
                item
                for item in analysis.get("job_recommendations", [])
                if isinstance(item, dict) and item.get("url") == payload.url
            ),
            None,
        )
        if recommendation is None:
            raise ValueError("岗位不在该报告的 A/B/C 推荐列表中")
        if recommendation.get("level") != payload.priority:
            raise ValueError("岗位优先级必须与报告推荐保持一致")

        existing = conn.execute(
            "SELECT * FROM job_targets WHERE report_id = ? AND url = ?",
            (payload.report_id, payload.url),
        ).fetchone()
        if existing is not None:
            return _job_target_row_to_dict(existing)

        _ensure_resume_version_exists(conn, report["resume_version_id"])
        cursor = conn.execute(
            """
            INSERT INTO job_targets (
                report_id, job_post_id, resume_version_id, title, company, url,
                priority, match_score, source_status, note, deadline_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.report_id,
                post["id"],
                report["resume_version_id"],
                post["title"],
                post["company"] or "",
                post["url"],
                payload.priority,
                recommendation.get("match_score"),
                post["status"],
                payload.note,
                post["deadline_at"],
            ),
        )
        target_id = int(cursor.lastrowid)
        conn.execute(
            "INSERT INTO application_events (job_target_id, event_type, note) VALUES (?, ?, ?)",
            (target_id, "saved", "从市场分析报告加入投递管道"),
        )
        row = conn.execute("SELECT * FROM job_targets WHERE id = ?", (target_id,)).fetchone()
        conn.commit()
        return _job_target_row_to_dict(row)


def list_job_targets(status: str | None = None) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        query = "SELECT * FROM job_targets"
        params: tuple[str, ...] = ()
        if status:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY CASE priority WHEN 'A' THEN 1 WHEN 'B' THEN 2 ELSE 3 END, updated_at DESC"
        rows = conn.execute(query, params).fetchall()
        return [_job_target_row_to_dict(row) for row in rows]


_ALLOWED_TARGET_TRANSITIONS = {
    "saved": {"applied", "withdrawn"},
    "applied": {"written_test", "interview", "offer", "rejected", "withdrawn"},
    "written_test": {"interview", "offer", "rejected", "withdrawn"},
    "interview": {"offer", "rejected", "withdrawn"},
    "offer": set(),
    "rejected": set(),
    "withdrawn": set(),
}


def update_job_target(target_id: int, payload: JobTargetUpdate) -> dict | None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        current = conn.execute("SELECT * FROM job_targets WHERE id = ?", (target_id,)).fetchone()
        if current is None:
            return None

        new_status = payload.status or current["status"]
        if new_status != current["status"] and new_status not in _ALLOWED_TARGET_TRANSITIONS[current["status"]]:
            raise ValueError(f"不能将岗位状态从 {current['status']} 变更为 {new_status}")
        if new_status == "applied" and current["source_status"] != "active":
            raise ValueError("岗位有效性未确认，不能记录为已投递")

        note = current["note"] if payload.note is None else payload.note
        deadline = payload.deadline_at.isoformat() if payload.deadline_at else current["deadline_at"]
        applied_at = current["applied_at"]
        if new_status == "applied" and applied_at is None:
            applied_at = "CURRENT_TIMESTAMP"

        if applied_at == "CURRENT_TIMESTAMP":
            conn.execute(
                """
                UPDATE job_targets
                SET status = ?, note = ?, deadline_at = ?, applied_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (new_status, note, deadline, target_id),
            )
        else:
            conn.execute(
                """
                UPDATE job_targets
                SET status = ?, note = ?, deadline_at = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (new_status, note, deadline, target_id),
            )

        if new_status != current["status"]:
            conn.execute(
                "INSERT INTO application_events (job_target_id, event_type, note) VALUES (?, ?, ?)",
                (target_id, f"status:{new_status}", "岗位状态更新"),
            )
        row = conn.execute("SELECT * FROM job_targets WHERE id = ?", (target_id,)).fetchone()
        conn.commit()
        return _job_target_row_to_dict(row)


def create_application_event(target_id: int, payload: ApplicationEventCreate) -> dict | None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        if conn.execute("SELECT 1 FROM job_targets WHERE id = ?", (target_id,)).fetchone() is None:
            return None
        if payload.occurred_at is None:
            cursor = conn.execute(
                "INSERT INTO application_events (job_target_id, event_type, note) VALUES (?, ?, ?)",
                (target_id, payload.event_type, payload.note),
            )
        else:
            cursor = conn.execute(
                """
                INSERT INTO application_events (job_target_id, event_type, occurred_at, note)
                VALUES (?, ?, ?, ?)
                """,
                (target_id, payload.event_type, payload.occurred_at.isoformat(), payload.note),
            )
        row = conn.execute(
            "SELECT id, job_target_id, event_type, occurred_at, note FROM application_events WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
        conn.commit()
        return _with_display_times(dict(row))


def _action_item_row_to_dict(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    result = _with_display_times(dict(row))
    result["evidence_count"] = conn.execute(
        "SELECT COUNT(*) FROM action_evidence WHERE action_item_id = ?", (row["id"],)
    ).fetchone()[0]
    return result


def create_action_items_from_report(
    report_id: int, payload: ActionItemsFromReportRequest
) -> list[dict]:
    """将已校验报告中的技能缺口转为待办；不接受报告外的任意技能。"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        report = conn.execute(
            "SELECT parsed_result, resume_version_id FROM reports WHERE id = ?", (report_id,)
        ).fetchone()
        if report is None:
            raise ValueError("报告不存在")
        try:
            analysis = json.loads(report["parsed_result"] or "{}")
        except json.JSONDecodeError:
            analysis = {}
        missing_skills = analysis.get("missing_market_skills") or analysis.get("missing_skills") or []
        missing_skills = [skill for skill in missing_skills if isinstance(skill, str) and skill.strip()]
        if not missing_skills:
            raise ValueError("该报告没有可转化的技能缺口")

        requested_skills = payload.skills or missing_skills[:5]
        invalid_skills = set(requested_skills) - set(missing_skills)
        if invalid_skills:
            raise ValueError("只能选择当前报告中的技能缺口创建任务")
        resume_version_id = payload.resume_version_id or report["resume_version_id"]
        _ensure_resume_version_exists(conn, resume_version_id)

        created_items = []
        for index, skill in enumerate(requested_skills):
            title = f"补强 {skill}"
            existing = conn.execute(
                "SELECT * FROM action_items WHERE report_id = ? AND skill = ? AND title = ?",
                (report_id, skill, title),
            ).fetchone()
            if existing is not None:
                created_items.append(_action_item_row_to_dict(conn, existing))
                continue
            cursor = conn.execute(
                """
                INSERT INTO action_items (
                    report_id, resume_version_id, action_type, skill, title, priority, expected_output
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report_id,
                    resume_version_id,
                    "skill_gap",
                    skill,
                    title,
                    "high" if index < 2 else "medium",
                    f"提交一个能够证明 {skill} 能力的项目链接、练习记录或简历版本。",
                ),
            )
            row = conn.execute("SELECT * FROM action_items WHERE id = ?", (cursor.lastrowid,)).fetchone()
            created_items.append(_action_item_row_to_dict(conn, row))
        conn.commit()
        return created_items


def list_action_items(status: str | None = None) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        query = "SELECT * FROM action_items"
        params: tuple[str, ...] = ()
        if status:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, updated_at DESC"
        rows = conn.execute(query, params).fetchall()
        return [_action_item_row_to_dict(conn, row) for row in rows]


def update_action_item(item_id: int, payload: ActionItemUpdate) -> dict | None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        current = conn.execute("SELECT * FROM action_items WHERE id = ?", (item_id,)).fetchone()
        if current is None:
            return None
        new_status = payload.status or current["status"]
        if new_status == "completed":
            evidence_count = conn.execute(
                "SELECT COUNT(*) FROM action_evidence WHERE action_item_id = ?", (item_id,)
            ).fetchone()[0]
            if evidence_count == 0:
                raise ValueError("请先提交成果证据，再将任务标记为完成")
        title = payload.title or current["title"]
        priority = payload.priority or current["priority"]
        due_date = payload.due_date.isoformat() if payload.due_date else current["due_date"]
        conn.execute(
            """
            UPDATE action_items
            SET title = ?, priority = ?, status = ?, due_date = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (title, priority, new_status, due_date, item_id),
        )
        row = conn.execute("SELECT * FROM action_items WHERE id = ?", (item_id,)).fetchone()
        conn.commit()
        return _action_item_row_to_dict(conn, row)


def create_action_evidence(item_id: int, payload: ActionEvidenceCreate) -> dict | None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        if conn.execute("SELECT 1 FROM action_items WHERE id = ?", (item_id,)).fetchone() is None:
            return None
        _ensure_resume_version_exists(conn, payload.resume_version_id)
        cursor = conn.execute(
            """
            INSERT INTO action_evidence (
                action_item_id, evidence_type, content, url, resume_version_id
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (item_id, payload.evidence_type, payload.content, payload.url, payload.resume_version_id),
        )
        row = conn.execute(
            """
            SELECT id, action_item_id, evidence_type, content, url, resume_version_id, created_at
            FROM action_evidence WHERE id = ?
            """,
            (cursor.lastrowid,),
        ).fetchone()
        conn.commit()
        return _with_display_times(dict(row))


def get_dashboard_summary() -> dict:
    """返回只基于用户真实操作数据的个人求职总览。"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        target_counts = {
            row["status"]: row["count"]
            for row in conn.execute(
                "SELECT status, COUNT(*) AS count FROM job_targets GROUP BY status"
            ).fetchall()
        }
        action_counts = {
            row["status"]: row["count"]
            for row in conn.execute(
                "SELECT status, COUNT(*) AS count FROM action_items GROUP BY status"
            ).fetchall()
        }
        evidence_count = conn.execute("SELECT COUNT(*) FROM action_evidence").fetchone()[0]
        return {
            "saved_job_count": target_counts.get("saved", 0),
            "applied_job_count": target_counts.get("applied", 0),
            "interview_job_count": target_counts.get("interview", 0),
            "offer_job_count": target_counts.get("offer", 0),
            "todo_action_count": action_counts.get("todo", 0),
            "in_progress_action_count": action_counts.get("in_progress", 0),
            "completed_action_count": action_counts.get("completed", 0),
            "evidence_count": evidence_count,
        }


def _copilot_session_row_to_dict(row: sqlite3.Row) -> dict:
    return _with_display_times(dict(row))


def _copilot_message_row_to_dict(row: sqlite3.Row) -> dict:
    return _with_display_times(dict(row))


def _artifact_row_to_dict(row: sqlite3.Row) -> dict:
    result = _with_display_times(dict(row))
    result["payload"] = json.loads(result.pop("payload_json"))
    return result


def _turn_row_to_dict(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    result = _with_display_times(dict(row))
    artifacts = conn.execute(
        "SELECT * FROM analysis_artifacts WHERE turn_id = ? ORDER BY id", (row["id"],)
    ).fetchall()
    result["artifacts"] = [_artifact_row_to_dict(item) for item in artifacts]
    return result


def create_copilot_session(
    resume_version_id: int | None, target_role: str
) -> dict:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        _ensure_resume_version_exists(conn, resume_version_id)
        cursor = conn.execute(
            "INSERT INTO copilot_sessions (resume_version_id, target_role) VALUES (?, ?)",
            (resume_version_id, target_role.strip()),
        )
        row = conn.execute(
            "SELECT * FROM copilot_sessions WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        conn.commit()
        return _copilot_session_row_to_dict(row)


def get_copilot_session(session_id: int) -> dict | None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM copilot_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row is None:
            return None
        result = _copilot_session_row_to_dict(row)
        messages = conn.execute(
            "SELECT * FROM copilot_messages WHERE session_id = ? ORDER BY id", (session_id,)
        ).fetchall()
        turns = conn.execute(
            "SELECT * FROM analysis_turns WHERE session_id = ? ORDER BY id DESC", (session_id,)
        ).fetchall()
        result["messages"] = [_copilot_message_row_to_dict(item) for item in messages]
        result["turns"] = [_turn_row_to_dict(conn, item) for item in turns]
        return result


def get_copilot_turn(turn_id: int) -> dict | None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM analysis_turns WHERE id = ?", (turn_id,)).fetchone()
        return _turn_row_to_dict(conn, row) if row else None


def create_copilot_message_and_turn(session_id: int, content: str) -> tuple[dict, dict] | None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        session = conn.execute(
            "SELECT id, active_report_id FROM copilot_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if session is None:
            return None
        message_cursor = conn.execute(
            "INSERT INTO copilot_messages (session_id, role, content) VALUES (?, 'user', ?)",
            (session_id, content.strip()),
        )
        previous_turn = conn.execute(
            "SELECT id FROM analysis_turns WHERE session_id = ? ORDER BY id DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        input_type = "follow_up" if session["active_report_id"] is not None else "initial_jd"
        turn_cursor = conn.execute(
            """
            INSERT INTO analysis_turns (
                session_id, input_message_id, report_id, parent_turn_id, input_type
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                session_id,
                message_cursor.lastrowid,
                session["active_report_id"],
                previous_turn["id"] if previous_turn else None,
                input_type,
            ),
        )
        conn.execute(
            "UPDATE copilot_messages SET turn_id = ? WHERE id = ?",
            (turn_cursor.lastrowid, message_cursor.lastrowid),
        )
        conn.execute(
            "UPDATE copilot_sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (session_id,),
        )
        message = conn.execute(
            "SELECT * FROM copilot_messages WHERE id = ?", (message_cursor.lastrowid,)
        ).fetchone()
        turn = conn.execute(
            "SELECT * FROM analysis_turns WHERE id = ?", (turn_cursor.lastrowid,)
        ).fetchone()
        conn.commit()
        return _copilot_message_row_to_dict(message), _turn_row_to_dict(conn, turn)


def get_turn_context(turn_id: int) -> dict | None:
    """读取运行 Agent 所需的受控上下文，避免服务层自行拼 SQL。"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        turn = conn.execute("SELECT * FROM analysis_turns WHERE id = ?", (turn_id,)).fetchone()
        if turn is None:
            return None
        session = conn.execute(
            "SELECT * FROM copilot_sessions WHERE id = ?", (turn["session_id"],)
        ).fetchone()
        message = conn.execute(
            "SELECT * FROM copilot_messages WHERE id = ?", (turn["input_message_id"],)
        ).fetchone()
        resume = None
        if session["resume_version_id"] is not None:
            resume = conn.execute(
                "SELECT * FROM resume_versions WHERE id = ?", (session["resume_version_id"],)
            ).fetchone()
        report = None
        report_id = turn["report_id"] or session["active_report_id"]
        if report_id is not None:
            report = conn.execute(
                "SELECT * FROM reports WHERE id = ?", (report_id,)
            ).fetchone()
        return {
            "turn": dict(turn),
            "session": dict(session),
            "message": dict(message) if message else None,
            "resume": dict(resume) if resume else None,
            "report": dict(report) if report else None,
        }


def update_copilot_turn(
    turn_id: int,
    status: str,
    stage: str | None = None,
    progress: int | None = None,
    error_message: str = "",
    report_id: int | None = None,
) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            UPDATE analysis_turns
            SET status = ?, stage = COALESCE(?, stage), progress = COALESCE(?, progress),
                error_message = ?, report_id = COALESCE(?, report_id),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, stage, progress, error_message, report_id, turn_id),
        )
        if report_id is not None:
            conn.execute(
                "UPDATE copilot_sessions SET active_report_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = (SELECT session_id FROM analysis_turns WHERE id = ?)",
                (report_id, turn_id),
            )
        conn.commit()


def save_copilot_artifact(
    turn_id: int, artifact_type: str, payload: dict, status: str = "ready"
) -> dict:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            """
            INSERT INTO analysis_artifacts (turn_id, artifact_type, payload_json, status)
            VALUES (?, ?, ?, ?)
            """,
            (turn_id, artifact_type, json.dumps(payload, ensure_ascii=False), status),
        )
        row = conn.execute(
            "SELECT * FROM analysis_artifacts WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        conn.commit()
        return _artifact_row_to_dict(row)


def update_copilot_artifact(
    artifact_id: int, payload: dict, status: str = "ready"
) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE analysis_artifacts SET payload_json = ?, status = ? WHERE id = ?",
            (json.dumps(payload, ensure_ascii=False), status, artifact_id),
        )
        conn.commit()


def save_copilot_assistant_message(session_id: int, turn_id: int, content: str) -> dict:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            """
            INSERT INTO copilot_messages (session_id, turn_id, role, content)
            VALUES (?, ?, 'assistant', ?)
            """,
            (session_id, turn_id, content),
        )
        conn.execute(
            "UPDATE copilot_sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (session_id,),
        )
        row = conn.execute(
            "SELECT * FROM copilot_messages WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        conn.commit()
        return _copilot_message_row_to_dict(row)


def create_artifact_decision(artifact_id: int, decision: str, note: str) -> dict | None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        if conn.execute(
            "SELECT 1 FROM analysis_artifacts WHERE id = ?", (artifact_id,)
        ).fetchone() is None:
            return None
        cursor = conn.execute(
            "INSERT INTO artifact_decisions (artifact_id, decision, note) VALUES (?, ?, ?)",
            (artifact_id, decision, note),
        )
        row = conn.execute(
            "SELECT * FROM artifact_decisions WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        conn.commit()
        return _with_display_times(dict(row))
