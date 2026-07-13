import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.config import settings
from app.schemas import JobPost
from app.schemas.resume import ResumeProfile, ResumeVersionCreate

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


        _ensure_report_columns(conn)
        _ensure_job_post_columns(conn)
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
                created_at
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
                created_at
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
