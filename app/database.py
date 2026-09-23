import sqlite3
import json
import re
from datetime import datetime, timedelta, timezone
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
    InterviewReviewCreate,
    InterviewReviewUpdate,
    ResumeSuggestionUpdate,
    ResumeVersionFromSuggestionsCreate,
)

DB_PATH = settings.database_path
DISPLAY_TIMEZONE = ZoneInfo("Asia/Shanghai")

SQLITE_TIMEOUT_SECONDS = 30
SQLITE_BUSY_TIMEOUT_MILLISECONDS = 30_000


def connect_db() -> sqlite3.Connection:
    """Create a consistently configured SQLite connection.

    Foreign keys are a per-connection SQLite setting, so every production
    connection must enable them explicitly. WAL and a busy timeout reduce
    contention between API reads and background analysis writes.
    """
    connection = sqlite3.connect(DB_PATH, timeout=SQLITE_TIMEOUT_SECONDS)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MILLISECONDS}")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    return connection


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

RESUME_VERSION_EXTRA_COLUMNS = {
    "parent_resume_version_id": "INTEGER",
    "source_report_id": "INTEGER",
}

TURN_EXTRA_COLUMNS = {
    "stage": "TEXT NOT NULL DEFAULT 'queued'",
    "progress": "INTEGER NOT NULL DEFAULT 0",
    "parent_turn_id": "INTEGER",
    "input_type": "TEXT NOT NULL DEFAULT 'initial_jd'",
}


ACTION_ITEM_EXTRA_COLUMNS = {
    "source_type": "TEXT NOT NULL DEFAULT 'report'",
    "source_turn_id": "INTEGER",
    "source_job_target_id": "INTEGER",
    "source_interview_review_id": "INTEGER",
    "archived_at": "TIMESTAMP",
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
    with connect_db() as conn:
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
                source_type TEXT NOT NULL DEFAULT 'report',
                source_turn_id INTEGER,
                source_job_target_id INTEGER,
                source_interview_review_id INTEGER,
                archived_at TIMESTAMP,
                UNIQUE(report_id, skill, title),
                FOREIGN KEY (report_id) REFERENCES reports(id),
                FOREIGN KEY (resume_version_id) REFERENCES resume_versions(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS resume_suggestions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_id INTEGER NOT NULL,
                resume_version_id INTEGER NOT NULL,
                suggestion_type TEXT NOT NULL,
                source_context TEXT NOT NULL DEFAULT "",
                suggested_text TEXT NOT NULL,
                edited_text TEXT NOT NULL DEFAULT "",
                status TEXT NOT NULL DEFAULT "pending",
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                confirmed_at TIMESTAMP,
                UNIQUE(report_id, suggestion_type, suggested_text),
                FOREIGN KEY (report_id) REFERENCES reports(id),
                FOREIGN KEY (resume_version_id) REFERENCES resume_versions(id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_resume_suggestions_report ON resume_suggestions(report_id, status)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS interview_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_target_id INTEGER NOT NULL,
                report_id INTEGER NOT NULL,
                round_number INTEGER NOT NULL DEFAULT 1,
                occurred_at TIMESTAMP,
                questions_json TEXT NOT NULL DEFAULT '[]',
                performance TEXT NOT NULL DEFAULT 'mixed',
                feedback TEXT NOT NULL DEFAULT '',
                result TEXT NOT NULL DEFAULT 'unknown',
                missing_skills_json TEXT NOT NULL DEFAULT '[]',
                conclusion TEXT NOT NULL DEFAULT '',
                actions_confirmed_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(job_target_id, round_number),
                FOREIGN KEY (job_target_id) REFERENCES job_targets(id),
                FOREIGN KEY (report_id) REFERENCES reports(id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_interview_reviews_target ON interview_reviews(job_target_id, round_number)"
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
            """
            CREATE TABLE IF NOT EXISTS analysis_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                turn_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                current_stage TEXT NOT NULL DEFAULT 'queued',
                pipeline_version TEXT NOT NULL,
                error_message TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (turn_id) REFERENCES analysis_turns(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_stage_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                analysis_run_id INTEGER NOT NULL,
                stage TEXT NOT NULL,
                status TEXT NOT NULL,
                input_json TEXT NOT NULL,
                output_json TEXT NOT NULL,
                validation_error TEXT NOT NULL DEFAULT '',
                retry_count INTEGER NOT NULL DEFAULT 0,
                latency_ms INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (analysis_run_id) REFERENCES analysis_runs(id),
                UNIQUE(analysis_run_id, stage)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS requirement_evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                analysis_run_id INTEGER NOT NULL,
                chunks_json TEXT NOT NULL DEFAULT '[]',
                requirement_json TEXT NOT NULL,
                candidates_json TEXT NOT NULL,
                decision_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (analysis_run_id) REFERENCES analysis_runs(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS evidence_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                turn_id INTEGER NOT NULL,
                analysis_run_id INTEGER NOT NULL,
                requirement_id TEXT NOT NULL,
                verdict TEXT NOT NULL,
                corrected_status TEXT,
                evidence_ids_json TEXT NOT NULL DEFAULT '[]',
                note TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (turn_id) REFERENCES analysis_turns(id),
                FOREIGN KEY (analysis_run_id) REFERENCES analysis_runs(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS resume_market_search_preferences (
                resume_version_id INTEGER PRIMARY KEY,
                auto_search_enabled INTEGER NOT NULL DEFAULT 0,
                city TEXT NOT NULL DEFAULT '',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (resume_version_id) REFERENCES resume_versions(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS resume_market_search_triggers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                resume_version_id INTEGER NOT NULL,
                source_turn_id INTEGER NOT NULL UNIQUE,
                analysis_task_id INTEGER NOT NULL,
                report_id INTEGER,
                target_role TEXT NOT NULL,
                city TEXT NOT NULL DEFAULT '',
                trigger_mode TEXT NOT NULL DEFAULT 'auto',
                status TEXT NOT NULL DEFAULT 'pending',
                reason TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (resume_version_id) REFERENCES resume_versions(id),
                FOREIGN KEY (source_turn_id) REFERENCES analysis_turns(id),
                FOREIGN KEY (analysis_task_id) REFERENCES analysis_tasks(id),
                FOREIGN KEY (report_id) REFERENCES reports(id)
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
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_analysis_runs_turn ON analysis_runs(turn_id, id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_stage_runs_run ON agent_stage_runs(analysis_run_id, id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_evidence_feedback_run ON evidence_feedback(analysis_run_id, requirement_id, id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_market_triggers_resume ON resume_market_search_triggers(resume_version_id, created_at)"
        )


        _ensure_report_columns(conn)
        _ensure_resume_version_columns(conn)
        _ensure_job_post_columns(conn)
        _ensure_session_columns(conn)
        _ensure_turn_columns(conn)
        _ensure_requirement_evidence_columns(conn)
        _ensure_action_item_columns(conn)
        # 历史报告可能是在 resume_suggestions 表加入前生成的。启动时补齐
        # 可从结构化报告安全提取的建议，确保工作台和简历页看到同一事实。
        _backfill_report_suggestions(conn)
        conn.commit()


def recover_stale_background_tasks(stale_after_seconds: int) -> dict[str, int]:
    """Mark abandoned in-process background work as failed after a restart.

    FastAPI BackgroundTasks are not durable. Persisted rows must not remain in
    a misleading pending/running state when the process that owned the work is
    gone. The next queue implementation can replace this with re-enqueueing.
    """
    threshold = max(60, int(stale_after_seconds))
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=threshold)
    cutoff_text = cutoff.strftime("%Y-%m-%d %H:%M:%S")
    task_error = "服务重启或任务超时，后台分析未完成，请重新发起。"
    turn_error = "服务重启或分析超时，本次分析未完成，请重新发起。"

    with connect_db() as conn:
        task_cursor = conn.execute(
            """
            UPDATE analysis_tasks
            SET status = 'failed', progress = 100,
                error_message = ?, updated_at = CURRENT_TIMESTAMP
            WHERE status IN ('pending', 'running') AND updated_at < ?
            """,
            (task_error, cutoff_text),
        )
        turn_cursor = conn.execute(
            """
            UPDATE analysis_turns
            SET status = 'failed', progress = 100,
                stage = 'failed', error_message = ?, updated_at = CURRENT_TIMESTAMP
            WHERE status IN ('pending', 'running') AND updated_at < ?
            """,
            (turn_error, cutoff_text),
        )
        conn.commit()

    return {
        "analysis_tasks": task_cursor.rowcount,
        "analysis_turns": turn_cursor.rowcount,
    }


def _ensure_resume_version_columns(conn: sqlite3.Connection) -> None:
    existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(resume_versions)").fetchall()}
    for column_name, column_type in RESUME_VERSION_EXTRA_COLUMNS.items():
        if column_name not in existing_columns:
            conn.execute(f"ALTER TABLE resume_versions ADD COLUMN {column_name} {column_type}")


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


def _ensure_action_item_columns(conn: sqlite3.Connection) -> None:
    existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(action_items)").fetchall()}
    for column_name, column_type in ACTION_ITEM_EXTRA_COLUMNS.items():
        if column_name not in existing_columns:
            conn.execute(f"ALTER TABLE action_items ADD COLUMN {column_name} {column_type}")

def _ensure_requirement_evidence_columns(conn: sqlite3.Connection) -> None:
    """Add pipeline evidence fields to databases created by the first M1 draft."""
    existing_columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(requirement_evidence)").fetchall()
    }
    if "chunks_json" not in existing_columns:
        conn.execute(
            "ALTER TABLE requirement_evidence ADD COLUMN chunks_json TEXT NOT NULL DEFAULT '[]'"
        )


def create_analysis_run(turn_id: int, pipeline_version: str) -> int:
    with connect_db() as conn:
        cursor = conn.execute(
            "INSERT INTO analysis_runs (turn_id, pipeline_version) VALUES (?, ?)",
            (turn_id, pipeline_version),
        )
        conn.commit()
        return int(cursor.lastrowid)


def update_analysis_run(
    run_id: int,
    *,
    status: str,
    current_stage: str,
    error_message: str = "",
) -> None:
    with connect_db() as conn:
        conn.execute(
            """
            UPDATE analysis_runs
            SET status = ?, current_stage = ?, error_message = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, current_stage, error_message, run_id),
        )
        conn.commit()


def save_agent_stage_run(
    *,
    run_id: int,
    stage: str,
    status: str,
    input_json: str,
    output_json: str,
    validation_error: str = "",
    retry_count: int = 0,
    latency_ms: int | None = None,
) -> int:
    with connect_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO agent_stage_runs (
                analysis_run_id, stage, status, input_json, output_json,
                validation_error, retry_count, latency_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(analysis_run_id, stage) DO UPDATE SET
                status = excluded.status,
                input_json = excluded.input_json,
                output_json = excluded.output_json,
                validation_error = excluded.validation_error,
                retry_count = excluded.retry_count,
                latency_ms = excluded.latency_ms
            """,
            (run_id, stage, status, input_json, output_json, validation_error, retry_count, latency_ms),
        )
        conn.commit()
        return int(cursor.lastrowid)


def save_requirement_evidence(
    *,
    run_id: int,
    chunks_json: str = "[]",
    requirement_json: str,
    candidates_json: str,
    decision_json: str | None,
) -> int:
    with connect_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO requirement_evidence (
                analysis_run_id, chunks_json, requirement_json, candidates_json, decision_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (run_id, chunks_json, requirement_json, candidates_json, decision_json),
        )
        conn.commit()
        return int(cursor.lastrowid)


def list_analysis_runs(turn_id: int) -> list[dict]:
    with connect_db() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM analysis_runs WHERE turn_id = ? ORDER BY id", (turn_id,)
        ).fetchall()
        return [dict(row) for row in rows]


def list_agent_stage_runs(run_id: int) -> list[dict]:
    with connect_db() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM agent_stage_runs WHERE analysis_run_id = ? ORDER BY id", (run_id,)
        ).fetchall()
        return [dict(row) for row in rows]


def get_analysis_evidence_chain(turn_id: int) -> dict | None:
    """Return only structured evidence data for the latest run of a turn."""
    with connect_db() as conn:
        conn.row_factory = sqlite3.Row
        run = conn.execute(
            """
            SELECT id, status, current_stage, pipeline_version
            FROM analysis_runs
            WHERE turn_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (turn_id,),
        ).fetchone()
        if run is None:
            return None
        rows = conn.execute(
            """
            SELECT chunks_json, requirement_json, candidates_json, decision_json
            FROM requirement_evidence
            WHERE analysis_run_id = ?
            ORDER BY id
            """,
            (run["id"],),
        ).fetchall()
        feedback_rows = conn.execute(
            """
            SELECT id, turn_id, analysis_run_id, requirement_id, verdict,
                   corrected_status, evidence_ids_json, note, created_at
            FROM evidence_feedback
            WHERE analysis_run_id = ? AND turn_id = ?
            ORDER BY id
            """,
            (run["id"], turn_id),
        ).fetchall()

    def parse_json(value: str | None, fallback: object) -> object:
        try:
            return json.loads(value or "")
        except json.JSONDecodeError:
            return fallback

    latest_feedback: dict[str, dict] = {}
    for feedback in feedback_rows:
        latest_feedback[feedback["requirement_id"]] = _evidence_feedback_row_to_dict(feedback)

    def requirement_id(row: sqlite3.Row) -> str | None:
        value = parse_json(row["requirement_json"], {})
        return value.get("id") if isinstance(value, dict) else None

    return {
        "analysis_run_id": run["id"],
        "status": run["status"],
        "current_stage": run["current_stage"],
        "pipeline_version": run["pipeline_version"],
        "items": [
            {
                "chunks": list(parse_json(row["chunks_json"], {}).values())
                if isinstance(parse_json(row["chunks_json"], {}), dict)
                else parse_json(row["chunks_json"], []),
                "requirement": parse_json(row["requirement_json"], {}),
                "candidates": parse_json(row["candidates_json"], []),
                "decision": parse_json(row["decision_json"], None),
                "review": latest_feedback.get(requirement_id(row)),
            }
            for row in rows
        ],
    }


def _evidence_feedback_row_to_dict(row: sqlite3.Row | dict) -> dict:
    evidence_ids_json = row["evidence_ids_json"]
    try:
        evidence_ids = json.loads(evidence_ids_json or "[]")
    except json.JSONDecodeError:
        evidence_ids = []
    if not isinstance(evidence_ids, list):
        evidence_ids = []
    return {
        "id": int(row["id"]),
        "turn_id": int(row["turn_id"]),
        "analysis_run_id": int(row["analysis_run_id"]),
        "requirement_id": row["requirement_id"],
        "verdict": row["verdict"],
        "corrected_status": row["corrected_status"] or None,
        "evidence_ids": evidence_ids,
        "note": row["note"] or "",
        "created_at": row["created_at"],
    }


def _get_evidence_run_context(
    conn: sqlite3.Connection, run_id: int, turn_id: int
) -> sqlite3.Row:
    run = conn.execute(
        "SELECT id, turn_id FROM analysis_runs WHERE id = ? AND turn_id = ?",
        (run_id, turn_id),
    ).fetchone()
    if run is None:
        raise ValueError("analysis_run 与 turn 不匹配")
    return run


def create_evidence_feedback(
    *,
    turn_id: int,
    analysis_run_id: int,
    requirement_id: str,
    verdict: str,
    corrected_status: str | None,
    evidence_ids: list[str],
    note: str,
) -> dict:
    """Persist review feedback only against the exact run and requirement."""
    if len(set(evidence_ids)) != len(evidence_ids):
        raise ValueError("evidence_ids 不可重复")
    if verdict == "corrected" and corrected_status is None:
        raise ValueError("corrected 必须指定 corrected_status")
    if verdict != "corrected" and corrected_status is not None:
        raise ValueError("confirmed/rejected 不应指定 corrected_status")
    if corrected_status == "missing_evidence" and evidence_ids:
        raise ValueError("missing_evidence 不允许关联 evidence_ids")
    if corrected_status in {"supported", "partial"} and not evidence_ids:
        raise ValueError("supported/partial 修正必须关联至少一条 evidence_id")
    with connect_db() as conn:
        conn.row_factory = sqlite3.Row
        _get_evidence_run_context(conn, analysis_run_id, turn_id)
        evidence_row = conn.execute(
            """
            SELECT requirement_json, candidates_json
            FROM requirement_evidence
            WHERE analysis_run_id = ?
            ORDER BY id
            """,
            (analysis_run_id,),
        ).fetchall()
        requirement_row = None
        for row in evidence_row:
            try:
                requirement = json.loads(row["requirement_json"] or "{}")
            except json.JSONDecodeError:
                requirement = {}
            if requirement.get("id") == requirement_id:
                requirement_row = row
                break
        if requirement_row is None:
            raise ValueError("requirement 不存在")
        try:
            candidate_items = json.loads(requirement_row["candidates_json"] or "[]")
        except json.JSONDecodeError:
            candidate_items = []
        candidate_map = {
            item.get("id"): item for item in candidate_items if isinstance(item, dict)
        }
        if any(
            evidence_id not in candidate_map
            or candidate_map[evidence_id].get("requirement_id") != requirement_id
            for evidence_id in evidence_ids
        ):
            raise ValueError("evidence_id 不属于该 requirement")
        cursor = conn.execute(
            """
            INSERT INTO evidence_feedback (
                turn_id, analysis_run_id, requirement_id, verdict,
                corrected_status, evidence_ids_json, note
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                turn_id,
                analysis_run_id,
                requirement_id,
                verdict,
                corrected_status,
                json.dumps(evidence_ids, ensure_ascii=False),
                note,
            ),
        )
        row = conn.execute(
            "SELECT * FROM evidence_feedback WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        conn.commit()
        return _evidence_feedback_row_to_dict(row)


def get_latest_evidence_feedback_by_run(
    analysis_run_id: int, turn_id: int | None = None
) -> dict[str, dict]:
    """Return the newest review per requirement after validating run ownership."""
    with connect_db() as conn:
        conn.row_factory = sqlite3.Row
        run = conn.execute(
            "SELECT turn_id FROM analysis_runs WHERE id = ?", (analysis_run_id,)
        ).fetchone()
        if run is None or (turn_id is not None and int(run["turn_id"]) != turn_id):
            return {}
        rows = conn.execute(
            "SELECT * FROM evidence_feedback WHERE analysis_run_id = ? AND turn_id = ? ORDER BY id",
            (analysis_run_id, int(run["turn_id"])),
        ).fetchall()
        latest: dict[str, dict] = {}
        for row in rows:
            latest[row["requirement_id"]] = _evidence_feedback_row_to_dict(row)
        return latest


def get_turn_evidence_feedback_summary(turn_id: int) -> dict[str, dict]:
    """Return reviews for the latest analysis run belonging to this turn."""
    with connect_db() as conn:
        conn.row_factory = sqlite3.Row
        run = conn.execute(
            "SELECT id FROM analysis_runs WHERE turn_id = ? ORDER BY id DESC LIMIT 1",
            (turn_id,),
        ).fetchone()
    if run is None:
        return {}
    return get_latest_evidence_feedback_by_run(int(run["id"]), turn_id)


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
    with connect_db() as conn:
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
        _ensure_report_suggestions(conn, int(cursor.lastrowid))
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
    with connect_db() as conn:
        conn.execute(
            """
            UPDATE reports
            SET score = ?, markdown_report = ?, parsed_result = ?, parse_status = ?,
                raw_result = COALESCE(?, raw_result)
            WHERE id = ?
            """,
            (score, markdown_report, parsed_result, parse_status, raw_result, report_id),
        )
        _ensure_report_suggestions(conn, report_id)
        conn.commit()


def save_job_posts(report_id: int, posts: list[JobPost]) -> None:
    """保存一次报告关联的岗位搜索结果。

    report_id 来自 save_report() 返回值，用它把"报告"和"参考岗位样本"关联起来。
    """
    if not posts:
        return

    with connect_db() as conn:
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
    with connect_db() as conn:
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
    with connect_db() as conn:
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
    with connect_db() as conn:
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


def confirm_job_post(post_id: int) -> dict | None:
    """记录用户已打开原链接并确认岗位仍可投递。"""
    with connect_db() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM job_posts WHERE id = ?", (post_id,)).fetchone()
        if row is None:
            return None
        if row["status"] == "expired":
            raise ValueError("已过期岗位不能确认有效")
        if row["status"] != "active":
            conn.execute(
                """
                UPDATE job_posts
                SET status = 'active', verification_status = 'user_confirmed',
                    verification_reason = '用户已打开原链接并确认仍可投递'
                WHERE id = ?
                """,
                (post_id,),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM job_posts WHERE id = ?", (post_id,)).fetchone()
        return dict(row)

def create_analysis_task(task_type: str) -> int:
    """创建异步分析任务，返回 task_id。"""
    with connect_db() as conn:
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
    with connect_db() as conn:
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
    with connect_db() as conn:
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
    with connect_db() as conn:
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
    with connect_db() as conn:
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


def get_resume_market_search_preference(resume_version_id: int) -> dict | None:
    """读取单个简历版本的岗位自动搜索偏好，默认关闭。"""
    with connect_db() as conn:
        conn.row_factory = sqlite3.Row
        if conn.execute("SELECT 1 FROM resume_versions WHERE id = ?", (resume_version_id,)).fetchone() is None:
            return None
        row = conn.execute(
            "SELECT resume_version_id, auto_search_enabled, city, updated_at "
            "FROM resume_market_search_preferences WHERE resume_version_id = ?",
            (resume_version_id,),
        ).fetchone()
        if row is None:
            return {
                "resume_version_id": resume_version_id,
                "auto_search_enabled": False,
                "city": "",
                "updated_at": None,
            }
        result = _with_display_times(dict(row))
        result["auto_search_enabled"] = bool(result["auto_search_enabled"])
        return result


def update_resume_market_search_preference(
    resume_version_id: int, *, auto_search_enabled: bool, city: str
) -> dict:
    with connect_db() as conn:
        conn.row_factory = sqlite3.Row
        _ensure_resume_version_exists(conn, resume_version_id)
        conn.execute(
            """
            INSERT INTO resume_market_search_preferences
                (resume_version_id, auto_search_enabled, city)
            VALUES (?, ?, ?)
            ON CONFLICT(resume_version_id) DO UPDATE SET
                auto_search_enabled = excluded.auto_search_enabled,
                city = excluded.city,
                updated_at = CURRENT_TIMESTAMP
            """,
            (resume_version_id, int(auto_search_enabled), city.strip()),
        )
        row = conn.execute(
            "SELECT resume_version_id, auto_search_enabled, city, updated_at "
            "FROM resume_market_search_preferences WHERE resume_version_id = ?",
            (resume_version_id,),
        ).fetchone()
        conn.commit()
        result = _with_display_times(dict(row))
        result["auto_search_enabled"] = bool(result["auto_search_enabled"])
        return result


def _market_trigger_row_to_dict(row: sqlite3.Row) -> dict:
    return _with_display_times(dict(row))


def get_resume_market_search_triggers(resume_version_id: int) -> list[dict]:
    with connect_db() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM resume_market_search_triggers "
            "WHERE resume_version_id = ? ORDER BY created_at DESC, id DESC",
            (resume_version_id,),
        ).fetchall()
        return [_market_trigger_row_to_dict(row) for row in rows]


def get_resume_market_search_trigger_by_turn(turn_id: int) -> dict | None:
    with connect_db() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM resume_market_search_triggers WHERE source_turn_id = ?",
            (turn_id,),
        ).fetchone()
        return _market_trigger_row_to_dict(row) if row else None


def create_resume_market_search_trigger(
    *,
    resume_version_id: int,
    source_turn_id: int,
    analysis_task_id: int,
    report_id: int | None,
    target_role: str,
    city: str,
    trigger_mode: str = "auto",
) -> dict:
    """创建可审计的自动搜索触发记录，并校验回合与简历版本归属。"""
    with connect_db() as conn:
        conn.row_factory = sqlite3.Row
        _ensure_resume_version_exists(conn, resume_version_id)
        context = conn.execute(
            """
            SELECT s.resume_version_id
            FROM analysis_turns t
            JOIN copilot_sessions s ON s.id = t.session_id
            WHERE t.id = ?
            """,
            (source_turn_id,),
        ).fetchone()
        if context is None:
            raise ValueError("source_turn_id 不存在")
        if context["resume_version_id"] != resume_version_id:
            raise ValueError("source_turn_id 与简历版本不匹配")
        existing = conn.execute(
            "SELECT * FROM resume_market_search_triggers WHERE source_turn_id = ?",
            (source_turn_id,),
        ).fetchone()
        if existing is not None:
            return _market_trigger_row_to_dict(existing)
        try:
            cursor = conn.execute(
                """
                INSERT INTO resume_market_search_triggers (
                    resume_version_id, source_turn_id, analysis_task_id, report_id,
                    target_role, city, trigger_mode, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
                """,
                (resume_version_id, source_turn_id, analysis_task_id, report_id,
                 target_role.strip(), city.strip(), trigger_mode),
            )
        except sqlite3.IntegrityError:
            existing = conn.execute(
                "SELECT * FROM resume_market_search_triggers WHERE source_turn_id = ?",
                (source_turn_id,),
            ).fetchone()
            if existing is not None:
                conn.rollback()
                return _market_trigger_row_to_dict(existing)
            raise
        row = conn.execute(
            "SELECT * FROM resume_market_search_triggers WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        conn.commit()
        return _market_trigger_row_to_dict(row)


def update_resume_market_search_trigger(
    trigger_id: int, *, status: str, report_id: int | None = None, reason: str = ""
) -> dict | None:
    with connect_db() as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            UPDATE resume_market_search_triggers
            SET status = ?, report_id = COALESCE(?, report_id), reason = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, report_id, reason[:500], trigger_id),
        )
        row = conn.execute(
            "SELECT * FROM resume_market_search_triggers WHERE id = ?", (trigger_id,)
        ).fetchone()
        conn.commit()
        return _market_trigger_row_to_dict(row) if row else None


def get_turn_auto_market_search_context(turn_id: int) -> dict | None:
    """读取自动搜索资格所需的受控上下文，不返回原始文本。"""
    with connect_db() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT t.id AS turn_id, t.status AS turn_status, t.report_id,
                   s.id AS session_id, s.resume_version_id, s.target_role AS session_target_role,
                   r.target_role AS resume_target_role, r.raw_text, r.id AS resume_id
            FROM analysis_turns t
            JOIN copilot_sessions s ON s.id = t.session_id
            LEFT JOIN resume_versions r ON r.id = s.resume_version_id
            WHERE t.id = ?
            """,
            (turn_id,),
        ).fetchone()
        if row is None:
            return None
        artifact = conn.execute(
            """
            SELECT payload_json FROM analysis_artifacts
            WHERE turn_id = ? AND artifact_type = 'fit_strategy'
            ORDER BY id DESC LIMIT 1
            """,
            (turn_id,),
        ).fetchone()
        strategy = json.loads(artifact["payload_json"]) if artifact else {}
        return {
            "turn_id": row["turn_id"],
            "turn_status": row["turn_status"],
            "report_id": row["report_id"],
            "session_id": row["session_id"],
            "resume_version_id": row["resume_version_id"],
            "resume_id": row["resume_id"],
            "target_role": (row["session_target_role"] or row["resume_target_role"] or "").strip(),
            "resume_text": row["raw_text"] or "",
            "fit_strategy": strategy,
        }


def get_resume_analysis_history(resume_version_id: int) -> dict | None:
    """聚合单个简历版本的副驾历史和市场触发记录，过滤敏感原文字段。"""
    with connect_db() as conn:
        conn.row_factory = sqlite3.Row
        version = conn.execute(
            "SELECT id, resume_id, version_name, target_role, created_at FROM resume_versions WHERE id = ?",
            (resume_version_id,),
        ).fetchone()
        if version is None:
            return None
        sessions = conn.execute(
            "SELECT * FROM copilot_sessions WHERE resume_version_id = ? ORDER BY created_at DESC, id DESC",
            (resume_version_id,),
        ).fetchall()
        session_payloads = []
        for session in sessions:
            messages = conn.execute(
                "SELECT id, session_id, turn_id, role, content, created_at FROM copilot_messages WHERE session_id = ? ORDER BY id",
                (session["id"],),
            ).fetchall()
            turns = conn.execute(
                "SELECT id, session_id, status, stage, progress, report_id, input_type, created_at, updated_at FROM analysis_turns WHERE session_id = ? ORDER BY id DESC",
                (session["id"],),
            ).fetchall()
            turn_payloads = []
            for turn in turns:
                artifacts = conn.execute(
                    "SELECT id, turn_id, artifact_type, payload_json, status, created_at FROM analysis_artifacts WHERE turn_id = ? AND artifact_type IN ('job_brief', 'evidence_map', 'fit_strategy', 'action_bundle') ORDER BY id",
                    (turn["id"],),
                ).fetchall()
                turn_payloads.append({
                    **_with_display_times(dict(turn)),
                    "artifacts": [
                        {
                            **_with_display_times({k: item[k] for k in item.keys() if k != "payload_json"}),
                            "payload": _scrub_history_value(json.loads(item["payload_json"])),
                        }
                        for item in artifacts
                    ],
                })
            session_payloads.append({
                **_with_display_times({k: session[k] for k in session.keys() if k != "active_report_id"}),
                "messages": [
                    {
                        **_with_display_times(dict(message)),
                        "content": _scrub_history_text(message["content"]),
                    }
                    for message in messages
                ],
                "turns": turn_payloads,
            })
        reports = conn.execute(
            """
            SELECT id, target_role, score, parse_status, parsed_result, created_at,
                   (SELECT COUNT(*) FROM job_posts p WHERE p.report_id = reports.id) AS job_post_count
            FROM reports WHERE resume_version_id = ? ORDER BY created_at DESC, id DESC
            """,
            (resume_version_id,),
        ).fetchall()
        report_payloads = []
        for report in reports:
            try:
                parsed = json.loads(report["parsed_result"] or "{}")
            except (TypeError, json.JSONDecodeError):
                parsed = {}
            summary = str(parsed.get("summary") or "")[:500]
            report_payloads.append({
                **_with_display_times({
                    "id": report["id"],
                    "target_role": report["target_role"],
                    "score": report["score"],
                    "parse_status": report["parse_status"] or "",
                    "created_at": report["created_at"],
                    "job_post_count": report["job_post_count"],
                }),
                "report_summary": summary,
            })
        result = {
            "resume_version_id": version["id"],
            "resume_id": version["resume_id"],
            "version_name": version["version_name"],
            "target_role": version["target_role"] or "",
            "created_at": version["created_at"],
            "sessions": session_payloads,
            "reports": report_payloads,
            "market_search_preference": get_resume_market_search_preference(resume_version_id),
            "market_search_triggers": get_resume_market_search_triggers(resume_version_id),
        }
        return _with_display_times(result)


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


_HISTORY_FORBIDDEN_KEYS = {
    "raw_result",
    "raw_output",
    "resume_text",
    "jd_text",
    "api_key",
    "embedding_api_key",
    "base_url",
}


def _scrub_history_value(value):
    """Remove debug/raw fields before structured history leaves the backend."""
    if isinstance(value, dict):
        return {
            key: _scrub_history_value(item)
            for key, item in value.items()
            if str(key).lower() not in _HISTORY_FORBIDDEN_KEYS
        }
    if isinstance(value, list):
        return [_scrub_history_value(item) for item in value]
    return value


def _scrub_history_text(value: str) -> str:
    """Redact obvious API-key forms in user-visible conversation history."""
    return re.sub(
        r"(?i)(api[_-]?key\s*[:=]\s*|sk-[a-z0-9_-]{16,})[^\s,;]+",
        "[REDACTED]",
        value,
    )


def create_job_target(payload: JobTargetCreate) -> dict:
    """从报告中的确认有效岗位创建投递目标，重复创建时返回已有记录。"""
    with connect_db() as conn:
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
            "SELECT parsed_result, resume_version_id, parse_status FROM reports WHERE id = ?",
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
        # 数据不足报告不会伪造 A/B/C 推荐；用户完成岗位链接核实后，
        # 可以用显式选择的优先级加入投递管道。
        if recommendation is None and report["parse_status"] == "skipped_insufficient_market_data":
            recommendation = {"level": payload.priority, "match_score": None}
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
    with connect_db() as conn:
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
    with connect_db() as conn:
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
    with connect_db() as conn:
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
    result["source_report_id"] = row["report_id"]
    result["source_type"] = row["source_type"] or "report"
    result["source_turn_id"] = row["source_turn_id"]
    result["source_job_target_id"] = row["source_job_target_id"]
    result["source_interview_review_id"] = row["source_interview_review_id"]
    target = None
    if row["source_job_target_id"]:
        target = conn.execute(
            "SELECT id, title FROM job_targets WHERE id = ?", (row["source_job_target_id"],)
        ).fetchone()
    result["source_job_title"] = target["title"] if target else ""
    return result

def create_action_items_from_report(
    report_id: int,
    payload: ActionItemsFromReportRequest,
    *,
    source_type: str = "report",
    source_turn_id: int | None = None,
    source_job_target_id: int | None = None,
    source_interview_review_id: int | None = None,
) -> list[dict]:
    """将已校验报告中的技能缺口转为可执行任务，并保留精确来源。"""
    with connect_db() as conn:
        conn.row_factory = sqlite3.Row
        report = conn.execute(
            "SELECT parsed_result, resume_version_id, parse_status FROM reports WHERE id = ?", (report_id,)
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
                "SELECT * FROM action_items WHERE report_id = ? AND skill = ? AND title = ? AND archived_at IS NULL",
                (report_id, skill, title),
            ).fetchone()
            if existing is not None:
                created_items.append(_action_item_row_to_dict(conn, existing))
                continue
            cursor = conn.execute(
                """
                INSERT INTO action_items (
                    report_id, resume_version_id, action_type, skill, title, priority, expected_output,
                    source_type, source_turn_id, source_job_target_id, source_interview_review_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report_id,
                    resume_version_id,
                    "skill_gap",
                    skill,
                    title,
                    "high" if index < 2 else "medium",
                    f"提交一个能够证明 {skill} 能力的项目链接、练习记录或简历版本。",
                    source_type,
                    source_turn_id,
                    source_job_target_id,
                    source_interview_review_id,
                ),
            )
            row = conn.execute("SELECT * FROM action_items WHERE id = ?", (cursor.lastrowid,)).fetchone()
            created_items.append(_action_item_row_to_dict(conn, row))
        conn.commit()
        return created_items


def list_action_items(
    status: str | None = None,
    resume_version_id: int | None = None,
    report_id: int | None = None,
    source_type: str | None = None,
    include_archived: bool = False,
) -> list[dict]:
    with connect_db() as conn:
        conn.row_factory = sqlite3.Row
        clauses: list[str] = []
        params: list[object] = []
        if not include_archived:
            clauses.append("archived_at IS NULL")
        if status:
            clauses.append("status = ?")
            params.append(status)
        if resume_version_id:
            clauses.append("resume_version_id = ?")
            params.append(resume_version_id)
        if report_id:
            clauses.append("report_id = ?")
            params.append(report_id)
        if source_type:
            clauses.append("source_type = ?")
            params.append(source_type)
        query = "SELECT * FROM action_items"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, updated_at DESC"
        rows = conn.execute(query, params).fetchall()
        return [_action_item_row_to_dict(conn, row) for row in rows]

def update_action_item(item_id: int, payload: ActionItemUpdate) -> dict | None:
    with connect_db() as conn:
        conn.row_factory = sqlite3.Row
        current = conn.execute("SELECT * FROM action_items WHERE id = ?", (item_id,)).fetchone()
        if current is None:
            return None
        if current["archived_at"] is not None:
            raise ValueError("已归档任务请先恢复")
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


def archive_action_item(item_id: int) -> dict | None:
    with connect_db() as conn:
        conn.row_factory = sqlite3.Row
        current = conn.execute("SELECT * FROM action_items WHERE id = ?", (item_id,)).fetchone()
        if current is None:
            return None
        conn.execute(
            "UPDATE action_items SET status = 'cancelled', archived_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (item_id,),
        )
        row = conn.execute("SELECT * FROM action_items WHERE id = ?", (item_id,)).fetchone()
        conn.commit()
        return _action_item_row_to_dict(conn, row)


def restore_action_item(item_id: int) -> dict | None:
    with connect_db() as conn:
        conn.row_factory = sqlite3.Row
        current = conn.execute("SELECT * FROM action_items WHERE id = ?", (item_id,)).fetchone()
        if current is None:
            return None
        conn.execute(
            "UPDATE action_items SET status = 'todo', archived_at = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (item_id,),
        )
        row = conn.execute("SELECT * FROM action_items WHERE id = ?", (item_id,)).fetchone()
        conn.commit()
        return _action_item_row_to_dict(conn, row)

def create_action_evidence(item_id: int, payload: ActionEvidenceCreate) -> dict | None:
    with connect_db() as conn:
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
    with connect_db() as conn:
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
                "SELECT status, COUNT(*) AS count FROM action_items WHERE archived_at IS NULL GROUP BY status"
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
    with connect_db() as conn:
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
    with connect_db() as conn:
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
    with connect_db() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM analysis_turns WHERE id = ?", (turn_id,)).fetchone()
        return _turn_row_to_dict(conn, row) if row else None


def get_copilot_report_source_turn(report_id: int) -> int | None:
    """Return the first analysis turn that produced a report."""
    with connect_db() as conn:
        row = conn.execute(
            "SELECT id FROM analysis_turns WHERE report_id = ? ORDER BY id ASC LIMIT 1",
            (report_id,),
        ).fetchone()
        return int(row[0]) if row else None


def list_recent_copilot_messages(session_id: int, limit: int = 4) -> list[dict]:
    """Read only a small recent window for follow-up context construction."""
    safe_limit = max(1, min(limit, 8))
    with connect_db() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, role, content, turn_id, created_at FROM copilot_messages "
            "WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, safe_limit),
        ).fetchall()
        return [dict(row) for row in reversed(rows)]


def create_copilot_message_and_turn(session_id: int, content: str) -> tuple[dict, dict] | None:
    with connect_db() as conn:
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
    with connect_db() as conn:
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
    with connect_db() as conn:
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
    with connect_db() as conn:
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
    with connect_db() as conn:
        conn.execute(
            "UPDATE analysis_artifacts SET payload_json = ?, status = ? WHERE id = ?",
            (json.dumps(payload, ensure_ascii=False), status, artifact_id),
        )
        conn.commit()


def save_copilot_assistant_message(session_id: int, turn_id: int, content: str) -> dict:
    with connect_db() as conn:
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
    with connect_db() as conn:
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

# --- Resume suggestion confirmation and interview review workflow ---

def _suggestion_row_to_dict(row: sqlite3.Row) -> dict:
    return _with_display_times(dict(row))


def _backfill_report_suggestions(conn: sqlite3.Connection) -> None:
    """为旧报告补齐可由结构化结果确定的简历建议。"""
    report_ids = conn.execute(
        "SELECT id FROM reports WHERE resume_version_id IS NOT NULL"
    ).fetchall()
    for row in report_ids:
        _ensure_report_suggestions(conn, int(row[0]))

def _ensure_report_suggestions(conn: sqlite3.Connection, report_id: int) -> None:
    conn.row_factory = sqlite3.Row
    report = conn.execute(
        "SELECT resume_version_id, parsed_result FROM reports WHERE id = ?", (report_id,)
    ).fetchone()
    if report is None or report["resume_version_id"] is None:
        return
    count = conn.execute(
        "SELECT COUNT(*) FROM resume_suggestions WHERE report_id = ?", (report_id,)
    ).fetchone()[0]
    if count:
        return
    try:
        parsed = json.loads(report["parsed_result"] or "{}")
    except (TypeError, json.JSONDecodeError):
        parsed = {}
    candidates: list[tuple[str, str, str]] = []
    for key, suggestion_type in (
        ("resume_bullets", "resume_bullet"),
        ("resume_improvement_suggestions", "resume_improvement"),
    ):
        values = parsed.get(key) or []
        if isinstance(values, str):
            values = [values]
        if isinstance(values, list):
            for value in values:
                if isinstance(value, str) and value.strip():
                    candidates.append((suggestion_type, key, value.strip()))
    for suggestion_type, source_context, suggested_text in candidates[:30]:
        conn.execute(
            """
            INSERT OR IGNORE INTO resume_suggestions
                (report_id, resume_version_id, suggestion_type, source_context, suggested_text)
            VALUES (?, ?, ?, ?, ?)
            """,
            (report_id, report["resume_version_id"], suggestion_type, source_context, suggested_text),
        )


def list_resume_suggestions(report_id: int) -> list[dict] | None:
    with connect_db() as conn:
        conn.row_factory = sqlite3.Row
        if conn.execute("SELECT 1 FROM reports WHERE id = ?", (report_id,)).fetchone() is None:
            return None
        _ensure_report_suggestions(conn, report_id)
        rows = conn.execute(
            "SELECT * FROM resume_suggestions WHERE report_id = ? ORDER BY id", (report_id,)
        ).fetchall()
        conn.commit()
        return [_suggestion_row_to_dict(row) for row in rows]


def update_resume_suggestion(
    suggestion_id: int, payload: ResumeSuggestionUpdate
) -> dict | None:
    with connect_db() as conn:
        conn.row_factory = sqlite3.Row
        current = conn.execute(
            "SELECT * FROM resume_suggestions WHERE id = ?", (suggestion_id,)
        ).fetchone()
        if current is None:
            return None
        edited_text = payload.edited_text.strip()
        if payload.status == "edited" and not edited_text:
            raise ValueError("编辑状态必须提供修改后的建议内容")
        confirmed_at = "CURRENT_TIMESTAMP" if payload.status in {"accepted", "edited", "rejected"} else None
        if confirmed_at:
            conn.execute(
                """
                UPDATE resume_suggestions
                SET status = ?, edited_text = ?, confirmed_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (payload.status, edited_text, suggestion_id),
            )
        else:
            conn.execute(
                "UPDATE resume_suggestions SET status = ?, edited_text = ?, confirmed_at = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (payload.status, edited_text, suggestion_id),
            )
        row = conn.execute("SELECT * FROM resume_suggestions WHERE id = ?", (suggestion_id,)).fetchone()
        conn.commit()
        return _suggestion_row_to_dict(row)


def create_resume_version_from_suggestions(
    payload: ResumeVersionFromSuggestionsCreate,
) -> dict:
    with connect_db() as conn:
        conn.row_factory = sqlite3.Row
        report = conn.execute(
            "SELECT resume_version_id FROM reports WHERE id = ?", (payload.report_id,)
        ).fetchone()
        if report is None:
            raise ValueError("报告不存在")
        if report["resume_version_id"] != payload.source_resume_version_id:
            raise ValueError("报告与来源简历版本不匹配")
        source = conn.execute(
            "SELECT id, resume_id, target_role FROM resume_versions WHERE id = ?",
            (payload.source_resume_version_id,),
        ).fetchone()
        if source is None:
            raise ValueError("来源简历版本不存在")
        ids = list(dict.fromkeys(payload.suggestion_ids))
        if not ids:
            raise ValueError("至少确认一条简历建议后才能创建新版本")
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"SELECT * FROM resume_suggestions WHERE report_id = ? AND resume_version_id = ? AND id IN ({placeholders})",
            (payload.report_id, payload.source_resume_version_id, *ids),
        ).fetchall()
        if len(rows) != len(ids):
            raise ValueError("存在不属于当前报告的简历建议")
        if any(row["status"] not in {"accepted", "edited"} for row in rows):
            raise ValueError("只能使用已接受或已编辑的简历建议创建新版本")
        profile = ResumeProfile.model_validate(payload.profile)
        conn.execute("UPDATE resumes SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (source["resume_id"],))
        cursor = conn.execute(
            """
            INSERT INTO resume_versions
                (resume_id, version_name, target_role, raw_text, profile_json,
                 parent_resume_version_id, source_report_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source["resume_id"], payload.version_name, payload.target_role or source["target_role"] or "",
                payload.raw_text, profile.model_dump_json(), payload.source_resume_version_id, payload.report_id,
            ),
        )
        row = conn.execute(
            "SELECT id, resume_id, version_name, target_role, raw_text, profile_json, created_at FROM resume_versions WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
        conn.commit()
        return _resume_version_row_to_dict(row)


def _interview_review_row_to_dict(row: sqlite3.Row) -> dict:
    result = _with_display_times(dict(row))
    result["occurred_at"] = format_datetime_for_display(result.get("occurred_at"))
    for field in ("questions_json", "missing_skills_json"):
        raw = result.pop(field, "[]")
        try:
            result[field.removesuffix("_json")] = json.loads(raw or "[]")
        except (TypeError, json.JSONDecodeError):
            result[field.removesuffix("_json")] = []
    return result


def _review_target(conn: sqlite3.Connection, job_target_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT id, report_id, status FROM job_targets WHERE id = ?", (job_target_id,)
    ).fetchone()


def create_interview_review(job_target_id: int, payload: InterviewReviewCreate) -> dict:
    with connect_db() as conn:
        conn.row_factory = sqlite3.Row
        target = _review_target(conn, job_target_id)
        if target is None:
            raise ValueError("投递目标不存在")
        if target["status"] not in {"interview", "offer", "rejected"}:
            raise ValueError("只有进入面试或面试后的岗位可以创建复盘")
        occurred_at = payload.occurred_at.isoformat() if payload.occurred_at else None
        try:
            cursor = conn.execute(
                """
                INSERT INTO interview_reviews
                    (job_target_id, report_id, round_number, occurred_at, questions_json,
                     performance, feedback, result, missing_skills_json, conclusion)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (job_target_id, target["report_id"], payload.round_number, occurred_at,
                 json.dumps(payload.questions, ensure_ascii=False), payload.performance,
                 payload.feedback, payload.result, json.dumps(payload.missing_skills, ensure_ascii=False),
                 payload.conclusion),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("该岗位的面试轮次已存在") from exc
        row = conn.execute("SELECT * FROM interview_reviews WHERE id = ?", (cursor.lastrowid,)).fetchone()
        conn.commit()
        return _interview_review_row_to_dict(row)


def list_interview_reviews(job_target_id: int) -> list[dict] | None:
    with connect_db() as conn:
        conn.row_factory = sqlite3.Row
        if _review_target(conn, job_target_id) is None:
            return None
        rows = conn.execute(
            "SELECT * FROM interview_reviews WHERE job_target_id = ? ORDER BY round_number, id",
            (job_target_id,),
        ).fetchall()
        return [_interview_review_row_to_dict(row) for row in rows]


def update_interview_review(review_id: int, payload: InterviewReviewUpdate) -> dict | None:
    with connect_db() as conn:
        conn.row_factory = sqlite3.Row
        current = conn.execute("SELECT * FROM interview_reviews WHERE id = ?", (review_id,)).fetchone()
        if current is None:
            return None
        values = {
            "round_number": payload.round_number if payload.round_number is not None else current["round_number"],
            "occurred_at": payload.occurred_at.isoformat() if payload.occurred_at else current["occurred_at"],
            "questions_json": json.dumps(payload.questions, ensure_ascii=False) if payload.questions is not None else current["questions_json"],
            "performance": payload.performance or current["performance"],
            "feedback": payload.feedback if payload.feedback is not None else current["feedback"],
            "result": payload.result or current["result"],
            "missing_skills_json": json.dumps(payload.missing_skills, ensure_ascii=False) if payload.missing_skills is not None else current["missing_skills_json"],
            "conclusion": payload.conclusion if payload.conclusion is not None else current["conclusion"],
        }
        try:
            conn.execute(
                """
                UPDATE interview_reviews SET round_number = ?, occurred_at = ?, questions_json = ?,
                    performance = ?, feedback = ?, result = ?, missing_skills_json = ?, conclusion = ?,
                    updated_at = CURRENT_TIMESTAMP WHERE id = ?
                """,
                (*values.values(), review_id),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("该岗位的面试轮次已存在") from exc
        row = conn.execute("SELECT * FROM interview_reviews WHERE id = ?", (review_id,)).fetchone()
        conn.commit()
        return _interview_review_row_to_dict(row)


def confirm_interview_actions(review_id: int, skills: list[str]) -> list[dict]:
    with connect_db() as conn:
        conn.row_factory = sqlite3.Row
        review = conn.execute("SELECT * FROM interview_reviews WHERE id = ?", (review_id,)).fetchone()
        if review is None:
            raise ValueError("面试复盘不存在")
        try:
            missing = json.loads(review["missing_skills_json"] or "[]")
        except json.JSONDecodeError:
            missing = []
        chosen = list(dict.fromkeys(skill.strip() for skill in (skills or missing) if skill.strip()))
        if not chosen:
            raise ValueError("至少确认一项待补能力")
        invalid = set(chosen) - set(missing)
        if invalid:
            raise ValueError("只能将本次复盘记录的待补能力转为行动项")
        target = conn.execute("SELECT report_id FROM job_targets WHERE id = ?", (review["job_target_id"],)).fetchone()
        if target is None:
            raise ValueError("投递目标不存在")
    items = create_action_items_from_report(
        int(target["report_id"]),
        ActionItemsFromReportRequest(skills=chosen),
        source_type="interview_review",
        source_job_target_id=int(review["job_target_id"]),
        source_interview_review_id=review_id,
    )
    with connect_db() as conn:
        conn.execute("UPDATE interview_reviews SET actions_confirmed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (review_id,))
        conn.commit()
    return items


def get_job_target_timeline(job_target_id: int) -> dict | None:
    with connect_db() as conn:
        conn.row_factory = sqlite3.Row
        target = conn.execute("SELECT * FROM job_targets WHERE id = ?", (job_target_id,)).fetchone()
        if target is None:
            return None
        events = conn.execute(
            "SELECT id, job_target_id, event_type, occurred_at, note FROM application_events WHERE job_target_id = ? ORDER BY occurred_at, id",
            (job_target_id,),
        ).fetchall()
        reviews = conn.execute(
            "SELECT * FROM interview_reviews WHERE job_target_id = ? ORDER BY round_number, id",
            (job_target_id,),
        ).fetchall()
        return {
            "target": _job_target_row_to_dict(target),
            "events": [_with_display_times(dict(row)) for row in events],
            "interview_reviews": [_interview_review_row_to_dict(row) for row in reviews],
        }
