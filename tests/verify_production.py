"""Real Linux/Gunicorn checks using an isolated local PostgreSQL schema.

Run from the project root with the Linux virtual environment's Python.
"""

from contextlib import contextmanager
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
from werkzeug.security import generate_password_hash


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import psycopg
from database import connect_database
from migrate import migrate
from postgres_support import isolated_postgres, url_options


ACCESS_PASSWORD = secrets.token_urlsafe(24)
ACCESS_PASSWORD_HASH = generate_password_hash(ACCESS_PASSWORD)
ACCESS_SESSION_SECRET = secrets.token_urlsafe(48)
AUTHENTICATED = {}


def server_environment(database_url, port):
    # Ignore inherited production/admin URLs and libpq override variables.
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("PG") and key not in
                   ("DATABASE_URL", "MIGRATION_DATABASE_URL", "DEVFLOW_DATABASE",
                    "DEVFLOW_ACCESS_PASSWORD_HASH", "DEVFLOW_SESSION_SECRET",
                    "DEVFLOW_ACCESS_REQUIRED", "DEVFLOW_ENV", "RENDER")}
    environment.update(
        DATABASE_URL=database_url,
        DEVFLOW_ACCESS_PASSWORD_HASH=ACCESS_PASSWORD_HASH,
        DEVFLOW_SESSION_SECRET=ACCESS_SESSION_SECRET,
        DEVFLOW_ENV="production",
        FLASK_DEBUG="1",
        PORT=str(port),
    )
    return environment


def verify_rejected_configuration(database_url):
    for value in (None, "", "sqlite:///old.db", "postgresql:///missing-host",
                  "postgresql://fixture:do-not-print@localhost/test?invalid_option=1"):
        environment = server_environment(database_url, 10000)
        if value is None:
            environment.pop("DATABASE_URL")
        else:
            environment["DATABASE_URL"] = value
        result = subprocess.run([sys.executable, str(ROOT / "start_production.py")],
                                cwd=ROOT, env=environment, capture_output=True, text=True, timeout=10)
        assert result.returncode != 0
        assert "DATABASE_URL" in result.stderr
        assert "do-not-print" not in result.stderr + result.stdout
        assert "Traceback" not in result.stderr
        assert "Booting worker" not in result.stderr
    print("PASS: missing/invalid DATABASE_URL fails before Gunicorn without secret disclosure")


def check_connections_closed(observer, application_name):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        count = observer.execute(
            "SELECT count(*) AS count FROM pg_stat_activity WHERE application_name = %s",
            (application_name,),
        ).fetchone()["count"]
        if count == 0:
            return
        time.sleep(0.02)
    raise AssertionError("Gunicorn left a PostgreSQL request connection open")


def request(base, path, method="GET", data=None, headers=None, context=None,
            authenticated=True):
    body = json.dumps(data).encode() if data is not None else None
    supplied = {"Content-Type": "application/json", **(headers or {})}
    state = AUTHENTICATED.get(base) if authenticated else None
    if state:
        supplied["Cookie"] = state["cookie"]
        if method not in ("GET", "HEAD", "OPTIONS"):
            supplied["X-CSRF-Token"] = state["csrf"]
    req = Request(base + path, data=body, method=method, headers=supplied)
    try:
        response = urlopen(req, timeout=10, context=context)
    except HTTPError as error:
        response = error
    with response:
        return response.status, response.headers, response.read()


def authenticate(base):
    parts = urlsplit(base)

    def exchange(method, path, body=None, headers=None):
        connection = HTTPConnection(parts.hostname, parts.port, timeout=10)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return (response.status,
                    {name.lower(): value for name, value in response.getheaders()},
                    response.read())
        finally:
            connection.close()

    status, headers, body = exchange("GET", "/login")
    assert status == 200
    pre_cookie = headers["set-cookie"].split(";", 1)[0]
    match = re.search(rb'name="_csrf_token" value="([^"]+)"', body)
    assert match is not None
    form = urlencode({"_csrf_token": match.group(1).decode(),
                      "password": ACCESS_PASSWORD}).encode()
    status, headers, _ = exchange("POST", "/login", form, {
        "Content-Type": "application/x-www-form-urlencoded",
        "Content-Length": str(len(form)),
        "Cookie": pre_cookie,
    })
    assert status == 303
    authenticated_cookie = headers["set-cookie"].split(";", 1)[0]
    status, _, body = exchange("GET", "/dashboard", headers={
        "Cookie": authenticated_cookie,
    })
    assert status == 200
    match = re.search(rb'<meta name="csrf-token" content="([^"]+)"', body)
    assert match is not None
    AUTHENTICATED[base] = {
        "cookie": authenticated_cookie,
        "csrf": match.group(1).decode(),
    }


@contextmanager
def gunicorn(directory, label, database_url, trust=None):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    environment = server_environment(database_url, port)
    # Intentionally poison all inherited override channels for the real launcher.
    environment.update(
        GUNICORN_CMD_ARGS="--preload --workers 9 --threads 9 --config /missing/config.py",
        FORWARDED_ALLOW_IPS="*", WEB_CONCURRENCY="9",
    )
    command = [sys.executable, str(ROOT / "start_production.py")]
    if trust is not None:
        # Test-only trusted ingress, never a production launcher option.
        environment.pop("GUNICORN_CMD_ARGS")
        command = [sys.executable, "-m", "gunicorn", "--config",
                   str(ROOT / "gunicorn.conf.py"), f"--forwarded-allow-ips={trust}", "app:app"]
    else:
        # Inspect what Gunicorn actually receives without adding launcher options.
        probe = directory / "probe"
        probe.mkdir(exist_ok=True)
        (probe / "sitecustomize.py").write_text(
            "import os, sys\n"
            "if os.environ.get('DEVFLOW_CONFIG_PROBE') == '1' and 'gunicorn' in sys.orig_argv:\n"
            "    import gunicorn.app.wsgiapp as entry\n"
            "    original_run = entry.run\n"
            "    def inspect_config(*args, **kwargs):\n"
            "        sys.argv.append('--print-config')\n"
            "        original_run(*args, **kwargs)\n"
            "    entry.run = inspect_config\n"
        )
        probe_env = {**environment, "DEVFLOW_CONFIG_PROBE": "1", "PYTHONPATH": str(probe)}
        result = subprocess.run(command, cwd=ROOT, env=probe_env, capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, "Gunicorn effective configuration probe failed"
        settings = {" ".join(line.split()) for line in result.stdout.splitlines()}
        for expected in ("workers = 1", "threads = 1", "worker_class = sync",
                         "preload_app = False", "reload = False", "forwarded_allow_ips = []",
                         "accesslog = -", "errorlog = -"):
            assert expected in settings, expected
        print("PASS: actual launcher effective config resists injected preload/workers/threads/config/trust")
        for arguments in (["--preload"], ["--workers", "9"], ["--threads", "9"],
                          ["--config", "/missing/config.py"], ["--forwarded-allow-ips=*"], ["other:app"]):
            rejected = subprocess.run(command + arguments, cwd=ROOT, env=environment,
                                      capture_output=True, text=True, timeout=10)
            assert rejected.returncode != 0 and "accepts no arguments" in rejected.stderr
    base = f"http://127.0.0.1:{port}"
    log = directory / f"{label}.log"
    with log.open("w") as output:
        process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=output, stderr=output)
        try:
            for _ in range(100):
                if process.poll() is not None:
                    raise AssertionError(f"Gunicorn failed to start; inspect {log}")
                try:
                    if request(base, "/")[0] == 200:
                        break
                except OSError:
                    pass
                time.sleep(0.1)
            else:
                raise AssertionError("Gunicorn startup timed out")
            authenticate(base)
            yield base, port
        finally:
            AUTHENTICATED.pop(base, None)
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    contents = log.read_text()
    assert not any(marker in contents for marker in ("Traceback", "[ERROR]", "[CRITICAL]")), label
    assert ACCESS_PASSWORD not in contents
    assert contents.count("Booting worker with pid:") == 1, label
    assert "Using worker: sync" in contents
    print(f"PASS: {label} Gunicorn startup, shutdown, and error-log review")


@contextmanager
def trusted_tls_proxy(directory, backend_port):
    """Terminate local TLS and overwrite scheme headers before the loopback upstream."""
    key, cert = directory / "test.key", directory / "test.pem"
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
        "-keyout", str(key), "-out", str(cert), "-subj", "/CN=localhost",
    ], check=True, capture_output=True)

    class Proxy(BaseHTTPRequestHandler):
        def do_GET(self):
            upstream = HTTPConnection("127.0.0.1", backend_port, timeout=10)
            try:
                # Never relay a browser-supplied scheme header.
                upstream.request("GET", self.path, headers={"X-Forwarded-Proto": "https"})
                response = upstream.getresponse()
                body = response.read()
                self.send_response(response.status)
                for name, value in response.getheaders():
                    if name.lower() not in ("connection", "transfer-encoding", "content-length"):
                        self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            finally:
                upstream.close()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert, key)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        # The generated certificate is intentionally self-signed and temporary.
        context = ssl._create_unverified_context()
        yield f"https://127.0.0.1:{server.server_port}", context
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def check_headers(headers):
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert "script-src 'self'" in headers["Content-Security-Policy"]
    assert "unsafe-inline" not in headers["Content-Security-Policy"]
    assert headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "camera=()" in headers["Permissions-Policy"]


def verify(directory, database_url):
    application_name = "devflow_verify_" + directory.name.replace("-", "_")
    web_url = url_options(database_url, application_name=application_name)
    verify_rejected_configuration(web_url)
    with connect_database(database_url) as observer:
        # Import/startup must not create schema or automatically seed tasks.
        with gunicorn(directory, "unmigrated", web_url) as (base, _):
            status, headers, body = request(base, "/api/tasks")
            assert status == 500 and json.loads(body) == {"error": "Database operation failed"}
            check_headers(headers)
            check_connections_closed(observer, application_name)
        assert observer.execute("SELECT to_regclass('tasks') AS name").fetchone()["name"] is None
        verify_migrations(directory, database_url)
        assert observer.execute("SELECT * FROM tasks").fetchall() == []
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            unavailable_url = url_options(web_url, port=str(reserved.getsockname()[1]))
            with gunicorn(directory, "database-unavailable", unavailable_url) as (base, _):
                status, headers, body = request(base, "/api/tasks")
                assert status == 500 and json.loads(body) == {"error": "Database operation failed"}
                check_headers(headers)
        print("PASS: no import-time schema/seeding; database errors remain generic JSON")
        verify_http(directory, web_url, observer, application_name)


def verify_http(directory, database_url, observer, application_name):
    with gunicorn(directory, "documented-startup", database_url) as (base, port):
        status, _, body = request(base, "/api/tasks", authenticated=False)
        assert status == 401 and json.loads(body) == {"error": "Authentication required"}
        for path in ("/", "/dashboard", "/static/css/styles.css", "/static/js/app.js",
                     "/static/js/api.js", "/static/js/editing.js", "/static/js/completion.js",
                     "/static/js/deletion.js", "/api/tasks"):
            status, headers, body = request(base, path)
            assert status == 200, path
            check_headers(headers)
            assert "Strict-Transport-Security" not in headers
            if path.endswith(".js"):
                assert "javascript" in headers["Content-Type"]
        print("PASS: homepage, dashboard, static modules, API, and HTTP security headers")
        assert json.loads(request(base, "/api/tasks")[2]) == {"success": True, "tasks": []}
        check_connections_closed(observer, application_name)

        status, _, body = request(base, "/api/tasks", "POST", {"name": "  Linux demo  "})
        assert status == 201
        task = json.loads(body)["task"]
        task_id = task["id"]
        assert task["name"] == "Linux demo"
        status, _, body = request(base, f"/api/tasks/{task_id}", "PATCH", {"name": "Saved Linux demo"})
        assert status == 200 and json.loads(body)["task"]["status"] == "pending"
        status, _, body = request(base, f"/api/tasks/{task_id}", "PATCH", {"status": "completed"})
        assert status == 200 and json.loads(body)["task"]["name"] == "Saved Linux demo"
        status, _, body = request(base, "/api/tasks", "POST", {"name": "Delete this disposable task"})
        assert status == 201
        deleted_id = json.loads(body)["task"]["id"]
        assert request(base, f"/api/tasks/{deleted_id}", "DELETE")[0] == 200
        assert request(base, f"/api/tasks/{deleted_id}", "DELETE")[0] == 404
        assert request(base, f"/api/tasks/{task_id}", "PATCH", {})[0] == 200
        assert request(base, f"/api/tasks/{task_id}", "PATCH", {"name": "Must not save", "status": "invalid"})[0] == 400
        check_connections_closed(observer, application_name)
        assert request(base, "/api/tasks", "POST", {"name": "  "})[0] == 400
        status, _, body = request(base, "/api/tasks", "POST", {"name": "x" * (16 * 1024)})
        assert status == 413 and "error" in json.loads(body)
        print("PASS: CRUD contracts, validation, nonexistent IDs, and 16 KiB limit")

        with trusted_tls_proxy(directory, port) as (proxy, context):
            status, headers, _ = request(proxy, "/login", headers={"X-Forwarded-Proto": "http"}, context=context)
            assert status == 200
            assert "Strict-Transport-Security" not in headers
            check_headers(headers)
        print("PASS: production launcher rejects forwarded scheme even behind local TLS proxy")

        # Exercise the real default quotas without changing application settings.
        write_statuses = [request(base, "/api/tasks", "POST", {})[0] for _ in range(61)]
        assert 400 in write_statuses and 429 in write_statuses
        assert request(base, "/api/tasks")[0] == 200  # Independent read quota.
        status, headers, body = request(base, "/api/tasks", "POST", {})
        assert status == 429 and 1 <= int(headers["Retry-After"]) <= 60
        assert "error" in json.loads(body)
        read_statuses = [request(base, "/api/tasks")[0] for _ in range(301)]
        assert 200 in read_statuses and 429 in read_statuses
        status, headers, _ = request(base, "/api/tasks")
        assert status == 429 and 1 <= int(headers["Retry-After"]) <= 60
        assert request(base, "/dashboard")[0] == 200
        print("PASS: actual read/write quotas, JSON 429, Retry-After, and dashboard availability")

    check_connections_closed(observer, application_name)
    with gunicorn(directory, "trusted-test-only", database_url, trust="127.0.0.1") as (base, port):
        with trusted_tls_proxy(directory, port) as (proxy, context):
            status, headers, _ = request(proxy, "/login", headers={"X-Forwarded-Proto": "http"}, context=context)
            assert status == 200
            assert headers["Strict-Transport-Security"] == "max-age=31536000"
            check_headers(headers)
        print("PASS: test-only explicit loopback trust, sanitized TLS proxy, and unchanged HSTS")

    with gunicorn(directory, "restart", database_url) as (base, _):
        status, headers, body = request(base, "/api/tasks", headers={"X-Forwarded-Proto": "https"})
        assert status == 200 and "Strict-Transport-Security" not in headers
        tasks = json.loads(body)["tasks"]
        assert {"id": task_id, "name": "Saved Linux demo", "status": "completed"} in tasks
        assert all(task["id"] != deleted_id for task in tasks)
        print("PASS: untrusted forwarded scheme ignored; committed updates/deletions survive restart")
        for task in tasks:
            assert request(base, f"/api/tasks/{task['id']}", "DELETE")[0] == 200

    assert migrate(database_url) == []
    with gunicorn(directory, "empty-restart", database_url) as (base, _):
        assert json.loads(request(base, "/api/tasks")[2]) == {"success": True, "tasks": []}
    check_connections_closed(observer, application_name)
    print("PASS: PostgreSQL connections close; migrations/restarts preserve an empty collection")
    print("NOTE: browser CSP enforcement and Render's actual proxy require separate verification.")


def verify_migrations(directory, database_url):
    environment = server_environment(database_url, 10000)
    for expected in ("0001", "none (up to date)"):
        result = subprocess.run([sys.executable, str(ROOT / "migrate.py")], cwd=ROOT,
                                env=environment, capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, "Migration command failed"
        assert expected in result.stdout
    migration_files = directory / "migrations"
    migration_files.mkdir()
    shutil.copyfile(ROOT / "migrations/0001_tasks.sql", migration_files / "0001_tasks.sql")
    (migration_files / "0002_failure.sql").write_text(
        "CREATE TABLE rollback_probe (id INTEGER); SELECT 1/0;", encoding="utf-8")
    try:
        migrate(database_url, migration_files)
    except psycopg.errors.DivisionByZero:
        pass
    else:
        raise AssertionError("Intentionally failing migration succeeded")
    with connect_database(database_url) as connection:
        assert connection.execute("SELECT to_regclass('rollback_probe') AS name").fetchone()["name"] is None
        assert connection.execute("SELECT version FROM schema_migrations").fetchall() == [{"version": "0001"}]
    assert migrate(database_url) == []
    print("PASS: actual migration CLI, repeatability, atomic failure rollback and history")


if __name__ == "__main__":
    if not sys.platform.startswith("linux"):
        raise SystemExit("Linux is required; do not count Windows execution as Gunicorn verification.")
    if shutil.which("openssl") is None:
        raise SystemExit("OpenSSL is required for the temporary local TLS proxy test.")
    exact = sys.version_info[:3] == (3, 14, 10)
    print(f"Verification runtime: {sys.version.split()[0]}; required production pin: 3.14.10")
    if "--require-pinned-runtime" in sys.argv:
        if not exact:
            raise SystemExit("Exact Python 3.14.10 is required; no verification was run")
    if any(argument != "--require-pinned-runtime" for argument in sys.argv[1:]):
        raise SystemExit("Only --require-pinned-runtime is supported")
    try:
        with isolated_postgres(os.environ.get("TEST_DATABASE_URL")) as database_url:
            with tempfile.TemporaryDirectory(prefix="devflow-production-check-") as temporary:
                verify(Path(temporary), database_url)
    except (ValueError, psycopg.Error):
        raise SystemExit("Verification failed: check the explicitly selected local TEST_DATABASE_URL") from None
    print("PASS: Linux production verification" + (" on pinned Python 3.14.10" if exact else " (supplementary runtime only)"))
