"""Shared isolation for local tests and production verification, never hosting."""

from contextlib import contextmanager
import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from database import connect_database, require_database_url


def url_options(value, **options):
    parts = urlsplit(value)
    query = [(key, val) for key, val in parse_qsl(parts.query) if key not in options]
    query.extend(options.items())
    return urlunsplit(parts._replace(query=urlencode(query)))


def local_test_url(value):
    """Reject remote endpoints before connecting and pin the loopback address."""
    try:
        settings = conninfo_to_dict(require_database_url(value))
        host = settings.get("host")
        if (host not in ("localhost", "127.0.0.1", "::1")
                or settings.get("hostaddr", "127.0.0.1") not in ("127.0.0.1", "::1")
                or not settings["dbname"].endswith("_test")
                or "service" in settings):
            raise ValueError
    except (ValueError, RuntimeError):
        raise ValueError("TEST_DATABASE_URL must select a loopback PostgreSQL database ending in _test") from None
    # Explicit hostaddr prevents inherited PGHOSTADDR from redirecting traffic.
    return url_options(value, hostaddr="::1" if host == "::1" else "127.0.0.1")


@contextmanager
def isolated_postgres(value):
    """Create/drop only a unique owned schema, preserving all existing schemas."""
    safe_url = local_test_url(value)
    schema = "devflow_test_" + uuid.uuid4().hex
    with connect_database(safe_url) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            yield url_options(safe_url, options="-csearch_path=" + schema)
        finally:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
