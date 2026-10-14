"""Local data export for backup and recovery."""

from datetime import date, datetime
from typing import Any

import sqlite3
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app import database
from app.auth import get_current_user_id
from app.database import connect_db

router = APIRouter(prefix="/api/data", tags=["Data"])

_BACKUP_FORMAT = "cs-jobmate-json-backup"
_BACKUP_VERSION = 1
_SYSTEM_TABLES = {"users", "auth_sessions", "schema_migrations", "legacy_imports", "legacy_import_claims"}


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


@router.get("/export")
def export_data() -> JSONResponse:
    """Export all local SQLite tables as a portable JSON backup."""
    with connect_db() as conn:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
            if row[0] not in _SYSTEM_TABLES
        ]
        data: dict[str, list[dict[str, Any]]] = {}
        for table in tables:
            rows = conn.execute(f'SELECT * FROM "{table}"').fetchall()
            data[table] = [
                {key: _json_value(row[key]) for key in row.keys()}
                for row in rows
            ]
    return JSONResponse(
        {"format": _BACKUP_FORMAT, "version": _BACKUP_VERSION, "tables": data},
        headers={"Content-Disposition": "attachment; filename=cs-jobmate-backup.json"},
    )


def _validate_backup(payload: dict) -> tuple[dict[str, list[dict]], dict[str, int]]:
    if payload.get("format") != _BACKUP_FORMAT or payload.get("version") != _BACKUP_VERSION:
        raise HTTPException(status_code=422, detail="备份格式或版本不受支持")
    tables = payload.get("tables")
    if not isinstance(tables, dict) or not tables:
        raise HTTPException(status_code=422, detail="备份缺少业务数据表")
    normalized: dict[str, list[dict]] = {}
    counts: dict[str, int] = {}
    for table, rows in tables.items():
        if not isinstance(table, str) or not table or table in _SYSTEM_TABLES or table.startswith("sqlite_"):
            raise HTTPException(status_code=422, detail="备份包含不允许恢复的系统表")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise HTTPException(status_code=422, detail=f"表 {table} 的数据格式无效")
        normalized[table] = rows
        counts[table] = len(rows)
    return normalized, counts


def _restore_preview(payload: dict) -> dict:
    tables, counts = _validate_backup(payload)
    with connect_db() as conn:
        existing = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ) if row[0] not in _SYSTEM_TABLES
        }
        unknown = set(tables) - existing
        if unknown:
            raise HTTPException(status_code=422, detail=f"目标数据库缺少数据表: {', '.join(sorted(unknown))}")
        missing = existing - set(tables)
        if missing:
            raise HTTPException(status_code=422, detail=f"备份缺少数据表: {', '.join(sorted(missing))}")
        target_counts = {
            table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in existing
        }
    status = "empty" if not any(target_counts.values()) else "occupied"
    return {"format": _BACKUP_FORMAT, "version": _BACKUP_VERSION, "status": status, "row_counts": counts, "target_counts": target_counts}


@router.post("/restore/preview")
def preview_restore(payload: dict) -> dict:
    return _restore_preview(payload)


@router.post("/restore")
def restore_data(payload: dict) -> dict:
    preview = _restore_preview(payload)
    if preview["status"] != "empty":
        raise HTTPException(status_code=409, detail="目标数据库已有业务数据，拒绝覆盖")
    tables, counts = _validate_backup(payload)
    owner_id = get_current_user_id()
    with connect_db() as conn:
        if owner_id is None:
            owner_row = conn.execute("SELECT id FROM users WHERE email = 'local-owner@localhost'").fetchone()
            owner_id = owner_row[0] if owner_row else None
        try:
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("BEGIN")
            for table, rows in tables.items():
                target_columns = [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]
                for row in rows:
                    columns = list(row)
                    if not set(columns).issubset(target_columns):
                        raise HTTPException(status_code=422, detail=f"表 {table} 包含未知字段")
                    values = [row[column] for column in columns]
                    if table in database.USER_OWNED_TABLES and "user_id" in target_columns and owner_id is not None:
                        if "user_id" not in columns:
                            columns.append("user_id")
                            values.append(owner_id)
                        else:
                            values[columns.index("user_id")] = owner_id
                    quoted = ", ".join(f'"{column}"' for column in columns)
                    placeholders = ", ".join("?" for _ in columns)
                    conn.execute(f'INSERT INTO "{table}" ({quoted}) VALUES ({placeholders})', values)
                actual = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                if actual != counts[table]:
                    raise HTTPException(status_code=422, detail=f"表 {table} 恢复行数校验失败")
            conn.execute("PRAGMA foreign_keys = ON")
            if conn.execute("PRAGMA foreign_key_check").fetchone():
                raise HTTPException(status_code=422, detail="恢复后的关联关系校验失败")
            conn.commit()
        except HTTPException:
            conn.rollback()
            raise
        except (sqlite3.Error, ValueError) as exc:
            conn.rollback()
            raise HTTPException(status_code=422, detail="备份恢复失败，未写入数据") from exc
    return {"status": "restored", "row_counts": counts}
