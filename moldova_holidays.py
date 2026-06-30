"""
Moldova public holidays via Nager.Date API (https://date.nager.at).
Caches per year on disk; in-memory cache for the running process.
"""

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

import certifi

NAGER_URL = "https://date.nager.at/api/v3/PublicHolidays/{year}/MD"
HOLIDAY_SOURCE = "Nager.Date"

# Law 170/2023 — often missing from third-party feeds
SUPPLEMENTAL_FIXED = [
    (6, 1, "Children's Day"),
]

_memory: dict[int, dict[date, str]] = {}


def _cache_dir() -> Path:
    if os.environ.get("VERCEL"):
        return Path("/tmp/holidays_cache")
    return Path(__file__).parent / ".cache" / "holidays"


def _cache_file(year: int) -> Path:
    return _cache_dir() / f"{year}.json"


def _load_disk(year: int) -> dict[date, str] | None:
    path = _cache_file(year)
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {date.fromisoformat(k): v for k, v in raw.items()}


def _save_disk(year: int, holidays: dict[date, str]) -> None:
    path = _cache_file(year)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {d.isoformat(): n for d, n in sorted(holidays.items())}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _fetch_from_nager(year: int) -> dict[date, str]:
    url = NAGER_URL.format(year=year)
    req = urllib.request.Request(url, headers={"User-Agent": "urchinsys-timesheet/1.0"})
    ctx = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(req, timeout=12, context=ctx) as resp:
        items = json.loads(resp.read().decode("utf-8"))

    holidays: dict[date, str] = {}
    for item in items:
        types = item.get("types") or []
        if "Public" not in types:
            continue
        d = date.fromisoformat(item["date"])
        name = (item.get("name") or item.get("localName") or "Public Holiday").strip()
        holidays[d] = name

    # Moldova: Easter Monday is off; Nager often lists only Easter Sunday
    for d, name in list(holidays.items()):
        lower = name.lower()
        if "easter" in lower and "monday" not in lower:
            monday = d + timedelta(days=1)
            if monday.year == year and monday not in holidays:
                holidays[monday] = "Orthodox Easter Monday"

    for month, day, name in SUPPLEMENTAL_FIXED:
        d = date(year, month, day)
        holidays.setdefault(d, name)

    return holidays


def get_moldova_holidays(year: int) -> dict[date, str]:
    """Return {date: holiday_name} for the given year."""
    if year in _memory:
        return _memory[year]

    holidays: dict[date, str]
    try:
        holidays = _fetch_from_nager(year)
        _save_disk(year, holidays)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, OSError):
        cached = _load_disk(year)
        holidays = cached if cached is not None else {}

    _memory[year] = holidays
    return holidays


def holidays_for_api(year: int) -> list[dict]:
    """JSON-serializable list for GET /holidays/<year>."""
    holidays = get_moldova_holidays(year)
    return [
        {
            "date": d.isoformat(),
            "day": d.day,
            "month": d.month,
            "name": name,
        }
        for d, name in sorted(holidays.items())
    ]
