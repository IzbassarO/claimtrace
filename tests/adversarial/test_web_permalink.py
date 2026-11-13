"""The merge of origin/main into feat/web-permalink committed conflict markers into app.js:
the browser rejects the whole script, so no button works and /r/<id> permalinks never load."""

import re
from pathlib import Path

from claimtrace.web import app as web

JS = Path(web.__file__).parent / "static" / "app.js"


def test_app_js_has_no_conflict_markers_and_wires_both_features():
    js = JS.read_text()
    assert not re.search(r"^(<{7}|={7}|>{7})", js, re.M), "conflict markers: SyntaxError in browser"
    # both sides of the conflict are needed: permalink loader and the sample button
    assert "location.pathname" in js and '"click", trySample' in js and '"click", copyLink' in js
