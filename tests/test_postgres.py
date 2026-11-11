"""Real PostgreSQL migration, transaction and concurrency integration tests."""

from concurrent.futures import ThreadPoolExecutor
import importlib
import shutil
import time
from pathlib import Path
import runpy

import psycopg
import pytest

from database import connect_database
from migrate import MIGRATIONS, MigrationError, migrate


def test_migration_creates_empty_schema_and_is_repeatable(postgres_url):
    assert migrate(postgres_url) == ["0001"]
    assert migrate(postgres_url) == []
    with connect_database(postgres_url) as connection:
        assert connection.execute("SELECT * FROM tasks").fetchall() == []
        assert connection.execute("SELECT version FROM schema_migrations").fetchall() == [{"version": "0001"}]
        row = connection.execute("INSERT INTO tasks (name) VALUES ('First') RETURNING *").fetchone()
        assert row == {"id": 1, "name": "First", "status": "pending"}
        connection.execute("DELETE FROM tasks")
    migrate(postgres_url)
    with connect_database(postgres_url) as connection:
        assert connection.execute("SELECT * FROM tasks").fetchall() == []


def test_unmigrated_application_fails_without_creating_schema(postgres_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", postgres_url)
    app = runpy.run_path(str(Path(__file__).resolve().parents[1] / "app.py"))["app"]
    response = app.test_client().get("/api/tasks")
    assert response.status_code == 500
    assert response.get_json() == {"error": "Database operation failed"}
    with connect_database(postgres_url) as connection:
        assert connection.execute("SELECT to_regclass('tasks') AS relation").fetchone()["relation"] is None


def test_migration_checksum_is_stable_across_platform_newlines(migrated_url, tmp_path):
    script = (MIGRATIONS / "0001_tasks.sql").read_text()
    (tmp_path / "0001_tasks.sql").write_bytes(script.replace("\n", "\r\n").encode())
    assert migrate(migrated_url, tmp_path) == []


def test_unversioned_table_is_not_adopted_or_overwritten(postgres_url):
    with connect_database(postgres_url) as connection:
        connection.execute("CREATE TABLE tasks (name TEXT)")
        connection.execute("INSERT INTO tasks VALUES ('Keep existing')")
    with pytest.raises(psycopg.errors.DuplicateTable):
        migrate(postgres_url)
    with connect_database(postgres_url) as connection:
        assert connection.execute("SELECT name FROM tasks").fetchall() == [{"name": "Keep existing"}]
        assert connection.execute("SELECT to_regclass('schema_migrations') AS relation").fetchone()["relation"] is None


def test_concurrent_migration_only_applies_once(postgres_url):
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(migrate, [postgres_url, postgres_url]))
    assert sorted(results) == [[], ["0001"]]


def test_failed_migration_rolls_back_schema_and_history(postgres_url, tmp_path):
    shutil.copyfile(MIGRATIONS / "0001_tasks.sql", tmp_path / "0001_tasks.sql")
    (tmp_path / "0002_broken.sql").write_text("CREATE TABLE should_rollback (id INTEGER); SELECT 1/0;")
    with pytest.raises(psycopg.errors.DivisionByZero):
        migrate(postgres_url, tmp_path)
    with connect_database(postgres_url) as connection:
        assert connection.execute("SELECT to_regclass('tasks') AS relation").fetchone()["relation"] is None
        assert connection.execute("SELECT to_regclass('schema_migrations') AS relation").fetchone()["relation"] is None


def test_changed_or_unknown_migration_is_refused(migrated_url, tmp_path):
    (tmp_path / "0001_tasks.sql").write_text("SELECT 1;")
    with pytest.raises(MigrationError, match="checksum"):
        migrate(migrated_url, tmp_path)
    with connect_database(migrated_url) as connection:
        connection.execute("UPDATE schema_migrations SET version = '9999'")
    with pytest.raises(MigrationError, match="history"):
        migrate(migrated_url)


@pytest.mark.parametrize("name,status", [("", "pending"), ("   ", "pending"), ("Valid", "invalid"), (None, "pending")])
def test_schema_rejects_invalid_records(migrated_url, name, status):
    with connect_database(migrated_url) as connection:
        with pytest.raises(psycopg.IntegrityError):
            with connection.transaction():
                connection.execute("INSERT INTO tasks (name, status) VALUES (%s, %s)", (name, status))
        assert connection.execute("SELECT * FROM tasks").fetchall() == []


@pytest.mark.parametrize("method,path,payload,statement", [
    ("POST", "/api/tasks", {"name": "Rolled back"}, "INSERT"),
    ("PATCH", "/api/tasks/2", {"name": "Rolled back"}, "UPDATE"),
    ("DELETE", "/api/tasks/2", None, "DELETE"),
])
def test_write_then_error_rolls_back(test_context, monkeypatch, method, path, payload, statement):
    client, url, _ = test_context
    app_module = importlib.import_module("app")
    with connect_database(url) as connection:
        before = connection.execute("SELECT * FROM tasks ORDER BY id").fetchall()

        class FailingConnection:
            transaction = connection.transaction

            def execute(self, query, parameters=None):
                result = connection.execute(query, parameters)
                if query.startswith(statement):
                    raise psycopg.OperationalError("simulated failure after write")
                return result

        monkeypatch.setattr(app_module, "get_db", lambda: FailingConnection())
        response = client.open(path, method=method, json=payload)
        assert response.status_code == 500
        assert response.get_json() == {"error": "Database operation failed"}
        assert connection.execute("SELECT * FROM tasks ORDER BY id").fetchall() == before


@pytest.mark.parametrize("operation", ["patch", "delete", "patch_delete"])
def test_concurrent_writes_lock_rows_without_lost_updates(test_context, monkeypatch, operation):
    _, url, _ = test_context
    app_module = importlib.import_module("app")
    from database import get_db as real_get_db
    from queue import Queue
    pids = Queue()

    def get_connection():
        connection = real_get_db()
        pids.put(connection.info.backend_pid)
        return connection

    monkeypatch.setattr(app_module, "get_db", get_connection)

    def perform(payload):
        with app_module.app.test_client() as client:
            if operation == "delete" or (operation == "patch_delete" and "status" in payload):
                return client.delete("/api/tasks/2").status_code
            return client.patch("/api/tasks/2", json=payload).status_code

    with connect_database(url) as blocker, ThreadPoolExecutor(max_workers=2) as executor:
        with blocker.transaction():
            blocker.execute("SELECT id FROM tasks WHERE id = 2 FOR UPDATE")
            futures = [executor.submit(perform, payload) for payload in
                       ({"name": "Concurrent name"}, {"status": "completed"})]
            waiting = [pids.get(timeout=10), pids.get(timeout=10)]
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                locked = [blocker.execute("SELECT cardinality(pg_blocking_pids(%s)) AS count", (pid,)).fetchone()["count"]
                          for pid in waiting]
                if all(locked):
                    break
                time.sleep(0.01)
            else:
                pytest.fail("Concurrent requests did not wait on row locks")
        results = [future.result(timeout=10) for future in futures]
        if operation == "delete":
            assert sorted(results) == [200, 404]
        elif operation == "patch_delete":
            assert results[0] in (200, 404)
            assert results[1] == 200
            assert blocker.execute("SELECT id FROM tasks WHERE id = 2").fetchone() is None
        else:
            assert results == [200, 200]
            assert blocker.execute("SELECT name, status FROM tasks WHERE id = 2").fetchone() == {
                "name": "Concurrent name", "status": "completed"}
