"""Filter regional holidays; keep national + Chișinău."""

from datetime import date

from moldova_holidays import filter_holidays


def test_filter_national_removes_balti():
    holidays = {
        date(2026, 5, 22): "Bălţi Day",
        date(2026, 5, 1): "Labour Day",
    }
    filtered = filter_holidays(holidays, "national")
    assert date(2026, 5, 1) in filtered
    assert date(2026, 5, 22) not in filtered


def test_filter_keeps_chisinau_capital_day():
    holidays = {
        date(2026, 10, 14): "Capital's Day",
        date(2026, 11, 21): "South Capital's Day Cahul",
        date(2026, 5, 22): "Bălţi Day",
    }
    filtered = filter_holidays(holidays, "national")
    assert date(2026, 10, 14) in filtered
    assert date(2026, 11, 21) not in filtered
    assert date(2026, 5, 22) not in filtered
