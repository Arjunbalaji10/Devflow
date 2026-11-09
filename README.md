# DevFlow

DevFlow is a responsive task dashboard built with Flask, SQLite, and vanilla
JavaScript. It demonstrates a complete task workflow backed by a REST API,
persistent storage, and automated Python and JavaScript tests.

**Public demonstration:** Version 1 has one shared task collection and no login.
Anyone can view, create, edit, complete, or delete tasks. Use demonstration data
only. Never enter personal information, confidential work, or credentials.

## Features

- Create tasks, rename them inline, toggle completion, and confirm deletions.
- Search task names without case sensitivity and combine search with status filters.
- View Total, Pending, and Completed counts across the full collection.
- Use a responsive layout with keyboard controls and accessible status messages.
- Receive loading, empty, no-results, success, and error feedback.
- Persist task changes in SQLite across requests and application restarts.

Task names are rendered as text. The server validates input, uses parameterized
SQL and transactions, and applies security headers, a 16 KiB body limit, and basic
process-wide API rate limits. These controls do not make the public data private.

## Technology and architecture

Python, Flask, SQLite, Jinja HTML templates, CSS, vanilla JavaScript ES modules,
pytest, and Node's built-in test runner. Gunicorn serves the app on Linux.
There is no frontend framework, npm dependency installation, or frontend build.
Pinned production packages are in [requirements.txt](requirements.txt).

```text
Browser HTML/CSS/JavaScript
          | same-origin fetch requests
          v
Flask REST API + security hooks
          | parameterized SQL / transactions
          v
SQLite database
```

Flask serves the page and static assets. JavaScript keeps the fetched collection
separate from its filtered view and refreshes data after successful mutations.
Database connections are scoped to each request and closed at teardown.

```text
app.py                       Flask routes, SQLite setup, WSGI entry point
security.py                  Headers, body limits, API rate limiting
templates/dashboard.html     Dashboard markup
static/css/styles.css        Responsive styles
static/js/                   API, rendering, editing, completion, deletion
demo/tasks.json              Eight generic portfolio demonstration tasks
tests/                       API, configuration, frontend, repository tests
tests/verify_production.py    Real Linux/Gunicorn verification harness
tests/verify_browser.mjs      Headless Chromium CSP verification harness
requirements.txt             Pinned production dependencies
DEPLOYMENT.md                Production configuration and verification details
.agents/skills/              Project-specific Codex engineering guidance
```

Local database files, virtual environments, caches, logs, secrets, and temporary
artifacts are excluded by [.gitignore](.gitignore).

## Local setup

Start in the project directory. Tested runtimes are Python 3.14.7 on Windows and
Python 3.14.4 on Ubuntu WSL. Node 24.21.0 and Git are also needed for the complete
test suite, but not for serving the dashboard. Use an existing Python installation
with virtual-environment support.

Windows PowerShell:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:FLASK_DEBUG = "0"
.\.venv\Scripts\python.exe -m flask --app app run
```

Linux or macOS:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
FLASK_DEBUG=0 .venv/bin/python -m flask --app app run
```

Open **http://127.0.0.1:5000/dashboard**. The `/` route returns the API welcome
message. The Flask development server is for local development only.

By default, SQLite uses `devflow.db` beside `app.py`. For a different database, set
`DEVFLOW_DATABASE` **before startup**. Prefer an absolute path; its parent directory
must already exist and be writable. Unset or empty configuration retains the
local default. No `.env` loader is installed; export variables in your shell or
configure them in the hosting dashboard. Never commit a real environment file.

The app seeds three generic tasks when creating its tasks table. Restarts preserve
existing data, including an intentionally empty collection. The optional
[eight-task demo dataset](demo/tasks.json) is not loaded automatically; see the
[clean demo preparation procedure](DEPLOYMENT.md#eight-task-portfolio-dataset).

## API

| Method | Endpoint | Behavior |
| --- | --- | --- |
| GET | `/api/tasks` | List tasks ordered by ID |
| POST | `/api/tasks` | Create from `{"name":"Task name"}` |
| PATCH | `/api/tasks/<id>` | Update `name`, `status`, or both |
| DELETE | `/api/tasks/<id>` | Delete a task |

Statuses are `pending` and `completed`. Creation returns 201; successful reads,
updates, and deletions return 200. Validation failures return 400, missing tasks
return 404, oversized bodies return 413, and exhausted API quotas return 429 with
`Retry-After`. Routing errors can be HTML; frontend modules handle non-JSON errors.

## Testing

Install the test runner separately from production dependencies. Run from the
project root with Node and Git on `PATH`:

```powershell
.\.venv\Scripts\python.exe -m pip install pytest==9.2.0
.\.venv\Scripts\python.exe -m pytest -q
node --test tests/frontend.test.mjs
```

On Linux/macOS, replace `.\.venv\Scripts\python.exe` with `.venv/bin/python`.
If using the original `venv/` environment, substitute that directory name.

Pytest uses isolated temporary SQLite databases and also invokes the complete
JavaScript suite. Repository tests initialize Git only inside disposable pytest
temporary directories, never in the project. JavaScript unit tests use a minimal
DOM test double; they do not establish browser layout or CSP enforcement.

For real Gunicorn, TLS-proxy, persistence, rate-limit, and browser checks, follow
[production verification](DEPLOYMENT.md#linux-production-verification-stage-9c).
Those harnesses require Linux/OpenSSL or an existing Chromium browser as described
there. Verification must use disposable databases, never a live demonstration.

## Production deployment

The WSGI entry point is `app:app`. On Linux, with a writable database directory and
the pinned production requirements installed:

```sh
export DEVFLOW_DATABASE=/var/data/devflow.db
export FLASK_DEBUG=0
export PORT=10000
gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --access-logfile - --error-logfile -
```

For Render, use a paid Python Web Service with a persistent disk mounted at
`/var/data`, build with `pip install -r requirements.txt`, and use the Gunicorn
command above as the start command. Render supplies `PORT`; configure
`DEVFLOW_DATABASE` in service settings. Do not copy the local database to hosting.
Ordinary ephemeral storage cannot preserve SQLite changes through redeployments.

See [DEPLOYMENT.md](DEPLOYMENT.md) for proxy trust, HSTS, backups, startup,
verification, and the remaining hosting checks. Keep one worker initially: API
quotas are process-local and the persistent SQLite disk serves one instance.
Run release checks before any deployment; this repository preparation does not
provision hosting or publish a site.

## Public demo limitations

All visitors share the same tasks and API quotas: 300 reads and 60 writes per
60-second fixed window. Counters reset when the process restarts. There are no
accounts, per-user permissions, private workspaces, automatic resets, or data
privacy guarantees. Public visitors may change or remove the demonstration data.
Rate limits are basic abuse controls, not complete spam or DDoS protection.

Use only generic sample tasks, keep recoverable backups if needed, and monitor
storage and abuse. Authentication and private workspaces are outside Version 1.
