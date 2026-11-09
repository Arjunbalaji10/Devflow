"""Real Linux/Gunicorn checks using disposable data and the Python standard library.

Run from the project root with the Linux virtual environment's Python.
"""

from contextlib import contextmanager
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]


def request(base, path, method="GET", data=None, headers=None, context=None):
    body = json.dumps(data).encode() if data is not None else None
    supplied = {"Content-Type": "application/json", **(headers or {})}
    req = Request(base + path, data=body, method=method, headers=supplied)
    try:
        response = urlopen(req, timeout=10, context=context)
    except HTTPError as error:
        response = error
    with response:
        return response.status, response.headers, response.read()


@contextmanager
def gunicorn(directory, label, trust=None):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    environment = os.environ.copy()
    environment.update(DEVFLOW_DATABASE=str(directory / "demo.db"), FLASK_DEBUG="0", PORT=str(port))
    for key in ("GUNICORN_CMD_ARGS", "FORWARDED_ALLOW_IPS"):
        environment.pop(key, None)
    command = [
        sys.executable, "-m", "gunicorn", "app:app", "--bind", f"0.0.0.0:{port}",
        "--workers", "1", "--access-logfile", "-", "--error-logfile", "-",
    ]
    if trust is not None:
        command.append(f"--forwarded-allow-ips={trust}")
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
            yield base, port
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    contents = log.read_text()
    assert not any(marker in contents for marker in ("Traceback", "[ERROR]", "[CRITICAL]")), label
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


def verify(directory):
    with gunicorn(directory, "documented-startup") as (base, port):
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

        status, _, body = request(base, "/api/tasks", "POST", {"name": "  Linux demo  "})
        assert status == 201
        task = json.loads(body)["task"]
        task_id = task["id"]
        assert task["name"] == "Linux demo"
        status, _, body = request(base, f"/api/tasks/{task_id}", "PATCH", {"name": "Saved Linux demo"})
        assert status == 200 and json.loads(body)["task"]["status"] == "pending"
        status, _, body = request(base, f"/api/tasks/{task_id}", "PATCH", {"status": "completed"})
        assert status == 200 and json.loads(body)["task"]["name"] == "Saved Linux demo"
        assert request(base, "/api/tasks/2", "DELETE")[0] == 200
        assert request(base, "/api/tasks/2", "DELETE")[0] == 404
        assert request(base, "/api/tasks", "POST", {"name": "  "})[0] == 400
        status, _, body = request(base, "/api/tasks", "POST", {"name": "x" * (16 * 1024)})
        assert status == 413 and "error" in json.loads(body)
        print("PASS: CRUD contracts, validation, nonexistent IDs, and 16 KiB limit")

        with trusted_tls_proxy(directory, port) as (proxy, context):
            status, headers, _ = request(proxy, "/dashboard", headers={"X-Forwarded-Proto": "http"}, context=context)
            assert status == 200
            assert headers["Strict-Transport-Security"] == "max-age=31536000"
            check_headers(headers)
        print("PASS: real local TLS reverse proxy, trusted scheme detection, spoof overwrite, and HSTS")

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

    with gunicorn(directory, "restart", trust="") as (base, _):
        status, headers, body = request(base, "/api/tasks", headers={"X-Forwarded-Proto": "https"})
        assert status == 200 and "Strict-Transport-Security" not in headers
        tasks = json.loads(body)["tasks"]
        assert {"id": task_id, "name": "Saved Linux demo", "status": "completed"} in tasks
        assert all(task["id"] != 2 for task in tasks)
        print("PASS: untrusted forwarded scheme ignored; committed updates/deletions survive restart")
        for task in tasks:
            assert request(base, f"/api/tasks/{task['id']}", "DELETE")[0] == 200

    with gunicorn(directory, "empty-restart") as (base, _):
        assert json.loads(request(base, "/api/tasks")[2]) == {"success": True, "tasks": []}
    with sqlite3.connect(directory / "demo.db") as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    print("PASS: empty database remains empty after restart; SQLite integrity is OK")
    print("NOTE: browser CSP enforcement and Render's actual proxy require separate verification.")


if __name__ == "__main__":
    if not sys.platform.startswith("linux"):
        raise SystemExit("Linux is required; do not count Windows execution as Gunicorn verification.")
    if shutil.which("openssl") is None:
        raise SystemExit("OpenSSL is required for the temporary local TLS proxy test.")
    with tempfile.TemporaryDirectory(prefix="devflow-production-check-") as temporary:
        verify(Path(temporary))
