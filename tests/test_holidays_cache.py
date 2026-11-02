"""Holiday cache and API fallback."""

import urllib.error

import moldova_holidays as mh
from datetime import date


def test_load_raw_falls_back_to_disk_when_api_fails(monkeypatch):
    year = 2026
    mh._raw_memory.pop(year, None)
    mh._meta_memory.pop(year, None)
    cached = mh.get_moldova_holidays(year)
    mh._raw_memory.pop(year, None)

    monkeypatch.setattr(
        mh,
        "_fetch_from_nager",
        lambda _y: (_ for _ in ()).throw(urllib.error.URLError("offline")),
    )
    monkeypatch.setattr(mh, "_load_disk", lambda y: cached if y == year else None)

    result = mh.get_moldova_holidays(year)
    assert result == cached


def test_easter_monday_derived_from_api_payload():
    import json
    from pathlib import Path
    payload = json.loads((Path(__file__).parent / "fixtures/holidays/2026.json").read_text())
    payload = [row for row in payload if row["date"] not in {"2026-04-12", "2026-04-13"}] + [
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


def test_cold_calendar_outage_never_becomes_an_empty_success(monkeypatch):
    import pytest
    year = 2040
    complete = {date(year, day.month, day.day): name for day, name in mh.get_moldova_holidays(2026).items()}
    mh._raw_memory.pop(year, None)
    mh._meta_memory.pop(year, None)
    monkeypatch.setattr(mh, '_fetch_from_nager', lambda _year: (_ for _ in ()).throw(urllib.error.URLError('offline')))
    monkeypatch.setattr(mh, '_load_disk', lambda _year: None)
    with pytest.raises(RuntimeError, match='holiday calendar'):
        mh.get_moldova_holidays(year)
    assert year not in mh._raw_memory
    # The same process can recover; a failed empty result must never be memoized.
    monkeypatch.setattr(mh, '_fetch_from_nager', lambda _year: complete)
    assert date(year, 1, 1) in mh.get_moldova_holidays(year)


def test_corrupt_disk_cache_fails_closed_after_upstream_outage(monkeypatch, tmp_path):
    import pytest
    year = 2041
    mh._raw_memory.pop(year, None)
    mh._meta_memory.pop(year, None)
    monkeypatch.setattr(mh, '_cache_dir', lambda: tmp_path)
    (tmp_path / f'{year}.json').write_text('{broken json')
    monkeypatch.setattr(mh, '_fetch_from_nager', lambda _year: (_ for _ in ()).throw(urllib.error.URLError('offline')))
    with pytest.raises(RuntimeError, match='holiday calendar'):
        mh.get_moldova_holidays(year)


def test_calendar_outage_prevents_preview_and_document_generation(client, monkeypatch):
    import pytest
    year = 2042
    mh._raw_memory.pop(year, None)
    mh._meta_memory.pop(year, None)
    monkeypatch.setattr(mh, '_fetch_from_nager', lambda _year: (_ for _ in ()).throw(urllib.error.URLError('offline')))
    monkeypatch.setattr(mh, '_load_disk', lambda _year: None)
    for path in ['/preview', '/generate', '/timesheets/submit']:
        response = client.post(path, data={'year':str(year), 'month':'11', 'format':'pdf', 'employee_name':'Synthetic Employee'})
        assert response.status_code == 503, path
        assert 'holiday calendar' in response.get_json()['error'].lower()


def test_partial_or_non_public_calendar_does_not_pass_as_a_complete_year(monkeypatch):
    import io
    import json
    import pytest
    year = 2026
    monkeypatch.setattr(mh, '_load_disk', lambda _year: None)
    for payload in [
        [{'date':'2026-01-01', 'name':'Observance', 'types':['Optional']}],
        [{'date':'2026-01-01', 'name':'New Year', 'types':['Public']}],
    ]:
        mh._raw_memory.pop(year, None)
        monkeypatch.setattr(mh.urllib.request, 'urlopen', lambda *a, **k: io.BytesIO(json.dumps(payload).encode()))
        with pytest.raises(RuntimeError, match='holiday calendar'):
            mh.get_moldova_holidays(year)


def test_partial_disk_cache_is_not_used_for_accounting(monkeypatch, tmp_path):
    import pytest
    year = 2026
    mh._raw_memory.pop(year, None)
    monkeypatch.setattr(mh, '_cache_dir', lambda: tmp_path)
    (tmp_path / f'{year}.json').write_text('{"2026-06-01":"Partial cache"}')
    monkeypatch.setattr(mh, '_fetch_from_nager', lambda _year: (_ for _ in ()).throw(urllib.error.URLError('offline')))
    with pytest.raises(RuntimeError, match='holiday calendar'):
        mh.get_moldova_holidays(year)


def test_malformed_upstream_name_uses_valid_disk_cache(monkeypatch):
    import io
    import json
    from pathlib import Path
    year = 2026
    valid = mh.get_moldova_holidays(year)
    mh._raw_memory.clear()
    payload = json.loads((Path(__file__).parent / 'fixtures/holidays/2026.json').read_text())
    payload[0]['name'] = 123
    monkeypatch.setattr(mh.urllib.request, 'urlopen', lambda *a, **k: io.BytesIO(json.dumps(payload).encode()))
    assert mh.get_moldova_holidays(year) == valid
