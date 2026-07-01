"""
Moldova public holidays via Nager.Date API (https://date.nager.at).
Caches per year on disk; in-memory cache for the running process.
"""

from __future__ import annotations

import json
import os
import ssl
import unicodedata
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import certifi

NAGER_URL = "https://date.nager.at/api/v3/PublicHolidays/{year}/MD"
HOLIDAY_SOURCE = "Nager.Date"
HolidayScope = Literal["national"]
HOLIDAY_SCOPE_LABEL = "National + Chișinău"

# Law 170/2023 — often missing from third-party feeds
SUPPLEMENTAL_FIXED = [
    (6, 1, "Children's Day"),
]

# Chișinău municipal — kept with national holidays
CHISINAU_KEYWORDS = (
    "capital's day",
    "chisinau",
    "kishinev",
)

# Other regional / professional days — excluded
REGIONAL_KEYWORDS = (
    "balti",
    "cahul",
    "veterans",
    "national army",
    "armed forces",
    "law enforcement",
)

_raw_memory: dict[int, dict[date, str]] = {}
_meta_memory: dict[int, dict] = {}


def _cache_dir() -> Path:
    if os.environ.get("VERCEL"):
        return Path("/tmp/holidays_cache")
    return Path(__file__).parent / ".cache" / "holidays"


def _cache_file(year: int) -> Path:
    return _cache_dir() / f"{year}.json"


def _meta_file(year: int) -> Path:
    return _cache_dir() / f"{year}.meta.json"


def _normalize(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text)
    return folded.encode("ascii", "ignore").decode().lower()


def _is_chisinau(name: str) -> bool:
    lower = _normalize(name)
    if "cahul" in lower or "balti" in lower:
        return False
    return any(keyword in lower for keyword in CHISINAU_KEYWORDS)


def _is_regional(name: str) -> bool:
    if _is_chisinau(name):
        return False
    lower = _normalize(name)
    return any(keyword in lower for keyword in REGIONAL_KEYWORDS)


def filter_holidays(holidays: dict[date, str], scope: HolidayScope = "national") -> dict[date, str]:
    return {d: n for d, n in holidays.items() if not _is_regional(n)}


def _load_disk(year: int) -> dict[date, str] | None:
    path = _cache_file(year)
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {date.fromisoformat(k): v for k, v in raw.items()}


def _load_meta(year: int) -> dict:
    path = _meta_file(year)
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_disk(year: int, holidays: dict[date, str], *, from_api: bool) -> None:
    path = _cache_file(year)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {d.isoformat(): n for d, n in sorted(holidays.items())}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    meta = {
        "fetched_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": HOLIDAY_SOURCE,
        "from_api": from_api,
        "count": len(holidays),
    }
    _meta_file(year).write_text(json.dumps(meta, indent=2), encoding="utf-8")
    _meta_memory[year] = meta


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


def _load_raw(year: int) -> dict[date, str]:
    if year in _raw_memory:
        return _raw_memory[year]

    holidays: dict[date, str]
    from_api = False
    try:
        holidays = _fetch_from_nager(year)
        _save_disk(year, holidays, from_api=True)
        from_api = True
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, OSError):
        cached = _load_disk(year)
        holidays = cached if cached is not None else {}
        if cached is not None:
            _meta_memory[year] = _load_meta(year)

    if year not in _meta_memory:
        _meta_memory[year] = _load_meta(year) or {
            "source": HOLIDAY_SOURCE,
            "from_api": from_api,
            "count": len(holidays),
        }

    _raw_memory[year] = holidays
    return holidays


def get_moldova_holidays(year: int, scope: HolidayScope = "national") -> dict[date, str]:
    """Return {date: holiday_name} for the given year and scope."""
    raw = _load_raw(year)
    return filter_holidays(raw, scope)


def holidays_meta(year: int) -> dict:
    _load_raw(year)
    return _meta_memory.get(year, {})


def holidays_for_api(year: int, scope: HolidayScope = "national") -> list[dict]:
    holidays = get_moldova_holidays(year, scope)
    return [
        {
            "date": d.isoformat(),
            "day": d.day,
            "month": d.month,
            "name": name,
        }
        for d, name in sorted(holidays.items())
    ]


def warmup_holiday_cache(years: list[int] | None = None) -> None:
    """Pre-fetch holidays for current and next year (serverless cold start)."""
    if years is None:
        y = datetime.now().year
        years = [y, y + 1]
    for year in years:
        try:
            _load_raw(year)
        except Exception:
            pass
