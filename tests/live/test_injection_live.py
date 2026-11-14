"""Live: one injected SciFact source through the real judge + skeptic. RUN_LIVE=1 only.
The full suite is `python -m eval.run --suite injection`."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from eval import injection, run  # noqa: E402


async def test_live_plain_injection_does_not_flip_unsupported():
    dev, corpus = run.load_scifact(run.scifact_tarball())
    cases = injection.build_cases(dev, corpus, injection.BASES[:1])  # gold: unsupported
    case = next(c for c in cases if c["variant"] == "plain")
    row = await run.check_item(case)
    assert row["verdict"] not in run.POSITIVE, row
    assert not row["quote"] or injection.verify_quote(row["quote"], case["original"])
