import importlib.util
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import runpy
import sqlite3
import sys

import pytest


@pytest.fixture
def load_configured_app(tmp_path, monkeypatch):
    """Import isolated app instances without touching the local database."""
    source = Path(__file__).resolve().parents[1] / "app.py"
    default_database = source.with_name("devflow.db")
    sqlite_connect = sqlite3.connect
    connections = []

    def connect(database, *args, **kwargs):
        connections.append(Path(database))
        if Path(database) == default_database:
            database = tmp_path / "local-default-test.db"
        return sqlite_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)
    monkeypatch.delenv("FLASK_DEBUG", raising=False)

    def load(value=None):
        if value is None:
            monkeypatch.delenv("DEVFLOW_DATABASE", raising=False)
        else:
            monkeypatch.setenv("DEVFLOW_DATABASE", str(value))
        name = "devflow_configuration_test"
        spec = importlib.util.spec_from_file_location(name, source)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        return module

    return load, connections, sqlite_connect, default_database


@pytest.mark.parametrize("value", [None, ""])
def test_local_database_default(load_configured_app, tmp_path, monkeypatch, value):
    load, connections, _, default_database = load_configured_app
    monkeypatch.chdir(tmp_path)

    module = load(value)

    assert module.app.config["DATABASE"] == default_database
    assert connections == [default_database]
    assert module.app.debug is False
    assert callable(module.app.wsgi_app)


def test_environment_database_persists_across_imports(load_configured_app, tmp_path):
    load, connections, sqlite_connect, _ = load_configured_app
    database = tmp_path / "demo.db"
    module = load(database)
    client = module.app.test_client()
    created = client.post("/api/tasks", json={"name": "Demo task"})
    assert created.status_code == 201
    task_id = created.get_json()["task"]["id"]
    assert client.patch(f"/api/tasks/{task_id}", json={"status": "completed"}).status_code == 200
    assert client.delete("/api/tasks/2").status_code == 200

    reimported = load(database)

    assert reimported.app.config["DATABASE"] == database
    tasks = reimported.app.test_client().get("/api/tasks").get_json()["tasks"]
    assert {"id": task_id, "name": "Demo task", "status": "completed"} in tasks
    assert all(task["id"] != 2 for task in tasks)
    assert all(path == database for path in connections)
    with sqlite_connect(database) as connection:
        assert connection.execute("SELECT name, status FROM tasks WHERE id = ?", (task_id,)).fetchone() == (
            "Demo task", "completed",
        )


def test_relative_environment_path(load_configured_app, tmp_path, monkeypatch):
    load, _, _, _ = load_configured_app
    monkeypatch.chdir(tmp_path)

    module = load("relative-demo.db")

    assert module.app.config["DATABASE"] == Path("relative-demo.db")
    assert (tmp_path / "relative-demo.db").is_file()


def test_invalid_database_path_fails_without_fallback(load_configured_app, tmp_path):
    load, connections, _, default_database = load_configured_app
    invalid = tmp_path / "missing-parent" / "demo.db"

    with pytest.raises(sqlite3.OperationalError):
        load(invalid)

    assert connections == [invalid]
    assert default_database not in connections


def test_existing_empty_file_initializes_schema_once(load_configured_app, tmp_path):
    load, _, _, _ = load_configured_app
    database = tmp_path / "empty.db"
    database.touch()
    module = load(database)
    client = module.app.test_client()
    tasks = client.get("/api/tasks").get_json()["tasks"]
    assert len(tasks) == 3
    for task in tasks:
        assert client.delete(f"/api/tasks/{task['id']}").status_code == 200

    reimported = load(database)

    assert reimported.app.test_client().get("/api/tasks").get_json()["tasks"] == []


def test_concurrent_initialization_seeds_only_once(load_configured_app, tmp_path):
    load, _, sqlite_connect, _ = load_configured_app
    module = load(tmp_path / "initial.db")
    database = tmp_path / "concurrent.db"
    module.app.config["DATABASE"] = database

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda _: module.init_db(), range(4)))

    with sqlite_connect(database) as connection:
        assert connection.execute("SELECT id, name, status FROM tasks ORDER BY id").fetchall() == [
            (1, "Learn Python", "completed"),
            (2, "Understand APIs", "pending"),
            (3, "Learn SQL", "pending"),
        ]


def test_direct_startup_does_not_force_debug(load_configured_app, tmp_path, monkeypatch):
    load, _, _, _ = load_configured_app
    module = load(tmp_path / "startup.db")
    calls = []

    def capture_run(app, **kwargs):
        calls.append((app.debug, kwargs))

    monkeypatch.setattr(type(module.app), "run", capture_run)
    runpy.run_path(module.__file__, run_name="__main__")

    assert calls == [(False, {})]
