"""Build eval/data/refs_seed.jsonl from live Crossref (+ OpenAlex retraction flag) lookups.

Run once: `uv run python -m eval.make_refs`. The output is committed; eval.run never calls this.
Metadata comes verbatim from Crossref, so every seed row is a real, correctly cited reference.
"""

import asyncio
import json
import re
import sys
from pathlib import Path

from claimtrace.core.http import make_client, request

OUT = Path(__file__).parent / "data" / "refs_seed.jsonl"

# Well-known papers across fields (CS, biology, medicine, physics, statistics).
DOIS = [
    "10.1038/nature14539",
    "10.1109/CVPR.2016.90",
    "10.1038/nature16961",
    "10.1038/s41586-021-03819-2",
    "10.1126/science.1127647",
    "10.1162/neco.1997.9.8.1735",
    "10.1038/323533a0",
    "10.1145/3065386",
    "10.1109/5.726791",
    "10.1038/nature14236",
    "10.1023/A:1010933404324",
    "10.1007/BF00994018",
    "10.18653/v1/N19-1423",
    "10.1093/bioinformatics/btp324",
    "10.1093/bioinformatics/btp352",
    "10.1038/nmeth.1923",
    "10.1186/s13059-014-0550-8",
    "10.1126/science.1225829",
    "10.1016/j.cell.2006.07.024",
    "10.1038/171737a0",
    "10.1056/NEJMoa2034577",
    "10.1056/NEJMoa2001017",
    "10.1016/S0140-6736(20)30183-5",
    "10.1038/s41586-020-2012-7",
    "10.1371/journal.pmed.0020124",
    "10.1136/bmj.n71",
    "10.1093/nar/25.17.3389",
    "10.1016/S0022-2836(05)80360-2",
    "10.1038/nature11247",
    "10.1038/35057062",
    "10.1103/PhysRevLett.116.061102",
    "10.1103/PhysRev.47.777",
    "10.1126/science.aac4716",
    "10.1038/s41586-019-1666-5",
    "10.1037/0033-295X.84.2.191",
    "10.1080/01621459.1958.10501452",
    "10.1111/j.2517-6161.1995.tb02031.x",
    "10.1093/biomet/52.3-4.591",
]

# Real papers that were retracted. Kept only if Crossref or OpenAlex flags the retraction.
RETRACTED_DOIS = [
    "10.1016/S0140-6736(97)11096-0",  # Wakefield 1998
    "10.1016/S0140-6736(20)31180-6",  # Surgisphere hydroxychloroquine, Lancet 2020
    "10.1056/NEJMoa2007621",  # Surgisphere, NEJM 2020
    "10.1038/nature12968",  # STAP cells 2014
    "10.1126/science.1094515",  # Hwang cloned blastocyst 2004
    "10.1126/science.1256151",  # LaCour & Green 2014
]

MAX_AUTHORS = 10


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s)).strip()


def parse_crossref(msg: dict) -> dict:
    authors = []
    for a in msg.get("author", [])[:MAX_AUTHORS]:
        if a.get("family"):
            authors.append(clean(f"{a['family']}, {a.get('given', '')}").rstrip(", "))
        elif a.get("name"):
            authors.append(clean(a["name"]))
    venue = msg.get("container-title") or [None]
    return {
        "doi": msg["DOI"].lower(),
        # a citing manuscript uses the original title, not the "RETRACTED:" banner
        "title": re.sub(r"^RETRACTED( ARTICLE)?:\s*", "", clean(msg["title"][0]), flags=re.I),
        "authors": authors,
        "year": msg["issued"]["date-parts"][0][0],
        "venue": clean(venue[0]) if venue[0] else None,
    }


def crossref_retracted(msg: dict) -> bool:
    return any(u.get("type") == "retraction" for u in msg.get("updated-by", []))


async def fetch(client, doi: str, want_retracted: bool) -> dict | None:
    r = await request(client, "GET", f"https://api.crossref.org/works/{doi}")
    if r.status_code != 200:
        print(f"skip {doi}: crossref {r.status_code}", file=sys.stderr)
        return None
    msg = r.json()["message"]
    row = parse_crossref(msg)
    retracted = crossref_retracted(msg)
    if not retracted:
        o = await request(client, "GET", f"https://api.openalex.org/works/doi:{doi}")
        retracted = o.status_code == 200 and bool(o.json().get("is_retracted"))
    if retracted != want_retracted:
        print(f"skip {doi}: retracted={retracted}, expected {want_retracted}", file=sys.stderr)
        return None
    row["retracted"] = retracted
    return row


async def main() -> None:
    async with make_client() as client:
        sem = asyncio.Semaphore(4)

        async def one(doi, want):
            async with sem:
                return await fetch(client, doi, want)

        rows = await asyncio.gather(
            *[one(d, False) for d in DOIS], *[one(d, True) for d in RETRACTED_DOIS]
        )
    rows = [r for r in rows if r]
    OUT.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    n_ret = sum(r["retracted"] for r in rows)
    print(f"wrote {len(rows)} rows ({n_ret} retracted) to {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
