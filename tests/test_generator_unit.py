"""Unit tests for timesheet generator logic."""

from datetime import date

from generate_timesheet import PDFTimesheetGenerator, build_timesheet_filename


def test_build_timesheet_filename():
    name = build_timesheet_filename("Jane Doe", 2026, 6, "pdf")
    assert name == "Timesheet_Jane_Doe_2026_06_June.pdf"


def test_get_all_days_counts_working_and_absences():
    gen = PDFTimesheetGenerator(
        year=2026,
        month=6,
        employee_name="Test",
        vacation_dates=[(date(2026, 6, 2), date(2026, 6, 2))],
        sick_dates=[(date(2026, 6, 10), date(2026, 6, 10))],
        custom_absences={date(2026, 6, 15): "Remote"},
    )
    working, hours, days = gen.get_all_days()

    by_day = {d["date"].day: d for d in days}
    assert by_day[2]["absence_type"] == "vacation"
    assert by_day[10]["absence_type"] == "sick"
    assert by_day[15]["absence_type"] == "custom"
    assert by_day[15]["absence_reason"] == "Remote"
    assert hours == working * 8
    assert working < 30
