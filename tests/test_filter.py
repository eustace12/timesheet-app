"""Filter regional holidays from national set."""

from moldova_holidays import filter_holidays
from datetime import date


def test_filter_national_removes_balti():
    holidays = {
        date(2026, 5, 22): "Bălţi Day",
        date(2026, 5, 1): "Labour Day",
    }
    filtered = filter_holidays(holidays, "national")
    assert date(2026, 5, 1) in filtered
    assert date(2026, 5, 22) not in filtered


def test_filter_all_keeps_regional():
    holidays = {
        date(2026, 5, 22): "Bălţi Day",
        date(2026, 5, 1): "Labour Day",
    }
    filtered = filter_holidays(holidays, "all")
    assert len(filtered) == 2
