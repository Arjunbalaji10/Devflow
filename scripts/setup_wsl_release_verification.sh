#!/usr/bin/env bash
# Prepare an isolated WSL Ubuntu release toolchain and run DevFlow verification.
# This script never uses a system PostgreSQL cluster or an inherited database URL.

set -Eeuo pipefail
umask 077

readonly PYTHON_VERSION="3.14.10"
readonly POSTGRES_VERSION="18.6"
readonly NODE_VERSION="24.21.0"
readonly PYTEST_VERSION="9.2.0"

readonly PYTHON_SHA256="de414a57119abef52784d8993e341d53aed635fc2b05c2607d9f98a2c5b72032"
readonly POSTGRES_SHA256="555610c24d53e4316da5b7d3fc25c279d96856d5e0e23ee308c328c5fa881d9f"
readonly NODE_SHA256="fd8e59d5a511510f6a298afb548f18c7d2b1be404d8b4a27d94fbe49f56cb2d6"

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
readonly TOOLCHAIN_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/devflow-release-toolchains"
readonly PYTHON_HOME="$TOOLCHAIN_ROOT/python-$PYTHON_VERSION"
readonly POSTGRES_HOME="$TOOLCHAIN_ROOT/postgresql-$POSTGRES_VERSION"
readonly NODE_HOME="$TOOLCHAIN_ROOT/node-v$NODE_VERSION-linux-x64"

RUN_ROOT=""
CLUSTER_STARTED=0
APP_PID=""
WINDOWS_BROWSER_NODE=""
WINDOWS_CHROME=""

die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

cleanup() {
    local status=$?
    trap - EXIT INT TERM HUP

    if [[ -n "$APP_PID" ]] && kill -0 "$APP_PID" >/dev/null 2>&1; then
        kill "$APP_PID" >/dev/null 2>&1 || true
        for _ in {1..50}; do
            kill -0 "$APP_PID" >/dev/null 2>&1 || break
            sleep 0.1
        done
        if kill -0 "$APP_PID" >/dev/null 2>&1; then
            kill -KILL "$APP_PID" >/dev/null 2>&1 || true
        fi
        wait "$APP_PID" >/dev/null 2>&1 || true
    fi
    unset DEVFLOW_BROWSER_PASSWORD DEVFLOW_ACCESS_PASSWORD_HASH DEVFLOW_SESSION_SECRET

    if [[ "$CLUSTER_STARTED" == 1 && -n "$RUN_ROOT" ]]; then
        "$POSTGRES_HOME/bin/pg_ctl" -D "$RUN_ROOT/pgdata" -m fast -w stop \
            >/dev/null 2>&1 || true
    fi

    if [[ -n "$RUN_ROOT" && -f "$RUN_ROOT/.devflow-temporary-verification" ]]; then
        case "$RUN_ROOT" in
            /tmp/devflow-release-verification.*)
                if [[ "${DEVFLOW_KEEP_TEMP:-0}" == 1 ]]; then
                    printf 'Temporary verification files retained at %s\n' "$RUN_ROOT" >&2
                else
                    rm -rf -- "$RUN_ROOT"
                fi
                ;;
            *)
                printf 'Refusing to remove unexpected temporary path: %s\n' "$RUN_ROOT" >&2
                status=1
                ;;
        esac
    fi

    exit "$status"
}
trap cleanup EXIT INT TERM HUP

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "Required command is missing: $1"
}

find_windows_browser_tools() {
    local candidate version
    require_command wslpath

    for candidate in \
        "/mnt/c/Program Files/nodejs/node.exe" \
        "/mnt/z/Program Files/nodejs/node.exe"; do
        if [[ -x "$candidate" ]]; then
            version=$("$candidate" --version 2>/dev/null | tr -d '\r')
            if [[ "$version" == "v$NODE_VERSION" ]]; then
                WINDOWS_BROWSER_NODE="$candidate"
                break
            fi
        fi
    done
    [[ -n "$WINDOWS_BROWSER_NODE" ]] \
        || die "Windows Node.js $NODE_VERSION is required to drive Windows Chrome"

    for candidate in \
        "/mnt/c/Program Files/Google/Chrome/Application/chrome.exe" \
        "/mnt/c/Program Files (x86)/Google/Chrome/Application/chrome.exe"; do
        if [[ -f "$candidate" ]]; then
            WINDOWS_CHROME="$candidate"
            break
        fi
    done
    [[ -n "$WINDOWS_CHROME" ]] || die "Windows Google Chrome was not found"
}

verify_download() {
    local expected=$1
    local file=$2
    printf '%s  %s\n' "$expected" "$file" | sha256sum --check --status - \
        || die "Checksum verification failed for $(basename -- "$file")"
}

install_from_destdir() {
    local staged_root=$1
    local destination=$2
    local staged_destination="$staged_root$destination"

    [[ -d "$staged_destination" ]] \
        || die "Expected staged installation is missing: $staged_destination"
    [[ ! -e "$destination" ]] \
        || die "Refusing to replace existing toolchain path: $destination"
    mkdir -p -- "$(dirname -- "$destination")"
    mv -- "$staged_destination" "$destination"
}

check_platform() {
    [[ "$(uname -s)" == Linux ]] || die "Linux is required"
    grep -qi microsoft /proc/sys/kernel/osrelease \
        || die "WSL2 is required; no Microsoft WSL kernel was detected"
    [[ -r /etc/os-release ]] || die "/etc/os-release is unavailable"

    # shellcheck disable=SC1091
    . /etc/os-release
    [[ "${ID:-}" == ubuntu ]] || die "Ubuntu is required (found ${ID:-unknown})"
    case "${VERSION_ID:-}" in
        22.04|24.04|26.04) ;;
        *) die "Unsupported Ubuntu release: ${VERSION_ID:-unknown}" ;;
    esac
    [[ "$(uname -m)" == x86_64 && "$(dpkg --print-architecture)" == amd64 ]] \
        || die "This script supports WSL Ubuntu amd64 only"
    [[ "$(id -u)" -ne 0 ]] || die "Run as an unprivileged WSL user, not root"
    [[ -n "${HOME:-}" && "$TOOLCHAIN_ROOT" == "$HOME"/* ]] \
        || die "Toolchains must remain under the Linux user's home directory"
    find_windows_browser_tools
}

check_project() {
    cd -- "$PROJECT_ROOT"
    [[ -f POSTGRESQL_MIGRATION.md && -f DEPLOYMENT.md && -f requirements.txt ]] \
        || die "Run this script from the DevFlow repository"
    [[ -f tests/verify_production.py && -f tests/frontend.test.mjs \
       && -f tests/verify_browser.mjs ]] \
        || die "Required verification files are missing"
    require_command git
    [[ "$(git rev-parse --show-toplevel)" == "$PROJECT_ROOT" ]] \
        || die "Resolved project root does not match the Git worktree root"
    git diff --check
}

clear_database_environment() {
    unset DATABASE_URL MIGRATION_DATABASE_URL DEVFLOW_DATABASE TEST_DATABASE_URL
    unset DEVFLOW_ACCESS_PASSWORD_HASH DEVFLOW_SESSION_SECRET DEVFLOW_ACCESS_REQUIRED
    unset DEVFLOW_ENV DEVFLOW_BROWSER_PASSWORD RENDER
    unset PGHOST PGHOSTADDR PGPORT PGDATABASE PGUSER PGSERVICE PGSERVICEFILE PGOPTIONS
    unset PGPASSWORD PGPASSFILE PGSSLMODE PGREQUIRESSL
}

install_build_prerequisites() {
    require_command sudo
    sudo -v || die "sudo access is required to install missing build prerequisites"
    sudo apt-get update
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        build-essential ca-certificates curl git openssl pkg-config tar xz-utils bzip2 \
        libbz2-dev libdb-dev libexpat1-dev libffi-dev libgdbm-dev \
        libgdbm-compat-dev liblzma-dev libmpdec-dev libncurses-dev libnsl-dev \
        libreadline-dev libsqlite3-dev libssl-dev libuuid1 uuid-dev zlib1g-dev
}

toolchains_ready() {
    [[ -x "$PYTHON_HOME/bin/python3.14" \
       && -x "$POSTGRES_HOME/bin/postgres" \
       && -x "$NODE_HOME/bin/node" ]] || return 1
    [[ "$($PYTHON_HOME/bin/python3.14 -c \
        'import platform; print(platform.python_version())')" == "$PYTHON_VERSION" \
       && "$($POSTGRES_HOME/bin/postgres --version)" == \
          "postgres (PostgreSQL) $POSTGRES_VERSION" \
       && "$($NODE_HOME/bin/node --version)" == "v$NODE_VERSION" ]]
}

install_python() {
    local source_archive="$RUN_ROOT/Python-$PYTHON_VERSION.tar.xz"
    local source_dir="$RUN_ROOT/Python-$PYTHON_VERSION"
    local stage="$RUN_ROOT/python-stage"

    if [[ -x "$PYTHON_HOME/bin/python3.14" ]]; then
        [[ "$($PYTHON_HOME/bin/python3.14 -c 'import platform; print(platform.python_version())')" == "$PYTHON_VERSION" ]] \
            || die "Existing Python toolchain has the wrong version: $PYTHON_HOME"
        return
    fi
    [[ ! -e "$PYTHON_HOME" ]] \
        || die "Incomplete Python toolchain exists; inspect it manually: $PYTHON_HOME"

    curl --fail --location --proto '=https' --tlsv1.2 \
        --output "$source_archive" \
        "https://www.python.org/ftp/python/$PYTHON_VERSION/Python-$PYTHON_VERSION.tar.xz"
    verify_download "$PYTHON_SHA256" "$source_archive"
    tar -xJf "$source_archive" -C "$RUN_ROOT"
    mkdir -p -- "$stage"
    (
        cd -- "$source_dir"
        ./configure --prefix="$PYTHON_HOME" --with-ensurepip=install
        make -j"$(nproc)"
        make altinstall DESTDIR="$stage"
    )
    install_from_destdir "$stage" "$PYTHON_HOME"
    "$PYTHON_HOME/bin/python3.14" -c \
        'import sys; assert sys.version_info[:3] == (3, 14, 10)'
}

install_postgres() {
    local source_archive="$RUN_ROOT/postgresql-$POSTGRES_VERSION.tar.bz2"
    local source_dir="$RUN_ROOT/postgresql-$POSTGRES_VERSION"
    local stage="$RUN_ROOT/postgres-stage"

    if [[ -x "$POSTGRES_HOME/bin/postgres" ]]; then
        [[ "$($POSTGRES_HOME/bin/postgres --version)" == "postgres (PostgreSQL) $POSTGRES_VERSION" ]] \
            || die "Existing PostgreSQL toolchain has the wrong version: $POSTGRES_HOME"
        return
    fi
    [[ ! -e "$POSTGRES_HOME" ]] \
        || die "Incomplete PostgreSQL toolchain exists; inspect it manually: $POSTGRES_HOME"

    curl --fail --location --proto '=https' --tlsv1.2 \
        --output "$source_archive" \
        "https://ftp.postgresql.org/pub/source/v$POSTGRES_VERSION/postgresql-$POSTGRES_VERSION.tar.bz2"
    verify_download "$POSTGRES_SHA256" "$source_archive"
    tar -xjf "$source_archive" -C "$RUN_ROOT"
    mkdir -p -- "$stage"
    (
        cd -- "$source_dir"
        ./configure --prefix="$POSTGRES_HOME" --with-openssl
        make -j"$(nproc)"
        make install DESTDIR="$stage"
    )
    install_from_destdir "$stage" "$POSTGRES_HOME"
    [[ "$($POSTGRES_HOME/bin/postgres --version)" == "postgres (PostgreSQL) $POSTGRES_VERSION" ]] \
        || die "Installed PostgreSQL version check failed"
}

install_node() {
    local source_archive="$RUN_ROOT/node-v$NODE_VERSION-linux-x64.tar.xz"
    local stage="$RUN_ROOT/node-stage"

    if [[ -x "$NODE_HOME/bin/node" ]]; then
        [[ "$($NODE_HOME/bin/node --version)" == "v$NODE_VERSION" ]] \
            || die "Existing Node.js toolchain has the wrong version: $NODE_HOME"
        return
    fi
    [[ ! -e "$NODE_HOME" ]] \
        || die "Incomplete Node.js toolchain exists; inspect it manually: $NODE_HOME"

    curl --fail --location --proto '=https' --tlsv1.2 \
        --output "$source_archive" \
        "https://nodejs.org/dist/v$NODE_VERSION/node-v$NODE_VERSION-linux-x64.tar.xz"
    verify_download "$NODE_SHA256" "$source_archive"
    mkdir -p -- "$stage" "$(dirname -- "$NODE_HOME")"
    tar -xJf "$source_archive" -C "$stage"
    [[ ! -e "$NODE_HOME" ]] || die "Refusing to replace existing Node.js path"
    mv -- "$stage/node-v$NODE_VERSION-linux-x64" "$NODE_HOME"
    [[ "$($NODE_HOME/bin/node --version)" == "v$NODE_VERSION" ]] \
        || die "Installed Node.js version check failed"
}

prepare_verification_environment() {
    local python_bin="$PYTHON_HOME/bin/python3.14"

    export PATH="$POSTGRES_HOME/bin:$NODE_HOME/bin:$PATH"
    export PYTHONDONTWRITEBYTECODE=1
    export PIP_DISABLE_PIP_VERSION_CHECK=1
    export PIP_NO_INPUT=1
    export npm_config_update_notifier=false

    for tool in initdb pg_ctl createdb psql node git openssl; do
        require_command "$tool"
    done
    [[ "$(node --version)" == "v$NODE_VERSION" ]] || die "Wrong Node.js is active"
    [[ "$(postgres --version)" == "postgres (PostgreSQL) $POSTGRES_VERSION" ]] \
        || die "Wrong PostgreSQL is active"
    "$python_bin" -c 'import sys; assert sys.version_info[:3] == (3, 14, 10)'

    "$python_bin" -m venv "$RUN_ROOT/venv"
    readonly VERIFICATION_PYTHON="$RUN_ROOT/venv/bin/python"
    "$VERIFICATION_PYTHON" -m pip install -r "$PROJECT_ROOT/requirements.txt" \
        "pytest==$PYTEST_VERSION"
    "$VERIFICATION_PYTHON" -c \
        'import sys; assert sys.version_info[:3] == (3, 14, 10)'
}

stop_temporary_app() {
    if [[ -n "$APP_PID" ]] && kill -0 "$APP_PID" >/dev/null 2>&1; then
        kill "$APP_PID"
        wait "$APP_PID" || true
    fi
    APP_PID=""
}

run_browser_verification() {
    local app_port browser_database_url browser_hash browser_script_windows
    local browser_secret chrome_windows

    createdb -h 127.0.0.1 -p "$("$VERIFICATION_PYTHON" -c \
        'from urllib.parse import urlsplit; print(urlsplit(__import__("os").environ["TEST_DATABASE_URL"]).port)')" \
        -U devflow_verifier devflow_browser_test
    browser_database_url="${TEST_DATABASE_URL%/devflow_verification_test}/devflow_browser_test"
    DATABASE_URL="$browser_database_url" "$VERIFICATION_PYTHON" migrate.py >/dev/null
    psql "$browser_database_url" -v ON_ERROR_STOP=1 >/dev/null <<'SQL'
INSERT INTO tasks (name, status) VALUES
    ('Learn Python', 'completed'),
    ('Understand APIs', 'pending'),
    ('Learn SQL', 'pending');
SQL

    DEVFLOW_BROWSER_PASSWORD=$("$VERIFICATION_PYTHON" -c \
        'import secrets; print(secrets.token_urlsafe(32))')
    browser_hash=$(printf '%s' "$DEVFLOW_BROWSER_PASSWORD" | "$VERIFICATION_PYTHON" -c \
        'import sys; from werkzeug.security import generate_password_hash; print(generate_password_hash(sys.stdin.read()))')
    browser_secret=$("$VERIFICATION_PYTHON" -c \
        'import secrets; print(secrets.token_urlsafe(48))')
    app_port=$("$VERIFICATION_PYTHON" -c \
        'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')

    DATABASE_URL="$browser_database_url" \
    DEVFLOW_ACCESS_PASSWORD_HASH="$browser_hash" \
    DEVFLOW_SESSION_SECRET="$browser_secret" \
    PORT="$app_port" \
        "$VERIFICATION_PYTHON" start_production.py \
        >"$RUN_ROOT/browser-server.log" 2>&1 &
    APP_PID=$!

    for _ in {1..100}; do
        if ! kill -0 "$APP_PID" >/dev/null 2>&1; then
            die "Temporary browser server exited; inspect $RUN_ROOT/browser-server.log"
        fi
        if curl --fail --silent --show-error "http://127.0.0.1:$app_port/" \
                >/dev/null 2>&1; then
            break
        fi
        sleep 0.1
    done
    curl --fail --silent --show-error "http://127.0.0.1:$app_port/" >/dev/null \
        || die "Temporary browser server did not become ready"

    browser_script_windows=$(wslpath -w "$PROJECT_ROOT/tests/verify_browser.mjs")
    chrome_windows=$(wslpath -w "$WINDOWS_CHROME")
    WSLENV="${WSLENV:+$WSLENV:}DEVFLOW_BROWSER_PASSWORD" \
    DEVFLOW_BROWSER_PASSWORD="$DEVFLOW_BROWSER_PASSWORD" \
        "$WINDOWS_BROWSER_NODE" "$browser_script_windows" "$chrome_windows" \
        "http://localhost:$app_port/dashboard"

    stop_temporary_app
    unset DEVFLOW_BROWSER_PASSWORD
}

start_isolated_postgres() {
    local port
    port=$("$VERIFICATION_PYTHON" -c \
        'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')

    initdb -D "$RUN_ROOT/pgdata" -U devflow_verifier \
        --auth=trust --encoding=UTF8 --no-locale
    {
        printf "listen_addresses = '127.0.0.1'\n"
        printf 'port = %s\n' "$port"
        printf "unix_socket_directories = '%s'\n" "$RUN_ROOT"
    } >> "$RUN_ROOT/pgdata/postgresql.conf"

    pg_ctl -D "$RUN_ROOT/pgdata" -l "$RUN_ROOT/postgres.log" -w start
    CLUSTER_STARTED=1
    createdb -h 127.0.0.1 -p "$port" -U devflow_verifier \
        devflow_verification_test
    export TEST_DATABASE_URL="postgresql://devflow_verifier@127.0.0.1:$port/devflow_verification_test"
}

run_verification() {
    local pytest_log="$RUN_ROOT/pytest.log"

    cd -- "$PROJECT_ROOT"
    "$VERIFICATION_PYTHON" --version
    node --version
    postgres --version

    "$VERIFICATION_PYTHON" -m pytest -q \
        --basetemp="$RUN_ROOT/pytest" -p no:cacheprovider 2>&1 | tee "$pytest_log"
    if grep -Eq '(^|[^0-9])[1-9][0-9]* skipped' "$pytest_log"; then
        die "Pytest reported skipped tests; release verification requires zero skips"
    fi

    node --test tests/frontend.test.mjs
    "$VERIFICATION_PYTHON" tests/verify_production.py --require-pinned-runtime
    run_browser_verification
    git diff --check
}

main() {
    clear_database_environment
    check_platform
    check_project

    RUN_ROOT=$(mktemp -d /tmp/devflow-release-verification.XXXXXX)
    : > "$RUN_ROOT/.devflow-temporary-verification"

    if ! toolchains_ready; then
        install_build_prerequisites
    fi
    install_python
    install_postgres
    install_node
    prepare_verification_environment
    start_isolated_postgres
    run_verification
}

main "$@"
