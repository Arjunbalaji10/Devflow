import importlib
import psycopg
from migrate import migrate
from html.parser import HTMLParser

import pytest
from io import BytesIO
import json


def test_get_tasks(test_context):
    client, _, _ = test_context
    response = client.get("/api/tasks")

    assert response.status_code == 200
    assert response.is_json

    data = response.get_json()
    assert data["success"] is True
    assert isinstance(data["tasks"], list)


def test_create_task(test_context):
    client, test_database, postgres_connect = test_context
    submitted_name = "Write POST test"

    response = client.post(
        "/api/tasks",
        json={"name": submitted_name},
    )

    assert response.status_code == 201
    assert response.is_json

    data = response.get_json()
    assert data["success"] is True

    task = data["task"]
    assert isinstance(task["id"], int)
    assert task["id"] > 0
    assert task["name"] == submitted_name
    assert task["status"] == "pending"

    with postgres_connect(test_database) as connection:
        saved_task = connection.execute(
            "SELECT id, name, status FROM tasks WHERE id = %s",
            (task["id"],),
        ).fetchone()

    assert saved_task == (
        task["id"],
        submitted_name,
        "pending",
    )


def read_tasks(test_context):
    _, test_database, postgres_connect = test_context
    with postgres_connect(test_database) as connection:
        return connection.execute(
            "SELECT id, name, status FROM tasks ORDER BY id"
        ).fetchall()


@pytest.mark.parametrize(
    "changes, expected_name, expected_status",
    [
        ({"name": "  Renamed task  "}, "Renamed task", "pending"),
        ({"status": "completed"}, "Understand APIs", "completed"),
        ({"name": "Updated task", "status": "completed"}, "Updated task", "completed"),
        ({}, "Understand APIs", "pending"),
    ],
)
def test_patch_task(test_context, changes, expected_name, expected_status):
    client, _, _ = test_context
    before = read_tasks(test_context)
    expected_task = {"id": 2, "name": expected_name, "status": expected_status}

    response = client.patch("/api/tasks/2", json=changes)

    assert response.status_code == 200
    assert response.is_json
    assert response.get_json() == {"success": True, "task": expected_task}
    expected_rows = [
        (task_id, expected_name, expected_status) if task_id == 2 else (task_id, name, status)
        for task_id, name, status in before
    ]
    assert read_tasks(test_context) == expected_rows
    assert client.get("/api/tasks").get_json()["tasks"] == [
        {"id": task_id, "name": name, "status": status}
        for task_id, name, status in expected_rows
    ]


def test_delete_task(test_context):
    client, _, _ = test_context
    before = read_tasks(test_context)

    response = client.delete("/api/tasks/2")

    assert response.status_code == 200
    assert response.is_json
    assert response.get_json() == {
        "success": True,
        "deleted_task": {"id": 2, "name": "Understand APIs", "status": "pending"},
    }
    expected_rows = [row for row in before if row[0] != 2]
    assert read_tasks(test_context) == expected_rows
    assert client.get("/api/tasks").get_json()["tasks"] == [
        {"id": task_id, "name": name, "status": status}
        for task_id, name, status in expected_rows
    ]
    assert client.delete("/api/tasks/2").status_code == 404
    assert read_tasks(test_context) == expected_rows


@pytest.mark.parametrize("method", ["PATCH", "DELETE"])
@pytest.mark.parametrize("task_id", ["abc", "1.5", "-1"])
def test_invalid_task_ids(test_context, method, task_id):
    client, _, _ = test_context
    before = read_tasks(test_context)

    response = client.open(
        f"/api/tasks/{task_id}", method=method, json={"status": "completed"}
    )

    assert response.status_code == 404
    assert read_tasks(test_context) == before


@pytest.mark.parametrize("method", ["PATCH", "DELETE"])
@pytest.mark.parametrize("task_id", [0, 999999])
def test_nonexistent_task_ids(test_context, method, task_id):
    client, _, _ = test_context
    before = read_tasks(test_context)

    response = client.open(
        f"/api/tasks/{task_id}", method=method, json={"status": "completed"}
    )

    assert response.status_code == 404
    assert response.is_json
    assert response.get_json() == {"error": "Task not found"}
    assert read_tasks(test_context) == before


@pytest.mark.parametrize("payload", [{}, {"status": "pending"}, {"name": None}, {"name": ""}, {"name": "   "}])
def test_create_task_missing_name(test_context, payload):
    client, _, _ = test_context
    before = read_tasks(test_context)

    response = client.post("/api/tasks", json=payload)

    assert response.status_code == 400
    assert response.get_json() == {"error": "Task name is required"}
    assert read_tasks(test_context) == before


@pytest.mark.parametrize(
    "payload, error",
    [
        ({"name": None}, "Invalid task name"),
        ({"name": ""}, "Invalid task name"),
        ({"name": "   "}, "Invalid task name"),
        ({"name": 123}, "Invalid task name"),
        ({"status": None}, "Invalid status"),
        ({"status": "unknown"}, "Invalid status"),
        ({"name": "Must not persist", "status": "unknown"}, "Invalid status"),
    ],
)
def test_patch_task_invalid_fields(test_context, payload, error):
    client, _, _ = test_context
    before = read_tasks(test_context)

    response = client.patch("/api/tasks/2", json=payload)

    assert response.status_code == 400
    assert response.get_json() == {"error": error}
    assert read_tasks(test_context) == before


@pytest.mark.parametrize("method, path", [("POST", "/api/tasks"), ("PATCH", "/api/tasks/2")])
@pytest.mark.parametrize("body", [None, "null", "[]", '"text"', "{"])
def test_task_requires_json_object(test_context, method, path, body):
    client, _, _ = test_context
    before = read_tasks(test_context)

    response = client.open(path, method=method, data=body, content_type="application/json")

    assert response.status_code == 400
    assert response.get_json() == {"error": "A JSON object is required"}
    assert read_tasks(test_context) == before


def test_dashboard_shell(test_context):
    client, _, _ = test_context
    before = read_tasks(test_context)
    elements = []

    class DashboardParser(HTMLParser):
        def handle_starttag(self, tag, attrs):
            elements.append((tag, dict(attrs)))

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert response.mimetype == "text/html"
    DashboardParser().feed(response.get_data(as_text=True))
    assert ("html", {"lang": "en"}) in elements
    assert any(tag == "meta" and attrs.get("name") == "viewport" for tag, attrs in elements)
    assert any(tag == "main" and attrs.get("id") == "main-content" for tag, attrs in elements)
    assert any(tag == "a" and attrs.get("href") == "#main-content" for tag, attrs in elements)
    labels = {attrs.get("for") for tag, attrs in elements if tag == "label"}
    inputs = [attrs for tag, attrs in elements if tag == "input"]
    assert inputs
    assert all(attrs.get("id") in labels for attrs in inputs)
    assert all("disabled" in attrs for tag, attrs in elements if tag == "fieldset")
    assert any(attrs.get("role") == "status" and attrs.get("aria-live") == "polite" for _, attrs in elements)
    stylesheets = [
        attrs["href"] for tag, attrs in elements
        if tag == "link" and attrs.get("rel") == "stylesheet"
    ]
    assert stylesheets
    for stylesheet in stylesheets:
        assert stylesheet.startswith("/static/")
        asset = client.get(stylesheet)
        assert asset.status_code == 200
        assert asset.mimetype == "text/css"
    assert read_tasks(test_context) == before


def test_home_preserved_with_dashboard(test_context):
    client, _, _ = test_context

    response = client.get("/")

    assert response.status_code == 200
    assert response.get_data(as_text=True) == "Welcome to DevFlow API!"


def test_dashboard_loads_frontend_modules(test_context):
    client, _, _ = test_context
    response = client.get("/dashboard")
    html = response.get_data(as_text=True)

    assert '<script type="module" src="/static/js/app.js"></script>' in html
    assert 'id="retry-tasks" type="button" hidden' in html
    for path in ("/static/js/app.js", "/static/js/api.js", "/static/js/editing.js", "/static/js/completion.js", "/static/js/deletion.js"):
        asset = client.get(path)
        assert asset.status_code == 200
        assert "javascript" in asset.mimetype


def test_task_api_preserves_names_as_json_data(test_context):
    client, test_database, postgres_connect = test_context
    name = '<img src=x onerror="alert(1)">'
    with postgres_connect(test_database) as connection:
        connection.execute("INSERT INTO tasks (name) VALUES (%s)", (name,))

    response = client.get("/api/tasks")

    assert response.status_code == 200
    assert response.get_json()["tasks"][-1]["name"] == name
    assert name not in client.get("/dashboard").get_data(as_text=True)


def test_create_task_trims_and_persists_across_requests(test_context):
    client, _, _ = test_context
    before = read_tasks(test_context)

    response = client.post("/api/tasks", json={"name": "  Build dashboard  "})

    assert response.status_code == 201
    task = response.get_json()["task"]
    assert task["name"] == "Build dashboard"
    assert task["status"] == "pending"
    assert read_tasks(test_context) == before + [(task["id"], "Build dashboard", "pending")]
    assert task in client.get("/api/tasks").get_json()["tasks"]


def test_dashboard_creation_feedback_is_accessible(test_context):
    client, _, _ = test_context
    html = client.get("/dashboard").get_data(as_text=True)

    assert 'id="task-fields" disabled' in html
    assert 'aria-describedby="create-feedback"' in html
    assert 'id="create-feedback" role="status" aria-live="polite" aria-atomic="true"' in html


@pytest.mark.parametrize("task_id, status", [(1, "completed"), (2, "pending")])
def test_rename_preserves_status_and_persists(test_context, task_id, status):
    client, _, _ = test_context
    before = read_tasks(test_context)

    response = client.patch(f"/api/tasks/{task_id}", json={"name": "  Renamed task  "})

    assert response.status_code == 200
    expected = {"id": task_id, "name": "Renamed task", "status": status}
    assert response.get_json() == {"success": True, "task": expected}
    assert read_tasks(test_context) == [
        (row_id, "Renamed task", row_status) if row_id == task_id else (row_id, name, row_status)
        for row_id, name, row_status in before
    ]
    assert expected in client.get("/api/tasks").get_json()["tasks"]


@pytest.mark.parametrize("task_id, new_status", [(1, "pending"), (2, "completed")])
def test_completion_preserves_name_and_persists(test_context, task_id, new_status):
    client, _, _ = test_context
    before = read_tasks(test_context)
    original_name = next(name for row_id, name, _ in before if row_id == task_id)

    response = client.patch(f"/api/tasks/{task_id}", json={"status": new_status})

    assert response.status_code == 200
    expected = {"id": task_id, "name": original_name, "status": new_status}
    assert response.get_json() == {"success": True, "task": expected}
    assert read_tasks(test_context) == [
        (row_id, name, new_status) if row_id == task_id else (row_id, name, status)
        for row_id, name, status in before
    ]
    assert expected in client.get("/api/tasks").get_json()["tasks"]


@pytest.mark.parametrize("task_id", [1, 2])
def test_delete_persists_and_repeated_delete_is_not_found(test_context, task_id):
    client, _, _ = test_context
    before = read_tasks(test_context)
    deleted_row = next(row for row in before if row[0] == task_id)

    response = client.delete(f"/api/tasks/{task_id}")

    assert response.status_code == 200
    assert response.get_json() == {
        "success": True,
        "deleted_task": {"id": deleted_row[0], "name": deleted_row[1], "status": deleted_row[2]},
    }
    remaining = [row for row in before if row[0] != task_id]
    assert read_tasks(test_context) == remaining
    assert client.get("/api/tasks").get_json()["tasks"] == [
        {"id": row_id, "name": name, "status": status} for row_id, name, status in remaining
    ]
    repeated = client.delete(f"/api/tasks/{task_id}")
    assert repeated.status_code == 404
    assert repeated.get_json() == {"error": "Task not found"}
    assert read_tasks(test_context) == remaining


def test_dashboard_search_and_filter_controls(test_context):
    client, _, _ = test_context
    elements = []

    class ControlParser(HTMLParser):
        def handle_starttag(self, tag, attrs):
            elements.append((tag, dict(attrs)))

    response = client.get("/dashboard")
    assert response.status_code == 200
    ControlParser().feed(response.get_data(as_text=True))
    controls = {attrs["id"]: (tag, attrs) for tag, attrs in elements if "id" in attrs}
    for status in ("all", "pending", "completed"):
        tag, attrs = controls[f"filter-{status}"]
        assert tag == "button"
        assert attrs["type"] == "button"
        assert attrs["data-filter"] == status
        assert attrs["aria-pressed"] == str(status == "all").lower()
    assert controls["task-search"][1]["type"] == "search"
    assert controls["clear-search"][1]["type"] == "button"
    assert any(tag == "label" and attrs.get("for") == "task-search" for tag, attrs in elements)


def test_persistence_after_reinitialization_and_new_client(test_context):
    client, _, _ = test_context
    app_module = importlib.import_module("app")
    task = client.post("/api/tasks", json={"name": "Persist across reload"}).get_json()["task"]
    updated = client.patch(
        f"/api/tasks/{task['id']}", json={"name": "Saved name", "status": "completed"}
    )
    assert updated.status_code == 200
    assert client.delete("/api/tasks/2").status_code == 200
    before_reload = read_tasks(test_context)

    migrate(test_context[1])
    fresh_client = app_module.app.test_client()

    assert read_tasks(test_context) == before_reload
    assert fresh_client.get("/api/tasks").get_json()["tasks"] == [
        {"id": row_id, "name": name, "status": status}
        for row_id, name, status in before_reload
    ]
    assert (task["id"], "Saved name", "completed") in before_reload
    assert all(row_id != 2 for row_id, _, _ in before_reload)


def test_empty_database_is_not_reseeded_after_reload(test_context):
    client, _, _ = test_context
    for task_id, _, _ in read_tasks(test_context):
        assert client.delete(f"/api/tasks/{task_id}").status_code == 200

    app_module = importlib.import_module("app")
    migrate(test_context[1])

    assert read_tasks(test_context) == []
    assert app_module.app.test_client().get("/api/tasks").get_json() == {"success": True, "tasks": []}


@pytest.mark.parametrize("method, path, payload", [
    ("GET", "/api/tasks", None),
    ("POST", "/api/tasks", {"name": "New task"}),
    ("PATCH", "/api/tasks/2", {"name": "Changed task"}),
    ("DELETE", "/api/tasks/2", None),
])
def test_database_errors_are_json_and_preserve_data(test_context, monkeypatch, method, path, payload):
    client, _, _ = test_context
    before = read_tasks(test_context)
    app_module = importlib.import_module("app")

    def unavailable_database():
        raise psycopg.OperationalError("private database configuration")

    monkeypatch.setattr(app_module, "get_db", unavailable_database)
    response = client.open(path, method=method, json=payload)

    assert response.status_code == 500
    assert response.get_json() == {"error": "Database operation failed"}
    assert read_tasks(test_context) == before


def test_sql_metacharacters_are_saved_as_task_data(test_context):
    client, _, _ = test_context
    name = "Robert'); DROP TABLE tasks; --"
    response = client.post("/api/tasks", json={"name": name})

    assert response.status_code == 201
    assert read_tasks(test_context)[-1][1] == name
    assert client.get("/api/tasks").status_code == 200


@pytest.fixture
def security_context(test_context, monkeypatch):
    app_module = importlib.import_module("app")
    limiter = app_module.app.extensions["devflow_rate_limiter"]
    limiter.windows.clear()
    now = [100.0]
    monkeypatch.setattr(limiter, "clock", lambda: now[0])
    monkeypatch.setitem(app_module.app.config, "RATE_LIMIT_ENABLED", True)
    try:
        yield test_context, app_module.app, now
    finally:
        limiter.windows.clear()


@pytest.mark.parametrize("method, path, expected", [
    ("GET", "/dashboard", 200),
    ("GET", "/static/js/app.js", 200),
    ("GET", "/api/tasks", 200),
    ("GET", "/missing", 404),
    ("PUT", "/api/tasks", 405),
    ("POST", "/api/tasks", 400),
])
def test_security_headers_on_success_and_errors(test_context, method, path, expected):
    client, _, _ = test_context
    response = client.open(path, method=method)

    assert response.status_code == expected
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "camera=()" in response.headers["Permissions-Policy"]
    csp = response.headers["Content-Security-Policy"]
    for directive in ("script-src 'self'", "style-src 'self'", "connect-src 'self'", "frame-ancestors 'none'"):
        assert directive in csp
    assert "unsafe-inline" not in csp
    assert "unsafe-eval" not in csp
    assert "Strict-Transport-Security" not in response.headers


def test_hsts_only_on_secure_requests(test_context):
    client, _, _ = test_context
    assert client.get("/dashboard", base_url="https://example.test").headers["Strict-Transport-Security"] == "max-age=31536000"
    spoofed = client.get("/dashboard", headers={"X-Forwarded-Proto": "https"})
    assert "Strict-Transport-Security" not in spoofed.headers


@pytest.mark.parametrize("method, path", [
    ("POST", "/api/tasks"), ("PATCH", "/api/tasks/2"),
    ("DELETE", "/api/tasks/2"), ("GET", "/api/tasks"),
])
def test_oversized_requests_are_rejected_without_changes(test_context, method, path):
    client, _, _ = test_context
    before = read_tasks(test_context)
    response = client.open(path, method=method, data=b"x" * (16 * 1024 + 1), content_type="application/json")

    assert response.status_code == 413
    assert response.get_json() == {"error": "Request body exceeds the 16 KiB limit"}
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert read_tasks(test_context) == before


def test_streamed_oversized_request_is_rejected(test_context):
    client, _, _ = test_context
    before = read_tasks(test_context)
    response = client.post("/api/tasks", content_type="application/json", environ_overrides={
        "wsgi.input": BytesIO(b"x" * (16 * 1024 + 1)),
        "wsgi.input_terminated": True,
        "CONTENT_LENGTH": "",
    })

    assert response.status_code == 413
    assert response.is_json
    assert read_tasks(test_context) == before


def test_body_at_limit_is_accepted(test_context):
    client, _, _ = test_context
    name = "x" * (16 * 1024 - len(json.dumps({"name": ""})))
    body = json.dumps({"name": name})
    assert len(body.encode()) == 16 * 1024

    response = client.post("/api/tasks", data=body, content_type="application/json")

    assert response.status_code == 201
    assert response.get_json()["task"]["name"] == name


def test_read_limit_retry_and_window_expiry(security_context, monkeypatch):
    context, app, now = security_context
    client, _, _ = context
    monkeypatch.setitem(app.config, "RATE_LIMIT_READS", 2)
    assert client.get("/api/tasks").status_code == 200
    assert client.head("/api/tasks").status_code == 200
    now[0] += 10

    response = client.get("/api/tasks")

    assert response.status_code == 429
    assert response.get_json() == {"error": "Too many requests. Please try again later."}
    assert response.headers["Retry-After"] == "50"
    assert response.headers["Cache-Control"] == "no-store"
    assert "Content-Security-Policy" in response.headers
    now[0] += 50
    assert client.get("/api/tasks").status_code == 200


def test_shared_write_limit_cannot_be_bypassed_with_forwarded_ips(security_context, monkeypatch):
    context, app, _ = security_context
    client, _, _ = context
    monkeypatch.setitem(app.config, "RATE_LIMIT_WRITES", 2)
    before = read_tasks(context)
    assert client.post("/api/tasks", json={}).status_code == 400
    assert client.patch("/api/tasks/2", json={"status": "invalid"}).status_code == 400

    response = client.delete("/api/tasks/2", headers={"X-Forwarded-For": "192.0.2.123"})

    assert response.status_code == 429
    assert read_tasks(context) == before
    assert client.get("/api/tasks").status_code == 200


def test_static_dashboard_and_options_do_not_consume_read_quota(security_context, monkeypatch):
    context, app, _ = security_context
    client, _, _ = context
    monkeypatch.setitem(app.config, "RATE_LIMIT_READS", 1)
    for _ in range(3):
        assert client.get("/dashboard").status_code == 200
        assert client.get("/static/js/app.js").status_code == 200
        assert client.options("/api/tasks").status_code == 200
    assert client.get("/api/tasks").status_code == 200
    assert client.get("/api/tasks").status_code == 429


def test_normal_crud_with_security_enabled(security_context):
    context, _, _ = security_context
    client, _, _ = context
    created = client.post("/api/tasks", json={"name": "  Public demo  "})
    assert created.status_code == 201
    task_id = created.get_json()["task"]["id"]
    updated = client.patch(f"/api/tasks/{task_id}", json={"name": "Demo update", "status": "completed"})
    assert updated.status_code == 200
    assert updated.get_json()["task"] == {"id": task_id, "name": "Demo update", "status": "completed"}
    assert client.get("/api/tasks").status_code == 200
    assert client.delete(f"/api/tasks/{task_id}").status_code == 200
    assert all(row_id != task_id for row_id, _, _ in read_tasks(context))


def test_internal_errors_do_not_expose_exception_details(test_context, monkeypatch):
    client, _, _ = test_context
    app_module = importlib.import_module("app")
    monkeypatch.setitem(app_module.app.config, "PROPAGATE_EXCEPTIONS", False)

    def fail():
        raise RuntimeError("diagnostic marker must stay in server logs")

    monkeypatch.setattr(app_module, "get_db", fail)
    response = client.get("/api/tasks")

    assert response.status_code == 500
    assert response.get_json() == {"error": "Internal server error"}
    assert "diagnostic marker" not in response.get_data(as_text=True)
    assert "Content-Security-Policy" in response.headers


def test_dashboard_discloses_shared_demo_data(test_context):
    client, _, _ = test_context
    html = client.get("/dashboard").get_data(as_text=True)

    assert "everyone with the password can view, edit, or delete these tasks" in html
    assert "do not enter personal or confidential information" in html
    assert "Your personal workspace" not in html


def test_portfolio_dataset_can_be_prepared_with_existing_api(test_context):
    from pathlib import Path

    client, _, _ = test_context
    samples = json.loads((Path(__file__).resolve().parents[1] / "demo/tasks.json").read_text())
    assert 5 <= len(samples) <= 10
    assert len({task["name"] for task in samples}) == len(samples)
    assert all(set(task) == {"name", "status"} for task in samples)
    assert all(task["name"] == task["name"].strip() and task["name"] for task in samples)
    initial = client.get("/api/tasks").get_json()["tasks"]
    assert [{"name": task["name"], "status": task["status"]} for task in initial] == samples[:3]

    for task in samples[3:]:
        response = client.post("/api/tasks", json={"name": task["name"]})
        assert response.status_code == 201
        task_id = response.get_json()["task"]["id"]
        if task["status"] == "completed":
            assert client.patch(f"/api/tasks/{task_id}", json={"status": "completed"}).status_code == 200

    saved = [{"name": name, "status": status} for _, name, status in read_tasks(test_context)]
    assert saved == samples
    assert sum(task["status"] == "pending" for task in saved) == 5
    assert sum(task["status"] == "completed" for task in saved) == 3
