"""Explicitly assign the old single-user SQLite data to one account."""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from app import database
from app.auth import current_user_id, normalize_email

_SYSTEM_TABLES = {"users", "schema_migrations", "legacy_imports", "legacy_import_claims"}


class LegacyMigrationError(ValueError):
    pass


def _quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _read_only(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise LegacyMigrationError(f"数据库不存在: {path}")
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _business_tables(conn: sqlite3.Connection) -> list[str]:
    return [
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ) if row[0] not in _SYSTEM_TABLES
    ]


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({_quoted(table)})")]


def _account_and_counts(conn: sqlite3.Connection, email: str) -> tuple[int, dict[str, int]]:
    try:
        account = conn.execute("SELECT id FROM users WHERE email = ?", (normalize_email(email),)).fetchone()
        owner = conn.execute("SELECT id FROM users WHERE email = 'local-owner@localhost'").fetchone()
    except sqlite3.OperationalError as exc:
        raise LegacyMigrationError("源库缺少账号结构，请先用当前版本启动后端完成初始化") from exc
    if account is None or owner is None or account[0] == owner[0]:
        raise LegacyMigrationError("目标账号不存在，或不能使用本地占位账号")
    claim_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'legacy_import_claims'"
    ).fetchone()
    if claim_table:
        claim = conn.execute("SELECT user_id FROM legacy_import_claims LIMIT 1").fetchone()
        if claim and claim[0] != account[0]:
            raise LegacyMigrationError("历史数据已归属其他账号，不能重复迁移")
    tables = _business_tables(conn)
    missing = set(database.USER_OWNED_TABLES) - set(tables)
    if missing:
        raise LegacyMigrationError(f"源库缺少业务表: {', '.join(sorted(missing))}")
    counts = {}
    for table in tables:
        if table in database.USER_OWNED_TABLES:
            if "user_id" not in _columns(conn, table):
                raise LegacyMigrationError("源库尚未完成用户归属字段初始化")
            foreign = conn.execute(
                f"SELECT 1 FROM {_quoted(table)} WHERE user_id IS NULL OR user_id != ? LIMIT 1",
                (owner[0],),
            ).fetchone()
            if foreign:
                raise LegacyMigrationError(f"源库 {table} 含有其他账号的数据，不能整体迁移")
        counts[table] = conn.execute(f"SELECT COUNT(*) FROM {_quoted(table)}").fetchone()[0]
    return account[0], counts


def _target_status(conn: sqlite3.Connection, tables: list[str], source_path: Path) -> str:
    existing = set(_business_tables(conn))
    missing = set(tables) - existing
    if missing:
        raise LegacyMigrationError(f"目标库缺少业务表: {', '.join(sorted(missing))}")
    marker = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'legacy_imports'"
    ).fetchone()
    if marker and conn.execute(
        "SELECT 1 FROM legacy_imports WHERE source_path = ?", (str(source_path),)
    ).fetchone():
        return "already_imported"
    for table in existing:
        if conn.execute(f"SELECT 1 FROM {_quoted(table)} LIMIT 1").fetchone():
            return "occupied"
    if marker and conn.execute("SELECT 1 FROM legacy_imports LIMIT 1").fetchone():
        return "occupied"
    return "empty"


def migrate_legacy_data(source_path: Path, email: str, *, apply: bool = False) -> dict:
    """Preview or atomically copy legacy business rows into one empty user DB."""
    source_path = source_path.resolve()
    with closing(_read_only(source_path)) as source:
        source.execute("BEGIN")
        user_id, counts = _account_and_counts(source, email)
        target_path = source_path.with_name(f"{source_path.stem}.user-{user_id}{source_path.suffix}")
        tables = list(counts)
        if target_path.exists():
            with closing(_read_only(target_path)) as target:
                status = _target_status(target, tables, source_path)
        else:
            status = "empty"
        result = {
            "source": str(source_path), "target": str(target_path),
            "user_id": user_id, "row_counts": counts, "status": status,
        }
        if not apply or status == "already_imported":
            return result
        if status != "empty":
            raise LegacyMigrationError("目标账号已有业务数据；为避免 ID 冲突，迁移只接受空目标库")

        # Record the exclusive owner before copying. A failed copy can be retried
        # for the same account without exposing the legacy data to another one.
        with closing(database._open_db(source_path)) as writable_source:
            writable_source.execute("BEGIN IMMEDIATE")
            writable_source.execute(
                "CREATE TABLE IF NOT EXISTS legacy_import_claims ("
                "user_id INTEGER PRIMARY KEY, claimed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            )
            claim = writable_source.execute("SELECT user_id FROM legacy_import_claims LIMIT 1").fetchone()
            if claim and claim[0] != user_id:
                raise LegacyMigrationError("历史数据已归属其他账号，不能重复迁移")
            if not claim:
                writable_source.execute("INSERT INTO legacy_import_claims(user_id) VALUES (?)", (user_id,))
            writable_source.commit()

        token = current_user_id.set(user_id)
        try:
            database.init_db(target_path)
        finally:
            current_user_id.reset(token)

        with closing(database._open_db(target_path)) as target:
            target.execute("PRAGMA foreign_keys = OFF")
            target.execute("BEGIN IMMEDIATE")
            try:
                if _target_status(target, tables, source_path) != "empty":
                    raise LegacyMigrationError("目标库在迁移前发生变化，请重新预览")
                for table in tables:
                    source_columns = _columns(source, table)
                    target_columns = _columns(target, table)
                    if not set(source_columns).issubset(target_columns):
                        raise LegacyMigrationError(f"目标库 {table} 的结构与源库不兼容")
                    columns = [column for column in target_columns if column in source_columns]
                    select = source.execute(
                        f"SELECT {', '.join(map(_quoted, columns))} FROM {_quoted(table)}"
                    )
                    insert = (
                        f"INSERT INTO {_quoted(table)} ({', '.join(map(_quoted, columns))}) "
                        f"VALUES ({', '.join('?' for _ in columns)})"
                    )
                    while batch := select.fetchmany(500):
                        records = [list(row) for row in batch]
                        if table in database.USER_OWNED_TABLES:
                            owner_index = columns.index("user_id")
                            for record in records:
                                record[owner_index] = user_id
                        target.executemany(insert, records)
                    copied = target.execute(f"SELECT COUNT(*) FROM {_quoted(table)}").fetchone()[0]
                    if copied != counts[table]:
                        raise LegacyMigrationError(f"{table} 的迁移行数不一致，未写入目标库")
                if target.execute("PRAGMA foreign_key_check").fetchone():
                    raise LegacyMigrationError("迁移后的关联关系校验失败，未写入目标库")
                target.execute(
                    "CREATE TABLE IF NOT EXISTS legacy_imports ("
                    "source_path TEXT PRIMARY KEY, user_id INTEGER NOT NULL, "
                    "imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
                )
                target.execute(
                    "INSERT INTO legacy_imports(source_path, user_id) VALUES (?, ?)",
                    (str(source_path), user_id),
                )
                target.commit()
            except Exception:
                target.rollback()
                raise
        result["status"] = "imported"
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview or import legacy single-user data")
    parser.add_argument("--email", required=True, help="Existing account that will own the data")
    parser.add_argument("--database", type=Path, default=database.DB_PATH)
    parser.add_argument("--apply", action="store_true", help="Write to the account database")
    args = parser.parse_args()
    try:
        result = migrate_legacy_data(args.database, args.email, apply=args.apply)
    except (LegacyMigrationError, sqlite3.Error) as exc:
        parser.exit(2, f"迁移失败: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
