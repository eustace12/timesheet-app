"""Holiday logic tests."""

from datetime import date

import pytest

from moldova_holidays import (
    filter_holidays,
    get_moldova_holidays,
    holidays_for_api,
    warmup_holiday_cache,
)


@pytest.fixture(scope="module", autouse=True)
def _warm_cache():
    warmup_holiday_cache([2025, 2026, 2027])


def test_holidays_2026_has_easter_and_children_day():
    h = get_moldova_holidays(2026, "national")
    assert date(2026, 4, 12) in h
    assert date(2026, 4, 13) in h  # Easter Monday derived
    assert date(2026, 6, 1) in h


def test_national_scope_excludes_regional():
    all_h = get_moldova_holidays(2026, "all")
    nat_h = get_moldova_holidays(2026, "national")
    assert len(all_h) >= len(nat_h)
    regional = [n for n in all_h.values() if "bălți" in n.lower() or "balti" in n.lower()]
    if regional:
        assert not any("bălți" in n.lower() or "balti" in n.lower() for n in nat_h.values())


def test_april_2026_working_days_via_preview(client):
    r = client.post("/preview", data={"year": "2026", "month": "4", "hours_per_day": "8", "holiday_scope": "national"})
    assert r.status_code == 200
    data = r.get_json()
    assert data["summary"]["working_days"] == 21


def test_june_2026_working_days_with_children_day(client):
    r = client.post("/preview", data={"year": "2026", "month": "6", "hours_per_day": "8", "holiday_scope": "national"})
    data = r.get_json()
    assert data["summary"]["working_days"] == 21


def test_holidays_api_format(client):
    r = client.get("/holidays/2026?scope=national")
    assert r.status_code == 200
    data = r.get_json()
    assert data["source"] == "Nager.Date"
    assert data["scope"] == "national"
    assert len(data["holidays"]) > 0
    assert "fetched_at" in data


def test_health_endpoint(client):
    r = client.get("/health")
    assert r.status_code == 200
    data = r.get_json()
    assert data["status"] in ("ok", "degraded")
    assert "holidays_loaded" in data
