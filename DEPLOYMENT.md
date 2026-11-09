# DevFlow Version 1 production configuration

Version 1 is a public portfolio demonstration with one shared task collection.
Anyone can read, create, edit, complete, or delete demonstration tasks. There are
no accounts or private workspaces. Use demonstration data only; never upload a
personal database, confidential information, credentials, or private task names.

This document prepares deployment. It does not provision or deploy a service.

## Database configuration

- Set `DEVFLOW_DATABASE` to an absolute SQLite file path in production, for example
  `/var/data/devflow.db`. The parent directory must already exist and be writable.
- If the variable is absent or empty, the local default remains `devflow.db`
  beside `app.py`, independent of the current working directory.
- Relative configured paths are relative to the process working directory.
- Configuration is read when `app` is imported. Restart after changing it.
- An invalid or inaccessible path fails startup; there is no silent fallback to
  another database. Do not create a database directory during the build.
- Initialization runs on import, inside a SQLite write transaction. A new tasks
  table receives three demonstration tasks. Existing tables, including empty
  tables after deletion, are preserved. Concurrent initializers serialize rather
  than inserting duplicate seeds. This is not a schema migration mechanism.

## Runtime and startup

Install production dependencies on Linux from the repository root:

```sh
python -m pip install -r requirements.txt
```

Flask and its dependencies retain the inspected environment's versions. Gunicorn
is the only new server dependency, pinned to 26.2.0. Its requirement is excluded on
Windows because Gunicorn requires Unix (or WSL). Pin the hosting Python version
explicitly after testing it on Linux; the local test environment uses 3.14.7.

Render configuration for a later authorized deployment:

| Setting | Value |
| --- | --- |
| Service | Python Web Service, paid instance with persistent disk |
| Build command | `pip install -r requirements.txt` |
| Start command | `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --access-logfile - --error-logfile -` |
| Disk mount | `/var/data` |
| Environment | `DEVFLOW_DATABASE=/var/data/devflow.db` |
| Health check | `/` (HTTP availability; does not check database access) |
| Dashboard | `/dashboard` |

The existing WSGI entry point is `app:app`. Start one synchronous worker initially
for this small shared SQLite demonstration. Flask continues serving templates
and `/static/` assets; there is no frontend build or separate frontend server.

Do not use `python app.py` or `flask run` as the production server. Direct local
startup no longer forces debug mode. Leave `FLASK_DEBUG` unset or set it to `0`
in production; do not enable a debugger or reloader in the Gunicorn command.
No secret key is needed by the current session-free application. Do not commit
environment secrets or add a placeholder production secret.

## Persistence and release checks

Render's ordinary filesystem is ephemeral. A free service cannot attach a
persistent disk. Only data below the mounted disk path survives restarts and
redeployments. The disk is available at runtime, not during builds or pre-deploy
commands. Keep one service instance; disk-backed deploys have downtime.

Start production with a fresh database of demonstration data, rather than copying
the local database. Maintain consistent SQLite backups using SQLite's backup API
and verify restoration; never reset a live database as part of normal startup.
Exclude local databases, virtual environments, caches, and secrets when preparing
the source repository for deployment.

Before deployment, run the complete suites from the project root:

```sh
python -m pytest -q
node --test tests/frontend.test.mjs
```

Pytest 9.2.0 and Node.js (tested locally with 24.21.0) are test tools, not production
runtime dependencies. Pytest also invokes the JavaScript suite, so Node must be
available in the test environment. A production-only install intentionally omits
pytest. Run Gunicorn startup and HTTP smoke checks on Linux/WSL with a temporary
database before claiming production-server verification.

After a later authorized deployment, verify static assets, CRUD, search, filters,
and persistence through a restart and redeploy. Monitor usage and keep all data
disposable.

## Public security and abuse controls

- All responses include a same-origin Content Security Policy, `nosniff`, frame
  protection, a referrer policy, and disabled camera/microphone/geolocation access.
  Inline scripts/styles, external scripts, and embedding are blocked. Existing
  modules, CSS, and API fetches are same-origin and need no CSP exceptions.
- HTTPS requests receive HSTS for one year, without subdomain coverage or preload.
  HTTP development does not. Verify HTTPS detection through the actual hosting
  proxy before release; do not blindly trust client-supplied forwarded headers.
- Bodies are limited to 16 KiB. Oversized requests return JSON 413 errors. Existing
  malformed JSON/field validation remains intact. This limits bytes, not name
  characters; normal small CRUD payloads retain their contracts.
- API traffic has process-wide fixed windows: 300 reads and 60 writes per 60
  seconds. GET/HEAD share the read quota; other methods share the write quota,
  except OPTIONS. Exhaustion returns JSON 429 with `Retry-After` seconds. Rejected
  validation attempts also consume quota; limits apply before database work.
- Static assets, the dashboard, and `/` do not consume API quotas. Declared
  oversized bodies are rejected before quota accounting; oversized streamed API
  bodies without a declared length consume quota before bounded reading rejects
  them. Requests are not retried automatically.
- The limiter stores only two counters, is thread-safe, and does not use forwarded
  IP addresses. All visitors share quotas, including legitimate visitors behind
  an abuser. Counters reset on process restart; multiple workers have independent
  quotas. Retain the documented one-worker command. This is a modest abuse brake,
  not distributed rate limiting or DDoS protection. Edge protection and monitoring
  remain operational requirements for greater exposure. Fixed windows permit a
  burst around a window boundary. No external limiter package or datastore is used.
- No authentication is added: anyone can still edit or delete shared tasks.
  Limits do not prevent sustained data growth, spam, or offensive user content.
  No private or confidential information belongs in this demonstration.

## Clean source and demonstration data

`.gitignore` excludes virtual environments, caches, SQLite files and journals,
backups, local environment files, private keys, credential files, and logs.
Ignore rules do not remove already-tracked files or clean Git history. Before the
first commit, inspect the staged file list and run a credential check. If a real
credential has ever been committed, rotate it and clean history separately; never
print its value in a report. Do not publish local SQLite files for review.

For first production startup, choose a fresh file path on the persistent disk.
The existing initializer creates only the three generic seed tasks. Never copy
the local database. Startup preserves edits and does not periodically reset data.
If demonstration data becomes unsuitable, stop the service, retain any needed
backup securely, and explicitly choose a new database filename. Restart to seed
the new file, then verify its contents before reopening access. This is an operator
maintenance procedure, not a public reset endpoint or automatic deletion job.

### Eight-task portfolio dataset

[demo/tasks.json](demo/tasks.json) is the reviewed source dataset for a fuller
portfolio demonstration: eight generic tasks, five pending and three completed.
It contains no IDs, personal names, contact information, credentials, or binary
database contents. Keep the JSON in source control; keep all SQLite files out.

Preparation strategy for a later release:

1. Choose a fresh database path on the persistent disk. Never overwrite or copy
   the local `devflow.db`. If a target file already exists, inspect it separately
   and choose a new filename rather than resetting it during preparation.
2. Start the app with that path while the new service is not yet publicly
   accessible. Its unchanged initializer creates the first three tasks listed
   in the dataset. Confirm their names and statuses via `GET /api/tasks`.
3. Add the remaining five tasks using `POST /api/tasks` with each task's name.
   POST creates pending tasks. For each additional sample marked completed,
   PATCH the returned ID with `{"status":"completed"}`. Do not hardcode IDs.
4. Verify the full names/statuses against the JSON and confirm summary counts of
   8 total, 5 pending, and 3 completed. Restart once and verify the collection.
5. If interrupted, read the current collection first and reconcile it against
   the dataset before adding more rows. Repeating POST blindly creates duplicates.
   Keep public access closed until the reviewed dataset and counts are correct.

This is an operator procedure using the existing API, not a new loader, seed
change, reset endpoint, or automatic data replacement. No database is created or
populated by repository preparation. The production/browser verification harnesses
still use their own isolated three-task startup databases.

## Linux production verification (Stage 9C)

The reproducible production check is `tests/verify_production.py`. It launches the
documented Gunicorn command with an ephemeral port and a fresh temporary SQLite
file, without importing or modifying the local application database. Run it on
Linux from the repository root after installing the pinned requirements:

```sh
verification_env=$(mktemp -d /tmp/devflow-verification.XXXXXX)
python3 -m venv "$verification_env/venv"
"$verification_env/venv/bin/python" -m pip install -r requirements.txt
"$verification_env/venv/bin/python" tests/verify_production.py
```

The harness requires the existing system `openssl` executable to generate an
ephemeral self-signed test certificate. It uses only Python standard-library
verification code and adds no production dependencies. On Ubuntu installations
without `ensurepip`, the isolated environment can be created with `--without-pip`
and pip bootstrapped inside it using the official
`https://bootstrap.pypa.io/get-pip.py`; do not install or change system Python.
WSL temporary files may disappear after the distribution stops, so keep setup
and verification in one Linux shell session.

The harness checks real HTTP responses for routes, static module MIME types,
CRUD, headers, the body limit, default API rate quotas and `Retry-After`. It restarts
Gunicorn against the same temporary database, verifies saved changes and deleted
rows, checks an empty database is not reseeded, and checks SQLite integrity. Each
server's log is checked for tracebacks and ERROR/CRITICAL entries after shutdown.

It also tests a real local TLS-terminating reverse proxy. That proxy overwrites
forwarded scheme headers and reaches Gunicorn over trusted loopback. A separate
Gunicorn run uses `--forwarded-allow-ips=` to prove an untrusted
`X-Forwarded-Proto: https` cannot produce HSTS. These local checks do not verify
Render's actual ingress or TLS certificate. At hosting verification time, identify
the trusted ingress and confirm headers are sanitized. Never solve proxy issues
by blindly trusting all forwarded sources or weakening the CSP.

Run both full suites in that Linux environment, with Node 24.21.0 and Git available:

```sh
"$verification_env/venv/bin/python" -m pip install pytest==9.2.0
"$verification_env/venv/bin/python" -m pytest -q
node --test tests/frontend.test.mjs
```

Stage 9C's Linux environment used Python 3.14.4, Gunicorn 26.2.0, and the pinned
production requirements. The Windows development Python remains 3.14.7. Verify
the exact selected hosting Python patch before release rather than assuming every
patch or Render's default has been tested. No WSL/Docker installation is required
by these instructions; use an existing Linux environment.

For an actual CSP check, start a separate local Gunicorn instance using a fresh
temporary demonstration database. From a host with an already-installed Chromium
browser and Node 24.21.0, run:

```sh
node tests/verify_browser.mjs /path/to/chrome http://127.0.0.1:18765/dashboard
```

On Windows, provide the quoted path to `chrome.exe`. The browser checker creates
an isolated temporary browser profile, verifies real module/CSS/API loading and
form creation, confirms an HTML-shaped task name stays text, and deliberately
tests that an inline script is blocked. The checker requires exactly three seed
tasks at startup and creates one disposable task. It must never target a live or
personal database. Stop the temporary Gunicorn server after verification. It
does not replace complete desktop/mobile interaction testing.

References:
- [Render Flask deployment](https://render.com/docs/deploy-flask)
- [Render persistent disks](https://render.com/docs/disks)
- [Flask Gunicorn guidance](https://flask.palletsprojects.com/en/stable/deploying/gunicorn/)
- [Gunicorn release](https://pypi.org/project/gunicorn/26.2.0/)
