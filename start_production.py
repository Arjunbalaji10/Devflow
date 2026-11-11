"""Launch the fixed production configuration without accepting Gunicorn options."""

import os
from pathlib import Path
import sys

from access import AccessConfigurationError, require_access_configuration
from database import DatabaseConfigurationError, require_database_url


def main():
    if len(sys.argv) != 1:
        raise SystemExit("Production launcher accepts no arguments")
    if not sys.platform.startswith("linux"):
        raise SystemExit("Production launcher requires Linux")
    port = os.environ.get("PORT", "")
    if not port.isascii() or not port.isdecimal() or not 1 <= int(port) <= 65535:
        raise SystemExit("PORT must be an integer from 1 to 65535")
    try:
        require_database_url(os.environ.get("DATABASE_URL"))
    except DatabaseConfigurationError as error:
        raise SystemExit(str(error)) from None
    try:
        require_access_configuration(os.environ, force_required=True)
    except AccessConfigurationError as error:
        raise SystemExit(str(error)) from None

    root = Path(__file__).resolve().parent
    environment = os.environ.copy()
    environment.pop("GUNICORN_CMD_ARGS", None)
    environment.pop("FORWARDED_ALLOW_IPS", None)
    environment.pop("WEB_CONCURRENCY", None)
    environment["FLASK_DEBUG"] = "0"
    environment["DEVFLOW_ENV"] = "production"
    os.chdir(root)
    os.execve(sys.executable, [
        sys.executable, "-m", "gunicorn", "--config", str(root / "gunicorn.conf.py"),
        "app:app",
    ], environment)


if __name__ == "__main__":
    main()
