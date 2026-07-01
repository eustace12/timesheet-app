"""Holiday cache and API fallback."""

import urllib.error

import moldova_holidays as mh
from datetime import date


def test_load_raw_falls_back_to_disk_when_api_fails(monkeypatch):
    year = 2099
    mh._raw_memory.pop(year, None)
    mh._meta_memory.pop(year, None)
    cached = {date(2099, 3, 8): "Cached Holiday"}

    monkeypatch.setattr(
        mh,
        "_fetch_from_nager",
        lambda _y: (_ for _ in ()).throw(urllib.error.URLError("offline")),
    )
    monkeypatch.setattr(mh, "_load_disk", lambda y: cached if y == year else None)

    result = mh.get_moldova_holidays(year)
    assert result == cached


def test_easter_monday_derived_from_api_payload():
    payload = [
        {
            "date": "2026-04-12",
            "name": "Easter Sunday",
            "types": ["Public"],
        }
    ]
    import json

    class FakeResp:
        def read(self):
            return json.dumps(payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    import moldova_holidays as mod

    orig_urlopen = mod.urllib.request.urlopen
    mod.urllib.request.urlopen = lambda *a, **k: FakeResp()
    try:
        holidays = mod._fetch_from_nager(2026)
    finally:
        mod.urllib.request.urlopen = orig_urlopen

    assert date(2026, 4, 12) in holidays
    assert date(2026, 4, 13) in holidays
    assert holidays[date(2026, 4, 13)] == "Orthodox Easter Monday"
