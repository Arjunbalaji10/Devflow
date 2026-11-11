# DevFlow

DevFlow is a responsive shared task dashboard built with Flask, PostgreSQL, and vanilla JavaScript. It provides a REST API, a shared-password access gate, session-bound CSRF protection, and automated Python and JavaScript tests.

## Architecture and behavior

```text
Browser HTML/CSS/JavaScript
          | same-origin requests and session cookie
          v
Flask + access/security hooks (Gunicorn on Linux)
          | parameterized SQL and explicit transactions
          v
PostgreSQL
```

The application requires an explicit PostgreSQL `DATABASE_URL`. Importing or starting it does not create schema or seed data. Run the versioned migrations in `migrations/` explicitly with `python migrate.py`. Request-scoped PostgreSQL connections are closed at Flask context teardown.

Production requires a Werkzeug scrypt hash in `DEVFLOW_ACCESS_PASSWORD_HASH` and a random value of at least 32 characters in `DEVFLOW_SESSION_SECRET`. Local development is open only when neither access variable is configured. When access is enabled, task API requests require an authenticated session and mutations also require the session CSRF token.

Features include creating, renaming, completing, filtering, searching, and deleting tasks. Task names are rendered as text. The server applies validation, transactions, security headers, a 16 KiB request limit, and process-wide API and login rate limits.

Key files:

```text
app.py                         Flask routes and WSGI entry point
access.py                      Authentication, sessions, and CSRF
database.py                    PostgreSQL configuration and connections
migrate.py, migrations/        Explicit versioned schema migrations
security.py                    Headers, body limits, and API rate limiting
start_production.py            Fixed Linux/Gunicorn launcher
gunicorn.conf.py               Production server and Render proxy policy
templates/, static/            HTML, CSS, and JavaScript frontend
tests/                         Unit, integration, and release verification
DEPLOYMENT.md                  Render architecture and release operations
POSTGRESQL_MIGRATION.md        Migration status and strict verification details
```

## Local development

Create a virtual environment and install the pinned requirements. PostgreSQL must already be running, and the selected development database must not contain data you need to preserve.

PowerShell:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:DATABASE_URL = "postgresql://user:password@127.0.0.1/devflow_development"
.\.venv\Scripts\python.exe migrate.py
$env:FLASK_DEBUG = "0"
.\.venv\Scripts\python.exe -m flask --app app run
```

Linux or macOS:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
export DATABASE_URL='postgresql://user:password@127.0.0.1/devflow_development'
.venv/bin/python migrate.py
FLASK_DEBUG=0 .venv/bin/python -m flask --app app run
```

Open `http://127.0.0.1:5000/dashboard`. Do not set `DEVFLOW_ENV=production` or `RENDER=true` locally. No `.env` loader is installed; keep URLs and credentials in your shell or secret manager and never commit them.

The optional [demo task list](demo/tasks.json) is reference data only; the application does not load it automatically. The ignored legacy `devflow.db` is not used by the current runtime and must be preserved until any separately authorized data migration is complete.

## API

| Method | Endpoint | Behavior |
| --- | --- | --- |
| GET | `/api/tasks` | List tasks ordered by ID |
| POST | `/api/tasks` | Create from `{"name":"Task name"}` |
| PATCH | `/api/tasks/<id>` | Update `name`, `status`, or both |
| DELETE | `/api/tasks/<id>` | Delete a task |

Statuses are `pending` and `completed`. Creation returns 201; successful reads, updates, and deletions return 200. Validation failures return 400, missing tasks return 404, oversized bodies return 413, and exhausted quotas return 429.

## Testing

Tests that exercise the database require an explicit, local-loopback PostgreSQL URL whose database name ends in `_test`. They create and remove unique schemas and never use `DATABASE_URL` as a test target.

```powershell
$env:TEST_DATABASE_URL = "postgresql://user:password@127.0.0.1/devflow_test"
.\.venv\Scripts\python.exe -m pip install pytest==9.2.0
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
node --test tests/frontend.test.mjs
```

Missing `TEST_DATABASE_URL` causes PostgreSQL integration tests to skip and is not a successful release result. For the exact Linux, PostgreSQL, Node, Gunicorn, TLS-proxy, and browser procedures, see [POSTGRESQL_MIGRATION.md](POSTGRESQL_MIGRATION.md#stage-3-production-verification).

## Render deployment

Use a Render Python Web Service and managed Render PostgreSQL in the same region. Use the database's internal URL for `DATABASE_URL`.

- Build command: `pip install -r requirements.txt`
- Pre-deploy command: `python migrate.py` (requires a paid service feature)
- Start command: `python start_production.py`
- Private environment values: `DATABASE_URL`, `DEVFLOW_ACCESS_PASSWORD_HASH`, and `DEVFLOW_SESSION_SECRET`

Render supplies `PORT` and `RENDER=true`. The launcher fixes one synchronous worker and one thread because current rate limits are process-local, disables preload/reload, rejects arguments, and removes inherited Gunicorn overrides. On Render only, Gunicorn accepts the platform-sanitized `X-Forwarded-Proto: https` signal so Flask recognizes edge TLS and emits HSTS. Direct and local Gunicorn runs trust no forwarded scheme headers.

See [DEPLOYMENT.md](DEPLOYMENT.md) for the security boundary, HSTS implications, database operations, verification, and unresolved release prerequisites. This repository configuration does not authorize a deploy or production data change.

## Demo limitations

Everyone with the shared password uses the same tasks and process-wide quotas: 300 API reads and 60 writes per 60 seconds, plus a shared failed-login throttle. There are no accounts, per-user permissions, private workspaces, or automatic resets. Use only generic demonstration data and maintain recoverable PostgreSQL backups. These controls are basic abuse protection, not a privacy guarantee.
