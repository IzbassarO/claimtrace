"""Repo hygiene: a merge must never ship conflict markers; the UI script must parse."""

import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKER = re.compile(r"^(<{7} |={7}$|>{7} )", re.M)


def test_no_conflict_markers_in_tracked_files():
    files = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    bad = [
        f
        for f in files
        if f.endswith((".py", ".js", ".html", ".css", ".md", ".yml", ".toml", ".txt"))
        and MARKER.search((ROOT / f).read_text(errors="replace"))
    ]
    assert not bad, f"conflict markers in {bad}"


def test_app_js_parses():
    node = shutil.which("node")
    if node is None:
        return  # ponytail: syntax check only where node exists (dev machines, GH runners)
    js = ROOT / "claimtrace/web/static/app.js"
    r = subprocess.run([node, "--check", str(js)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
