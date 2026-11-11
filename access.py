"""Shared-password access control for the small public demonstration."""

from datetime import timedelta
import hmac
import os
import secrets
import threading
import time

from flask import current_app, jsonify, redirect, request, session, url_for
from werkzeug.security import check_password_hash


class AccessConfigurationError(RuntimeError):
    """Invalid access configuration, reported without credential values."""


class FailedLoginLimiter:
    """A bounded, process-wide failed-login window for the single worker."""

    def __init__(self, limit=10, seconds=600, clock=time.monotonic):
        self.limit = limit
        self.seconds = seconds
        self.clock = clock
        self.started = clock()
        self.failures = 0
        self.lock = threading.Lock()

    def retry_after(self):
        with self.lock:
            now = self.clock()
            if now - self.started >= self.seconds:
                self.started = now
                self.failures = 0
            if self.failures < self.limit:
                return 0
            return max(1, int(self.seconds - (now - self.started) + 0.999))

    def record_failure(self):
        with self.lock:
            now = self.clock()
            if now - self.started >= self.seconds:
                self.started = now
                self.failures = 0
            self.failures += 1

    def reset(self):
        with self.lock:
            self.started = self.clock()
            self.failures = 0


def _enabled_setting(value):
    if value is None or not value.strip():
        return False
    normalized = value.strip().lower()
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("0", "false", "no", "off"):
        return False
    raise AccessConfigurationError("DEVFLOW_ACCESS_REQUIRED must be true or false")


def require_access_configuration(environ=None, force_required=False):
    """Return validated environment configuration without exposing its values."""
    environ = os.environ if environ is None else environ
    production = (force_required
                  or environ.get("RENDER", "").strip().lower() == "true"
                  or environ.get("DEVFLOW_ENV", "").strip().lower() == "production")
    password_hash = environ.get("DEVFLOW_ACCESS_PASSWORD_HASH", "").strip()
    session_secret = environ.get("DEVFLOW_SESSION_SECRET", "").strip()
    explicitly_required = _enabled_setting(environ.get("DEVFLOW_ACCESS_REQUIRED"))
    enabled = production or explicitly_required or bool(password_hash or session_secret)

    if not enabled:
        return {"enabled": False, "production": False,
                "password_hash": "", "session_secret": ""}
    if not password_hash:
        raise AccessConfigurationError("DEVFLOW_ACCESS_PASSWORD_HASH is required")
    if not session_secret:
        raise AccessConfigurationError("DEVFLOW_SESSION_SECRET is required")
    if not password_hash.startswith("scrypt:") or password_hash.count("$") != 2:
        raise AccessConfigurationError(
            "DEVFLOW_ACCESS_PASSWORD_HASH must be a Werkzeug scrypt hash"
        )
    if len(session_secret) < 32:
        raise AccessConfigurationError("DEVFLOW_SESSION_SECRET must be at least 32 characters")
    try:
        check_password_hash(password_hash, "configuration-validation")
    except (TypeError, ValueError):
        raise AccessConfigurationError(
            "DEVFLOW_ACCESS_PASSWORD_HASH must be a valid Werkzeug scrypt hash"
        ) from None
    return {"enabled": True, "production": production,
            "password_hash": password_hash, "session_secret": session_secret}


def new_csrf_token():
    return secrets.token_urlsafe(32)


def csrf_token():
    if not current_app.config["ACCESS_ENABLED"]:
        return ""
    token = session.get("access_csrf_token")
    if not isinstance(token, str) or not token:
        token = new_csrf_token()
        session["access_csrf_token"] = token
    return token


def valid_csrf_token(value):
    expected = session.get("access_csrf_token")
    return (isinstance(value, str) and isinstance(expected, str)
            and bool(value) and hmac.compare_digest(value, expected))


def authenticated():
    return (not current_app.config["ACCESS_ENABLED"]
            or session.get("access_authenticated") is True)


def verify_password(value):
    if not isinstance(value, str):
        return False
    try:
        return check_password_hash(current_app.config["ACCESS_PASSWORD_HASH"], value)
    except (TypeError, ValueError):
        return False


def establish_session():
    session.clear()
    session["access_authenticated"] = True
    session["access_csrf_token"] = new_csrf_token()
    session.permanent = True


def clear_session():
    session.clear()


def _json_access_error(message, status, retry_after=None):
    response = jsonify({"error": message})
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    if retry_after is not None:
        response.headers["Retry-After"] = str(retry_after)
    return response


def configure_access(app, environ=None):
    configuration = require_access_configuration(environ)
    app.config.update(
        ACCESS_ENABLED=configuration["enabled"],
        ACCESS_PASSWORD_HASH=configuration["password_hash"],
        ACCESS_PRODUCTION=configuration["production"],
        LOGIN_RATE_LIMIT=10,
        LOGIN_RATE_WINDOW=600,
    )
    if configuration["enabled"]:
        app.config.update(
            SECRET_KEY=configuration["session_secret"],
            SESSION_COOKIE_NAME=("__Host-devflow_session" if configuration["production"]
                                 else "devflow_session"),
            SESSION_COOKIE_SECURE=configuration["production"],
            SESSION_COOKIE_HTTPONLY=True,
            SESSION_COOKIE_SAMESITE="Lax",
            SESSION_COOKIE_DOMAIN=None,
            SESSION_COOKIE_PATH="/",
            PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
            SESSION_REFRESH_EACH_REQUEST=False,
        )
    limiter = FailedLoginLimiter(app.config["LOGIN_RATE_LIMIT"],
                                 app.config["LOGIN_RATE_WINDOW"])
    app.extensions["devflow_login_limiter"] = limiter

    @app.before_request
    def enforce_access():
        if not app.config["ACCESS_ENABLED"]:
            return None
        is_task_api = request.path == "/api/tasks" or request.path.startswith("/api/tasks/")
        if is_task_api:
            if not authenticated():
                return _json_access_error("Authentication required", 401)
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                if not valid_csrf_token(request.headers.get("X-CSRF-Token")):
                    return _json_access_error("Invalid CSRF token", 403)
        elif request.path == "/dashboard" and not authenticated():
            return redirect(url_for("login"))
        return None

    @app.after_request
    def protect_authenticated_responses(response):
        if app.config["ACCESS_ENABLED"] and (
                request.path in ("/login", "/logout", "/dashboard")
                or request.path == "/api/tasks"
                or request.path.startswith("/api/tasks/")):
            response.headers["Cache-Control"] = "no-store"
            response.vary.add("Cookie")
        return response

