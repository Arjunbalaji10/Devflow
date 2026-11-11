"""Shared-password access checks, using only disposable test state."""

import importlib
import secrets

from flask import Flask, session
import pytest
from unittest.mock import Mock
from werkzeug.security import generate_password_hash

import access
import database


TEST_PASSWORD = secrets.token_urlsafe(24)
WRONG_PASSWORD = secrets.token_urlsafe(24)
TEST_PASSWORD_HASH = generate_password_hash(TEST_PASSWORD)
TEST_SESSION_SECRET = secrets.token_urlsafe(48)


def access_environment(**extra):
    values = {
        "DEVFLOW_ACCESS_PASSWORD_HASH": TEST_PASSWORD_HASH,
        "DEVFLOW_SESSION_SECRET": TEST_SESSION_SECRET,
    }
    values.update(extra)
    return values


def enable_access(app, monkeypatch):
    """Enable the gate with runtime-generated test credentials."""
    for key, value in {
        "ACCESS_ENABLED": True,
        "ACCESS_PASSWORD_HASH": TEST_PASSWORD_HASH,
        "ACCESS_PRODUCTION": False,
        "SECRET_KEY": TEST_SESSION_SECRET,
        "SESSION_COOKIE_NAME": "devflow_session",
        "SESSION_COOKIE_SECURE": False,
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "SESSION_COOKIE_DOMAIN": None,
        "SESSION_COOKIE_PATH": "/",
        "SESSION_REFRESH_EACH_REQUEST": False,
    }.items():
        monkeypatch.setitem(app.config, key, value)
    monkeypatch.setitem(app.extensions, "devflow_login_limiter", access.FailedLoginLimiter())


@pytest.fixture
def protected_context(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://127.0.0.1/devflow_unreachable_test")
    app_module = importlib.import_module("app")
    app = app_module.app
    enable_access(app, monkeypatch)
    return app, app.test_client()


@pytest.fixture
def protected_database_context(test_context, monkeypatch):
    app_module = importlib.import_module("app")
    app = app_module.app
    enable_access(app, monkeypatch)
    return app, app.test_client(), test_context


def login(client, password=TEST_PASSWORD, base_url=None):
    options = {"base_url": base_url} if base_url else {}
    response = client.get("/login", **options)
    assert response.status_code == 200
    with client.session_transaction() as state:
        token = state["access_csrf_token"]
    return client.post("/login", data={"_csrf_token": token, "password": password},
                       **options)


def session_csrf(client):
    with client.session_transaction() as state:
        return state["access_csrf_token"]


def test_local_configuration_is_open_only_when_credentials_are_absent():
    assert access.require_access_configuration({}) == {
        "enabled": False, "production": False,
        "password_hash": "", "session_secret": "",
    }
    configured = access.require_access_configuration(access_environment())
    assert configured["enabled"] is True
    assert configured["production"] is False


@pytest.mark.parametrize("environment", [
    {"RENDER": "true"},
    {"DEVFLOW_ENV": "production"},
    {"DEVFLOW_ACCESS_REQUIRED": "true"},
    {"DEVFLOW_ACCESS_PASSWORD_HASH": TEST_PASSWORD_HASH},
    {"DEVFLOW_SESSION_SECRET": TEST_SESSION_SECRET},
])
def test_required_or_partial_configuration_fails_closed(environment):
    with pytest.raises(access.AccessConfigurationError):
        access.require_access_configuration(environment)


def test_forced_production_configuration_and_values_are_validated():
    with pytest.raises(access.AccessConfigurationError, match="PASSWORD_HASH"):
        access.require_access_configuration({}, force_required=True)
    with pytest.raises(access.AccessConfigurationError, match="scrypt"):
        access.require_access_configuration({
            "DEVFLOW_ACCESS_PASSWORD_HASH": "not-a-password-hash",
            "DEVFLOW_SESSION_SECRET": TEST_SESSION_SECRET,
        })
    with pytest.raises(access.AccessConfigurationError, match="at least 32"):
        access.require_access_configuration({
            "DEVFLOW_ACCESS_PASSWORD_HASH": TEST_PASSWORD_HASH,
            "DEVFLOW_SESSION_SECRET": "short",
        })


def test_production_cookie_configuration_is_secure_and_host_only():
    app = Flask(__name__)
    access.configure_access(app, access_environment(DEVFLOW_ENV="production"))

    @app.get("/cookie")
    def cookie():
        session["access_authenticated"] = True
        session.permanent = True
        return "ok"

    response = app.test_client().get("/cookie", base_url="https://example.test")
    cookie = response.headers["Set-Cookie"]
    assert cookie.startswith("__Host-devflow_session=")
    assert "; Secure" in cookie
    assert "; HttpOnly" in cookie
    assert "; SameSite=Lax" in cookie
    assert "; Path=/" in cookie
    assert "; Domain=" not in cookie


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/tasks"),
    ("HEAD", "/api/tasks"),
    ("OPTIONS", "/api/tasks"),
    ("POST", "/api/tasks"),
    ("PATCH", "/api/tasks/1"),
    ("DELETE", "/api/tasks/1"),
])
def test_every_task_api_method_rejects_before_database_access(
        protected_context, monkeypatch, method, path):
    _, client = protected_context
    connect = Mock(side_effect=AssertionError("authentication must run before database access"))
    monkeypatch.setattr(database, "connect_database", connect)

    response = client.open(path, method=method, json={})

    assert response.status_code == 401
    if method != "HEAD":
        assert response.get_json() == {"error": "Authentication required"}
    assert response.headers["Cache-Control"] == "no-store"
    connect.assert_not_called()


def test_dashboard_redirects_and_login_rotates_session_and_csrf(protected_context):
    _, client = protected_context
    response = client.get("/dashboard")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")

    login_page = client.get("/login")
    assert login_page.status_code == 200
    with client.session_transaction() as state:
        before = state["access_csrf_token"]
        assert "access_authenticated" not in state
    response = client.post("/login", data={
        "_csrf_token": before,
        "password": TEST_PASSWORD,
    })
    assert response.status_code == 303
    with client.session_transaction() as state:
        assert state["access_authenticated"] is True
        assert state["access_csrf_token"] != before
        assert TEST_PASSWORD not in state.values()
    assert client.get("/dashboard").status_code == 200


def test_root_redirects_unauthenticated_user_to_login(protected_context):
    _, client = protected_context

    response = client.get("/")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_root_redirects_authenticated_user_to_dashboard(protected_context):
    _, client = protected_context
    assert login(client).status_code == 303

    response = client.get("/")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")


def test_login_requires_pre_authentication_csrf(protected_context):
    _, client = protected_context
    assert client.post("/login", data={"password": TEST_PASSWORD}).status_code == 403
    client.get("/login")
    assert client.post("/login", data={
        "_csrf_token": secrets.token_urlsafe(32),
        "password": TEST_PASSWORD,
    }).status_code == 403


def test_csrf_rejection_preserves_data_and_valid_token_preserves_crud_contracts(
        protected_database_context):
    _, client, _ = protected_database_context
    assert login(client).status_code == 303
    before = client.get("/api/tasks").get_json()["tasks"]

    assert client.post("/api/tasks", json={"name": "Blocked"}).status_code == 403
    assert client.patch("/api/tasks/1", json={"status": "pending"},
                        headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert client.delete("/api/tasks/1").status_code == 403
    assert client.get("/api/tasks").get_json()["tasks"] == before

    headers = {"X-CSRF-Token": session_csrf(client)}
    created = client.post("/api/tasks", json={"name": "Authenticated demo"},
                          headers=headers)
    assert created.status_code == 201
    task = created.get_json()["task"]
    updated = client.patch(f"/api/tasks/{task['id']}", json={"status": "completed"},
                           headers=headers)
    assert updated.status_code == 200
    assert updated.get_json()["task"]["status"] == "completed"
    assert client.delete(f"/api/tasks/{task['id']}", headers=headers).status_code == 200


def test_logout_requires_csrf_and_clears_access(protected_context):
    _, client = protected_context
    assert login(client).status_code == 303
    assert client.post("/logout").status_code == 403
    response = client.post("/logout", data={"_csrf_token": session_csrf(client)})
    assert response.status_code == 303
    assert client.get("/api/tasks").status_code == 401


def test_failed_login_rate_limit_and_expiry(protected_context):
    app, client = protected_context
    now = [100.0]
    app.extensions["devflow_login_limiter"] = access.FailedLoginLimiter(
        limit=2, seconds=60, clock=lambda: now[0]
    )
    client.get("/login")
    token = session_csrf(client)
    for _ in range(2):
        response = client.post("/login", data={
            "_csrf_token": token, "password": WRONG_PASSWORD,
        })
        assert response.status_code == 401
        assert TEST_PASSWORD not in response.get_data(as_text=True)
    limited = client.post("/login", data={
        "_csrf_token": token, "password": WRONG_PASSWORD,
    })
    assert limited.status_code == 429
    assert limited.headers["Retry-After"] == "60"
    now[0] += 60
    assert client.post("/login", data={
        "_csrf_token": token, "password": WRONG_PASSWORD,
    }).status_code == 401


def test_tampered_session_cookie_fails_closed(protected_context):
    app, client = protected_context
    assert login(client).status_code == 303
    client.set_cookie(app.config["SESSION_COOKIE_NAME"], "tampered")
    assert client.get("/api/tasks").status_code == 401
