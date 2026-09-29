"""Local data export for backup and recovery."""

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.database import connect_db

router = APIRouter(prefix="/api/data", tags=["Data"])


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
        ]
        data: dict[str, list[dict[str, Any]]] = {}
        for table in tables:
            rows = conn.execute(f'SELECT * FROM "{table}"').fetchall()
            data[table] = [
                {key: _json_value(row[key]) for key in row.keys()}
                for row in rows
            ]
    return JSONResponse(
        {"format": "cs-jobmate-json-backup", "version": 1, "tables": data},
        headers={"Content-Disposition": "attachment; filename=cs-jobmate-backup.json"},
    )
