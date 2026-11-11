# PostgreSQL migration progress

Stage 1 adds `psycopg[binary]==3.3.8` and `database.py`. The connection layer
requires an explicit PostgreSQL `DATABASE_URL`, preserves its TLS options, uses
dictionary rows, and closes request connections at Flask context teardown.
Connections use autocommit for reads; writes must use an explicit
`connection.transaction()` block. Connection attempts have a ten-second timeout
and automatic prepared statements are disabled for transaction-pool compatibility.
Importing the layer never connects, creates schema, or seeds tasks.

Stage 2 integrates this layer into `app.py` and `start_production.py`.
`DATABASE_URL` is now required at startup in every environment; `DEVFLOW_DATABASE`
is no longer read. Startup validates configuration but makes no database connection,
schema changes, or seed inserts. GET returns rows ordered by ID. POST uses
`INSERT ... RETURNING`; PATCH and DELETE lock the selected row with
`SELECT ... FOR UPDATE` inside explicit transactions. API routes, JSON contracts,
validation ordering, and empty PATCH behavior are retained. Existing Stage 10
worker, thread, preload, reload, logging, and proxy-trust settings are retained.

Stage 10 Part 5.2 adds a shared-password access gate. Production startup requires
`DEVFLOW_ACCESS_PASSWORD_HASH` containing a Werkzeug scrypt hash and
`DEVFLOW_SESSION_SECRET` containing at least 32 random characters. Render and
`start_production.py` fail closed when either is missing. Local Flask development
remains open when both are absent. Authenticated task mutations require a rotated,
session-bound CSRF token; login has its own pre-authentication token and failed-login
limit. Credentials and plaintext passwords must never be committed or printed.

## Explicit schema migrations

`migrations/0001_tasks.sql` creates an empty tasks table with a BIGINT identity ID
and existing name/status constraints. `python migrate.py` records versions and
checksums in `schema_migrations` and applies pending files in one transaction.
A transaction-scoped advisory lock serializes concurrent runners. Repeat execution
is a no-op. Failed migrations roll back schema/history changes. Changed or unknown
history is refused, and existing unversioned tables are never overwritten/adopted.
Checksums normalize line endings across Windows/Linux. Applied files must not be
edited; add another numbered migration for future schema changes.

For local development, configure a disposable PostgreSQL database first, install
`requirements.txt`, then set `DATABASE_URL` privately in your shell and run:

```sh
python migrate.py
python -m flask --app app run
```

On Linux the existing hardened server command remains `python start_production.py`
with `PORT` set. Migrations are a separate explicit step, never a server startup
or import side effect. `MIGRATION_DATABASE_URL`, when set, overrides `DATABASE_URL`
for the migration command only. Use a direct connection for administrative work;
retain the supplied TLS parameters on any future hosted URL. No hosting setup or
cutover has been performed, and this intermediate stage is not ready for deployment.

## Isolated tests

Set `TEST_DATABASE_URL` to a dedicated **local loopback** PostgreSQL database whose
name ends in `_test`. The test role needs schema creation privileges. Test fixtures
ignore production `DATABASE_URL`, reject remote hosts, create a unique schema per
test, and drop only that generated schema afterward. Existing schemas/tables are
not reset. Missing TEST_DATABASE_URL skips integration tests explicitly; it is not
a successful PostgreSQL verification run. Fixture records are inserted explicitly
by tests only, never by the application or migration.

```sh
python -m pytest -q -p no:cacheprovider
node --test tests/frontend.test.mjs
```

Tests cover existing API/security contracts, real PostgreSQL CRUD and persistence,
write-then-fail rollback, row-lock waits and concurrent PATCH/DELETE, schema
constraints, atomic/concurrent migrations, checksum/history checks, no seeding,
missing configuration, and connection failures.

Stage 2 verification (2026-11-10): Windows Python 3.14.7 and Psycopg 3.3.8,
using a real disposable PostgreSQL 18.6 server bound to loopback in Ubuntu WSL:
`python -m pytest -q --basetemp=<unique workspace temporary directory> -p no:cacheprovider`
completed with **141 passed, no skips**, in 23.21 seconds. The standalone
`node --test tests/frontend.test.mjs` run completed with **74 passed**.
`git diff --check` passed. PostgreSQL binaries/libraries were extracted under a
unique WSL temporary directory; no system service or hosted database was used.
This historical checkpoint is superseded by the final verification below.

## Stage 3 production verification

`tests/verify_production.py` now requires Linux and an explicit local
`TEST_DATABASE_URL`. It shares schema isolation with pytest and ignores inherited
production/admin URLs and libpq overrides in server/migration subprocesses.
Missing URLs, remote hosts, remote hostaddr overrides, service indirection, and
database names not ending in `_test` are rejected before a connection is opened.
Only a uniquely generated test schema is created and removed. Existing SQLite
files/backups and other PostgreSQL schemas are untouched.

The harness verifies real launcher rejection of missing/invalid DATABASE_URL;
fixed worker/thread/preload/reload/logging/proxy settings despite injected
arguments; no schema creation or seeding at startup; generic JSON on missing
schema or refused database connections; migration CLI repetition and rollback;
HTTP CRUD, validation, security headers, body/rate limits and TLS proxy behavior;
and persistence/empty collections across Gunicorn restarts. It observes
`pg_stat_activity` using a unique application name to check request connections
are closed after successful, invalid, and failing database requests.
The old SQLite integrity/WAL backup exercise has been removed from this runtime
harness; historical Stage 10 evidence and the backup instructions remain retained.
PostgreSQL backup/restore rehearsal is a separate remaining operational task.

Run supplementary checks on another Linux patch with:

```sh
python tests/verify_production.py
```

Release verification must use the strict gate:

```sh
python tests/verify_production.py --require-pinned-runtime
```

The gate stops before database setup unless `sys.version_info[:3] == (3, 14, 10)`.

### Exact Linux Python 3.14.10 verification commands

Run from the repository root as an unprivileged Linux user with Python **3.14.10**,
PostgreSQL 18.6 tools (`initdb`, `pg_ctl`, `createdb`), Node 24.21.0, Git and OpenSSL
already available on PATH. Add your PostgreSQL installation's bin directory to
PATH if necessary. Use a Linux Node binary for a fully native toolchain.
This creates its own temporary local database and needs no Neon credentials.
Run the complete block in one Bash session, including under WSL:

```bash
set -eu
test "$(uname -s)" = Linux
python3.14 -c 'import sys; assert sys.version_info[:3] == (3, 14, 10), "Python 3.14.10 required"'
for tool in initdb pg_ctl createdb node git openssl; do command -v "$tool" >/dev/null; done
unset DATABASE_URL MIGRATION_DATABASE_URL DEVFLOW_DATABASE
unset PGHOST PGHOSTADDR PGPORT PGDATABASE PGUSER PGSERVICE PGSERVICEFILE PGOPTIONS
unset PGPASSWORD PGPASSFILE PGSSLMODE PGREQUIRESSL
verification_env=$(mktemp -d /tmp/devflow-final-verification.XXXXXX)
python3.14 -m venv "$verification_env/venv"
verification_python="$verification_env/venv/bin/python"
"$verification_python" -m pip install -r requirements.txt pytest==9.2.0
"$verification_python" -c 'import sys; assert sys.version_info[:3] == (3, 14, 10)'
initdb -D "$verification_env/data" -U devflow_verifier --auth=trust --encoding=UTF8 --no-locale
verification_port=$("$verification_python" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')
trap 'pg_ctl -D "$verification_env/data" -m fast -w stop >/dev/null 2>&1 || true' EXIT
pg_ctl -D "$verification_env/data" -l "$verification_env/postgres.log" -o "-h 127.0.0.1 -p $verification_port -k $verification_env" -w start
createdb -h 127.0.0.1 -p "$verification_port" -U devflow_verifier devflow_verification_test
export TEST_DATABASE_URL="postgresql://devflow_verifier@127.0.0.1:$verification_port/devflow_verification_test"
"$verification_python" --version
node --version
"$verification_python" -m pytest -q -p no:cacheprovider
node --test tests/frontend.test.mjs
"$verification_python" tests/verify_production.py --require-pinned-runtime
```

Require zero skipped integration tests. The temporary cluster uses trust
authentication only for its disposable local data, binds only to loopback, and
stops when the session exits. Temporary artifacts are retained for inspection;
no existing database or backup is deleted. If `venv` lacks ensurepip, bootstrap
pip inside that temporary environment using the official get-pip procedure
already described in DEPLOYMENT.md; do not alter system Python.

Stage 3 results (2026-11-10): Windows Python 3.14.7 **159 passed, zero skips**;
Linux Ubuntu WSL Python 3.14.4 **159 passed, zero skips**; standalone JavaScript
**74 passed** using Node 24.21.0 (Windows binary invoked from WSL). Both Python
runs used the real disposable PostgreSQL 18.6 database. The Linux production
harness passed all checks with Gunicorn 26.2.0 and Psycopg 3.3.8 on Python 3.14.4.
This is supplementary Linux evidence.

Final release verification (2026-11-11) used **Python 3.14.10** and a disposable
loopback **PostgreSQL 18.6** cluster. The Python suite completed with **183 passed,
zero skipped**. The standalone JavaScript suite completed with **76 passed, zero
failed**. The strict production verification passed, including the real
Gunicorn/PostgreSQL/TLS checks, and the Chromium browser verification passed,
including authentication and CSP enforcement. No hosted database, Render service,
or production setting was accessed or changed.

Remaining stages:

1. Decide whether legacy SQLite data must be migrated. If required, add read-only
   SQLite export and explicit PostgreSQL import with preserved IDs, names,
   statuses, empty collections, and sequence high-water marks; refuse accidental
   overwrite and verify the imported contents.
2. Rehearse managed Render PostgreSQL backup and restore before cutover.
3. Verify proxy sanitization and HSTS through the actual Render ingress after a
   separately authorized deployment; local verification cannot prove that hosted
   boundary.

Never use a live database for tests. Store connection URLs only in environment
configuration; do not print them or commit them. The existing SQLite file and
backups must be retained. Local installation: `python -m pip install -r requirements.txt`.
Stage 1 unit checks: `python -m pytest -q tests/test_database.py`.
