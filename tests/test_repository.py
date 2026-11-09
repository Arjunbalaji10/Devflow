"""Check repository exclusions without initializing Git in the project."""

from pathlib import Path
import shutil
import subprocess


def test_gitignore_excludes_local_artifacts_but_keeps_application(tmp_path):
    git = shutil.which("git")
    assert git is not None, "Git is required to verify repository ignore rules"
    source = Path(__file__).resolve().parents[1]
    shutil.copyfile(source / ".gitignore", tmp_path / ".gitignore")
    subprocess.run([git, "init", "--quiet", str(tmp_path)], check=True, capture_output=True)
    ignored = [
        "venv/Lib/module.py", ".venv/bin/python", "__pycache__/app.pyc",
        ".pytest_cache/nodeids", "devflow.db", "data/demo.sqlite3",
        "data/demo.db-wal", "data/demo.db-shm", "data/demo.sqlite-journal",
        "backups/demo.backup", ".env", ".env.production", "local.env",
        "credentials.json", "secrets.toml", "private.pem", "signing.key",
        ".aws/credentials", ".ssh/id_ed25519", "server.log",
        ".venv-linux/bin/python", "tmp/check.txt", "temp/profile/check.txt",
        "scratch.tmp", "scratch.temp", "app.py.swp", "app.py.swo", "app.py~",
        "gunicorn.pid", "gunicorn.sock", "gunicorn.ctl",
    ]
    retained = [
        "app.py", "security.py", "requirements.txt", "DEPLOYMENT.md",
        "templates/dashboard.html", "static/js/app.js", "static/css/styles.css",
        "tests/test_app.py", ".agents/skills/devflow-engineering/SKILL.md",
        "README.md", "demo/tasks.json", "tests/verify_production.py", "tests/verify_browser.mjs",
    ]
    result = subprocess.run(
        [git, "-c", "core.excludesFile=", "check-ignore", "--no-index", "-z", "--stdin"],
        cwd=tmp_path,
        input="\0".join(ignored + retained) + "\0",
        text=True,
        capture_output=True,
        check=True,
    )
    assert set(result.stdout.rstrip("\0").split("\0")) == set(ignored)
