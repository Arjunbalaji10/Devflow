"""Explicit versioned PostgreSQL migrations. Never run on application import."""

import hashlib
import os
from pathlib import Path

import psycopg

from database import DatabaseConfigurationError, connect_database


MIGRATIONS = Path(__file__).resolve().with_name("migrations")


class MigrationError(RuntimeError):
    pass


def migrate(database_url, directory=MIGRATIONS):
    """Apply files atomically under a transaction-scoped advisory lock.

    Applied files are immutable. Unknown versions and changed checksums fail.
    No records are seeded and unversioned existing tasks tables are not adopted.
    """
    files = sorted(Path(directory).glob("[0-9][0-9][0-9][0-9]_*.sql"))
    versions = [path.name.split("_", 1)[0] for path in files]
    if not files or len(set(versions)) != len(versions):
        raise MigrationError("Missing or duplicate migration versions")
    scripts = {}
    for version, path in zip(versions, files):
        # Canonical newlines keep Windows and Linux checkouts compatible.
        script = path.read_text(encoding="utf-8")
        scripts[version] = (script, hashlib.sha256(script.encode("utf-8")).hexdigest())
    with connect_database(database_url) as connection:
        with connection.transaction():
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (104021002,))
            connection.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    checksum TEXT NOT NULL,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            applied = connection.execute(
                "SELECT version, checksum FROM schema_migrations ORDER BY version"
            ).fetchall()
            if [row["version"] for row in applied] != versions[:len(applied)]:
                raise MigrationError("Database migration history is not a known prefix")
            for row in applied:
                if scripts[row["version"]][1] != row["checksum"]:
                    raise MigrationError("An applied migration checksum has changed")
            pending = versions[len(applied):]
            for version in pending:
                script, checksum = scripts[version]
                connection.execute(script)
                connection.execute(
                    "INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s)",
                    (version, checksum),
                )
    return pending


if __name__ == "__main__":
    try:
        completed = migrate(os.environ.get("MIGRATION_DATABASE_URL") or os.environ.get("DATABASE_URL"))
    except DatabaseConfigurationError as error:
        raise SystemExit(str(error)) from None
    except (psycopg.Error, MigrationError):
        raise SystemExit("PostgreSQL migration failed; check connectivity and migration history") from None
    print("Applied migrations: " + (", ".join(completed) or "none (up to date)"))
