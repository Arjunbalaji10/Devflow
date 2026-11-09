from pathlib import Path
import shutil
import subprocess


def test_frontend_behavior():
    """Execute real frontend modules without installing a browser or npm packages."""
    node = shutil.which("node")
    assert node is not None, "Node.js is required to run frontend behavior tests"
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [node, "--test", "tests/frontend.test.mjs"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
