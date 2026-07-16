import sqlite3

from app import database
from app.database import format_datetime_for_display


def test_format_datetime_for_display_converts_sqlite_utc_to_beijing_time():
    # SQLite CURRENT_TIMESTAMP 保存 UTC；北京时间应在同一时刻加 8 小时。
    assert (
        format_datetime_for_display("2026-07-12 17:22:08")
        == "2026-07-13 01:22:08"
    )


def test_format_datetime_for_display_keeps_empty_value_empty():
    assert format_datetime_for_display(None) is None


def test_init_db_migrates_legacy_copilot_tables(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy.db"
    monkeypatch.setattr(database, "DB_PATH", db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE copilot_sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, resume_version_id INTEGER, target_role TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'active', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.execute(
            "CREATE TABLE analysis_turns (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER NOT NULL, input_message_id INTEGER, status TEXT NOT NULL DEFAULT 'pending', stage TEXT NOT NULL DEFAULT 'queued', progress INTEGER NOT NULL DEFAULT 0, error_message TEXT NOT NULL DEFAULT '', report_id INTEGER, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.commit()

    database.init_db()

    with sqlite3.connect(db_path) as conn:
        session_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(copilot_sessions)")
        }
        turn_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(analysis_turns)")
        }
    assert "active_report_id" in session_columns
    assert {"parent_turn_id", "input_type"}.issubset(turn_columns)
