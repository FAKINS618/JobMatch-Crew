from fastapi.testclient import TestClient
from pathlib import Path
from uuid import uuid4
import pytest

from app.main import app
from app import database
from app.auth import current_user_id, get_current_user_id
from app.cache import build_cache_key
from app.config import settings
from app.services import copilot_context_service
from app import task_worker
from app.task_queue import dispatch_task
from app.legacy_migration import LegacyMigrationError, migrate_legacy_data


# 测基础 API：不调用大模型、不联网，只验证 FastAPI 应用能正常加载。
client = TestClient(app)


def test_health_api():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_roles_api():
    response = client.get("/api/roles")

    assert response.status_code == 200
    roles = response.json()["roles"]
    assert "AI 应用开发实习" in roles
    assert "Java 后端开发实习" in roles
    assert "前端开发实习" in roles


def test_reports_api():
    response = client.get("/api/reports")

    assert response.status_code == 200
    assert "reports" in response.json()
    if response.json()["reports"]:
        assert "job_post_count" in response.json()["reports"][0]
        assert "created_at_local" in response.json()["reports"][0]


def test_dashboard_api_initializes_workflow_tables():
    """应用启动后，闭环总览接口应可在空数据状态下正常返回。"""
    with TestClient(app) as lifespan_client:
        response = lifespan_client.get("/api/dashboard/summary")

    assert response.status_code == 200
    assert response.json()["saved_job_count"] >= 0


def test_copilot_session_api():
    with TestClient(app) as lifespan_client:
        response = lifespan_client.post(
            "/api/v1/copilot/sessions", json={"target_role": "Python 后端开发实习"}
        )

    assert response.status_code == 201
    assert response.json()["target_role"] == "Python 后端开发实习"


def test_data_export_api_returns_portable_backup():
    with TestClient(app) as lifespan_client:
        response = lifespan_client.get("/api/data/export")

    assert response.status_code == 200
    assert response.json()["format"] == "cs-jobmate-json-backup"
    assert "reports" in response.json()["tables"]


def test_analysis_task_retry_persists_payload_and_limits_attempts(monkeypatch):
    db_path = Path("tmp") / f"task-{uuid4().hex}.db"
    monkeypatch.setattr(database.settings, "database_path", db_path)
    monkeypatch.setattr(database, "DB_PATH", db_path)
    database.init_db()
    task_id = database.create_analysis_task("market_match", {"request": {"target_role": "Python"}})
    database.update_analysis_task(task_id, status="failed", progress=100)
    assert database.retry_analysis_task(task_id)["attempt_count"] == 1
    assert database.get_analysis_task_payload(task_id)["request"]["target_role"] == "Python"
    for _ in range(2):
        database.update_analysis_task(task_id, status="failed", progress=100)
        database.retry_analysis_task(task_id)
    database.update_analysis_task(task_id, status="failed", progress=100)
    with pytest.raises(ValueError, match="最大重试"):
        database.retry_analysis_task(task_id)


def test_auth_register_login_and_protected_api(monkeypatch):
    db_path = Path("tmp") / f"auth-{uuid4().hex}.db"
    monkeypatch.setattr(database.settings, "database_path", db_path)
    monkeypatch.setattr(database, "DB_PATH", db_path)
    monkeypatch.setattr(database.settings, "auth_enabled", True)
    monkeypatch.setattr(database.settings, "auth_secret", "test-auth-secret-with-at-least-32-characters")
    with TestClient(app) as lifespan_client:
        denied = lifespan_client.get("/api/reports")
        assert denied.status_code == 401
        registered = lifespan_client.post(
            "/api/auth/register",
            json={"email": f"user-{uuid4().hex}@example.com", "password": "long-password-123"},
        )
        assert registered.status_code == 201
        token = registered.json()["access_token"]
        allowed = lifespan_client.get("/api/reports", headers={"Authorization": f"Bearer {token}"})
        assert allowed.status_code == 200
        me = lifespan_client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        assert me.json()["email"] == registered.json()["user"]["email"]
        assert lifespan_client.get("/api/auth/me").status_code == 401


def test_auth_rejects_default_signing_secret(monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "auth_secret", "change-me-in-production")
    with pytest.raises(RuntimeError, match="AUTH_SECRET"):
        with TestClient(app):
            pass


def test_authenticated_users_have_separate_business_databases(monkeypatch):
    db_path = Path("tmp") / f"isolation-{uuid4().hex}.db"
    monkeypatch.setattr(database, "DB_PATH", db_path)
    monkeypatch.setattr(database.settings, "auth_enabled", True)
    monkeypatch.setattr(database.settings, "auth_secret", "test-auth-secret-with-at-least-32-characters")
    with TestClient(app) as client:
        users = []
        for _ in range(2):
            response = client.post("/api/auth/register", json={
                "email": f"member-{uuid4().hex}@example.com", "password": "long-password-123",
            })
            assert response.status_code == 201
            users.append(response.json())
        first = {"Authorization": f"Bearer {users[0]['access_token']}"}
        second = {"Authorization": f"Bearer {users[1]['access_token']}"}
        created = client.post("/api/resumes/versions", headers=first, json={
            "version_name": "Backend resume", "target_role": "Backend",
            "raw_text": "Python FastAPI database internship project experience. " * 3,
            "profile": {"skills": ["Python"]},
        })
        assert created.status_code == 200
        assert len(client.get("/api/resumes/versions", headers=first).json()) == 1
        assert client.get("/api/resumes/versions", headers=second).json() == []
        assert client.get("/api/resumes/versions", headers=first).json()[0]["id"] == created.json()["id"]
        token = current_user_id.set(users[0]["user"]["id"])
        try:
            report_id = database.save_report(
                "Backend", 80, "resume content", "job description", "report content",
            )
            task_id = database.create_analysis_task("market_match", {"request": {"target_role": "Backend"}})
            with database.connect_db() as conn:
                owner = conn.execute("SELECT user_id FROM reports WHERE id = ?", (report_id,)).fetchone()[0]
            assert owner == users[0]["user"]["id"]
        finally:
            current_user_id.reset(token)
        assert client.get(f"/api/reports/{report_id}", headers=first).status_code == 200
        assert client.get(f"/api/reports/{report_id}", headers=second).status_code == 404
        assert client.get(f"/api/tasks/{task_id}", headers=first).status_code == 200
        assert client.get(f"/api/tasks/{task_id}", headers=second).status_code == 404
        with pytest.raises(RuntimeError, match="user context"):
            database.connect_db()
        export = client.get("/api/data/export", headers=second)
        assert export.status_code == 200
        assert export.json()["tables"]["reports"] == []
        assert "users" not in export.json()["tables"]


def test_copilot_cache_keeps_same_report_id_separate_by_user(monkeypatch):
    class FakeCache:
        def __init__(self):
            self.values = {}

        def get_json(self, key):
            return self.values.get(key)

        def set_json(self, key, value, _ttl):
            self.values[key] = value

        def delete(self, key):
            self.values.pop(key, None)

    fake = FakeCache()
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "cache_enabled", True)
    monkeypatch.setattr(copilot_context_service, "get_cache", lambda: fake)
    monkeypatch.setattr(copilot_context_service, "get_copilot_report_source_turn", lambda _id: None)
    monkeypatch.setattr(copilot_context_service, "list_recent_copilot_messages", lambda *_args, **_kwargs: [])
    contexts = []
    for user_id, summary in ((11, "first private summary"), (12, "second private summary")):
        token = current_user_id.set(user_id)
        try:
            contexts.append(copilot_context_service.get_copilot_context(
                {"id": 1, "parsed_result": f'{{"summary": "{summary}"}}'}, 1,
            ))
        finally:
            current_user_id.reset(token)
    assert contexts[0]["snapshot"]["analysis"]["summary"] == "first private summary"
    assert contexts[1]["snapshot"]["analysis"]["summary"] == "second private summary"
    assert len(fake.values) == 4
    with pytest.raises(RuntimeError, match="user context"):
        build_cache_key("copilot:context_pointer", {"report_id": 1})


def test_worker_restores_user_context_after_task(monkeypatch):
    seen = []
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(task_worker, "run_copilot_turn", lambda _id: seen.append(get_current_user_id()))

    task_worker.handle_task({"user_id": 7, "task_type": "copilot_turn", "payload": {"turn_id": 1}})

    assert seen == [7]
    assert get_current_user_id() is None
    with pytest.raises(ValueError, match="用户身份"):
        task_worker.handle_task({"task_type": "copilot_turn", "payload": {"turn_id": 1}})


def test_local_background_task_keeps_request_owner(monkeypatch):
    class PendingTasks:
        def add_task(self, callback, *args):
            self.callback = callback
            self.args = args

    pending = PendingTasks()
    seen = []
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "task_queue_enabled", False)
    token = current_user_id.set(8)
    try:
        dispatch_task(
            pending, task_type="copilot_turn", payload={"turn_id": 1},
            local_runner=lambda _id: seen.append(get_current_user_id()), local_args=(1,),
        )
    finally:
        current_user_id.reset(token)
    pending.callback(*pending.args)
    assert seen == [8]
    assert get_current_user_id() is None


def test_worker_failure_update_uses_task_owner(monkeypatch):
    class StopWorker(Exception):
        pass

    class FakeQueue:
        def __init__(self):
            self.client = "fake"
            self.acknowledged = []
            self.reads = 0

        def ensure_group(self):
            pass

        def reclaim_pending(self, _consumer):
            return []

        def consume(self, _consumer):
            self.reads += 1
            if self.reads > 1:
                raise StopWorker
            return "message-1", {"user_id": 9, "task_type": "unknown", "payload": {"task_id": 3}}

        def acknowledge(self, message_id):
            self.acknowledged.append(message_id)

    queue = FakeQueue()
    updates = []
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(task_worker, "RedisTaskQueue", lambda: queue)
    monkeypatch.setattr(task_worker, "update_analysis_task", lambda task_id, **kwargs: updates.append((get_current_user_id(), task_id, kwargs)))

    with pytest.raises(StopWorker):
        task_worker.main()

    assert updates == [(9, 3, {"status": "failed", "progress": 100, "error_message": "任务消息处理失败，请重试。"})]
    assert queue.acknowledged == ["message-1"]
    assert get_current_user_id() is None


def _legacy_database_with_accounts(path: Path) -> None:
    database.init_db(path)
    with database._open_db(path) as conn:
        owner_id = conn.execute(
            "SELECT id FROM users WHERE email = 'local-owner@localhost'"
        ).fetchone()[0]
        conn.execute("INSERT INTO users(email, password_hash) VALUES ('first@example.com', 'hash')")
        conn.execute("INSERT INTO users(email, password_hash) VALUES ('second@example.com', 'hash')")
        conn.execute(
            "INSERT INTO reports(id, target_role, resume_text, jd_text, markdown_report, user_id) "
            "VALUES (41, 'Backend', 'private resume', 'private JD', 'private report', ?)",
            (owner_id,),
        )
        conn.execute(
            "INSERT INTO job_posts(id, report_id, title, user_id) VALUES (51, 41, 'Developer', ?)",
            (owner_id,),
        )
        conn.execute("INSERT INTO resumes(id, display_name, user_id) VALUES (61, 'My resume', ?)", (owner_id,))
        conn.execute(
            "INSERT INTO resume_versions(id, resume_id, version_name, raw_text, profile_json, user_id) "
            "VALUES (71, 61, 'Original', 'private resume', '{}', ?)",
            (owner_id,),
        )
        conn.execute(
            "INSERT INTO copilot_sessions(id, resume_version_id, target_role, user_id) "
            "VALUES (81, 71, 'Backend', ?)",
            (owner_id,),
        )
        conn.execute("INSERT INTO analysis_turns(id, session_id) VALUES (91, 81)")
        conn.execute("INSERT INTO copilot_messages(session_id, turn_id, role, content) VALUES (81, 91, 'user', 'private question')")


def test_legacy_migration_previews_and_preserves_relationships(monkeypatch):
    source = Path("tmp") / f"legacy-{uuid4().hex}.db"
    _legacy_database_with_accounts(source)
    preview = migrate_legacy_data(source, "first@example.com")
    target = Path(preview["target"])
    assert preview["status"] == "empty"
    assert preview["row_counts"]["reports"] == 1
    assert not target.exists()

    imported = migrate_legacy_data(source, "first@example.com", apply=True)
    assert imported["status"] == "imported"
    with database._open_db(target) as conn:
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert tuple(conn.execute("SELECT id, user_id FROM reports").fetchone()) == (41, imported["user_id"])
        assert conn.execute("SELECT report_id FROM job_posts").fetchone()[0] == 41
        assert conn.execute("SELECT content FROM copilot_messages").fetchone()[0] == "private question"
        assert conn.execute("SELECT email FROM users WHERE email = 'first@example.com'").fetchone() is None
    with database._open_db(source) as conn:
        assert conn.execute("SELECT markdown_report FROM reports WHERE id = 41").fetchone()[0] == "private report"
        other_user_id = conn.execute("SELECT id FROM users WHERE email = 'second@example.com'").fetchone()[0]
    monkeypatch.setattr(database, "DB_PATH", source.resolve())
    monkeypatch.setattr(settings, "auth_enabled", True)
    token = current_user_id.set(imported["user_id"])
    try:
        assert database.get_report(41)["markdown_report"] == "private report"
    finally:
        current_user_id.reset(token)
    token = current_user_id.set(other_user_id)
    try:
        assert database.get_report(41) is None
    finally:
        current_user_id.reset(token)
    assert migrate_legacy_data(source, "first@example.com", apply=True)["status"] == "already_imported"
    with pytest.raises(LegacyMigrationError, match="其他账号"):
        migrate_legacy_data(source, "second@example.com", apply=True)


def test_legacy_migration_refuses_occupied_account_database():
    source = Path("tmp") / f"legacy-occupied-{uuid4().hex}.db"
    _legacy_database_with_accounts(source)
    preview = migrate_legacy_data(source, "first@example.com")
    target = Path(preview["target"])
    database.init_db(target)
    with database._open_db(target) as conn:
        conn.execute("INSERT INTO reports(target_role, resume_text, jd_text, markdown_report) VALUES ('Other', 'r', 'j', 'existing')")
    with pytest.raises(LegacyMigrationError, match="已有业务数据"):
        migrate_legacy_data(source, "first@example.com", apply=True)
    with database._open_db(source) as conn:
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'legacy_import_claims'"
        ).fetchone() is None
