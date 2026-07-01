"""API edge cases: download, validation, custom absences."""

from datetime import date
from io import BytesIO

import pytest
from openpyxl import load_workbook

from generate_timesheet import build_timesheet_filename
from web_app import OUTPUT_DIR


def test_index_renders_form(client):
    html = client.get("/").data.decode()
    assert 'id="employee_name"' in html
    assert 'rel="icon"' in html


def test_favicon_endpoints(client):
    for path in (
        "/favicon.ico",
        "/static/favicon.ico",
        "/static/favicon-32x32.png",
        "/static/apple-touch-icon.png",
    ):
        r = client.get(path)
        assert r.status_code == 200, path
        assert r.data


def test_download_existing_file(client, form_base):
    client.post("/generate", data={**form_base, "format": "pdf"})
    filename = build_timesheet_filename("Test User", 2026, 6, "pdf")
    assert (OUTPUT_DIR / filename).exists()
    r = client.get(f"/download/{filename}")
    assert r.status_code == 200
    assert r.data[:4] == b"%PDF"


def test_download_missing_returns_404(client):
    r = client.get("/download/does-not-exist.pdf")
    assert r.status_code == 404


def test_generate_invalid_format(client, form_base):
    r = client.post("/generate", data={**form_base, "format": "doc"})
    assert r.status_code == 400
    assert "error" in r.get_json()


def test_preview_invalid_month_returns_error(client, form_base):
    r = client.post("/preview", data={**form_base, "month": "xx"})
    assert r.status_code == 500
    assert "error" in r.get_json()


def test_preview_custom_absence(client, form_base):
    r = client.post(
        "/preview",
        data={**form_base, "custom_days": "15", "custom_reason": "Personal day"},
    )
    assert r.status_code == 200
    day15 = next(d for d in r.get_json()["days"] if d["day"] == 15)
    assert day15["status"] == "custom"
    assert day15["custom_reason"] == "Personal day"
    assert day15["hours"] == 0


def test_preview_vacation_overrides_holiday(client, form_base):
    # 2026-06-01 is Children's Day (public holiday)
    r = client.post("/preview", data={**form_base, "month": "6", "vacation": "1"})
    day1 = next(d for d in r.get_json()["days"] if d["day"] == 1)
    assert day1["status"] == "vacation"


def test_generate_with_custom_absence(client, form_base):
    r = client.post(
        "/generate",
        data={
            **form_base,
            "format": "xlsx",
            "custom_days": "15",
            "custom_reason": "Training",
        },
    )
    assert r.status_code == 200
    wb = load_workbook(BytesIO(r.data))
    values = [c.value for row in wb.active.iter_rows() for c in row if c.value]
    assert "Training" in values


def test_xlsx_contains_summary_fields(client, form_base):
    r = client.post("/generate", data={**form_base, "format": "xlsx"})
    wb = load_workbook(BytesIO(r.data))
    ws = wb.active
    flat = [c.value for row in ws.iter_rows() for c in row if c.value]
    assert "MONTHLY TIMESHEET" in flat
    assert "Test User" in flat
    assert "Urchin Systems SA" in flat
    assert "DATE" in flat
    assert ws.max_row >= 10
