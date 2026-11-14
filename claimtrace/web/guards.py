"""Deployment guards: per-UTC-day job counter by country (no IPs stored) and offline IP->country.

Usage store: CACHE_DIR/usage/usage.json = {"YYYY-MM-DD": {"KZ": 3, "LOCAL": 1}}. The day's job
count is the sum of its values. Country DB: db-ip.com "IP to Country Lite" CSV (CC BY 4.0),
path from env GEOIP_CSV."""

import csv
import gzip
import ipaddress
import json
import logging
import os
import socket
import sys
import threading
from bisect import bisect_right
from datetime import UTC, datetime, timedelta
from pathlib import Path

from claimtrace.core.settings import get_settings

log = logging.getLogger("claimtrace.web")
GEOIP_DEFAULT = "/opt/geo/dbip-country-lite.csv.gz"


def _store() -> Path:
    return Path(get_settings().cache_dir) / "usage" / "usage.json"


def today() -> str:
    return datetime.now(UTC).date().isoformat()


def load() -> dict[str, dict[str, int]]:
    try:
        return json.loads(_store().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:  # ponytail: a corrupt file resets the counts (fails open)
        log.warning("cannot read usage store: %s", type(e).__name__)
        return {}


def jobs_today() -> int:
    return sum(load().get(today(), {}).values())


def record(country: str) -> None:
    """Count one accepted job. Sync read-modify-write: no await in between, single process."""
    data = load()
    day = data.setdefault(today(), {})
    day[country] = day.get(country, 0) + 1
    path = _store()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def until_reset() -> str:
    now = datetime.now(UTC)
    midnight = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), UTC)
    mins = int((midnight - now).total_seconds()) // 60
    return f"{mins // 60} h {mins % 60:02d} min"


def summary(days: int = 30) -> dict:
    data = load()
    start = datetime.now(UTC).date()
    out_days, countries = [], {}
    for i in range(days):
        d = (start - timedelta(days=i)).isoformat()
        counts = data.get(d, {})
        out_days.append({"date": d, "total": sum(counts.values()), "countries": counts})
        for cc, n in counts.items():
            countries[cc] = countries.get(cc, 0) + n
    return {
        "days": out_days,
        "countries": dict(sorted(countries.items(), key=lambda kv: -kv[1])),
        "total": sum(countries.values()),
    }


# --- IP -> country (offline) -------------------------------------------------------------


def _ip_int(s: str) -> tuple[int, int]:
    """(family, int) without ipaddress objects: faster for the ~730k-row CSV."""
    fam = socket.AF_INET6 if ":" in s else socket.AF_INET
    return fam, int.from_bytes(socket.inet_pton(fam, s))


_lock = threading.Lock()
_tables: dict[str, dict | None] = {}


def _table(path: str) -> dict | None:
    """{family: (starts, ends, countries)} sorted by start, or None if the file is missing."""
    with _lock:
        if path not in _tables:
            _tables[path] = _read(path)
        return _tables[path]


def _read(path: str) -> dict | None:
    out = {socket.AF_INET: ([], [], []), socket.AF_INET6: ([], [], [])}
    opener = gzip.open if path.endswith(".gz") else open
    try:
        with opener(path, "rt", encoding="ascii", newline="") as f:
            for start, end, cc in csv.reader(f):
                fam, lo = _ip_int(start)
                starts, ends, ccs = out[fam]
                starts.append(lo)
                ends.append(_ip_int(end)[1])
                ccs.append(sys.intern(cc))
    except FileNotFoundError:
        log.warning("GEOIP_CSV not found; countries will be '??'")
        return None
    except (OSError, ValueError) as e:
        log.warning("cannot read GEOIP_CSV: %s", type(e).__name__)
        return None
    # ponytail: plain int lists (~100 MB RSS, ~0.8 s for the full DB); numpy if RAM gets tight
    for fam, (starts, ends, ccs) in out.items():
        if starts != sorted(starts):  # db-ip ships sorted; anything else gets sorted once
            order = sorted(range(len(starts)), key=starts.__getitem__)
            out[fam] = tuple([col[i] for i in order] for col in (starts, ends, ccs))
    return out


def country(ip: str) -> str:
    """ISO country code, "LOCAL" for private/loopback, "??" if unknown. Never logs the IP."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return "??"
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    if addr.is_private or addr.is_loopback or addr.is_link_local:
        return "LOCAL"
    table = _table(os.environ.get("GEOIP_CSV", GEOIP_DEFAULT))
    if table is None:
        return "??"
    starts, ends, ccs = table[socket.AF_INET6 if addr.version == 6 else socket.AF_INET]
    i = bisect_right(starts, int(addr)) - 1
    if i < 0 or int(addr) > ends[i] or ccs[i] == "ZZ":
        return "??"
    return ccs[i]
