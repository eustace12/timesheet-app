"""Zoho-style time logging, approvals, and leave accounting."""
from io import BytesIO

from openpyxl import load_workbook
import pytest
import db
from web_app import app


@pytest.fixture(autouse=True)
def real_requests(client, monkeypatch):
    monkeypatch.setitem(app.config, "TESTING", False)
    client.get("/")
    with client.session_transaction() as session:
        session["user_id"] = db.get_user_by_username("employee")["id"]


def post(client, path, data):
    with client.session_transaction() as session:
        token = session["_csrf_token"]
    return client.post(path, data={**data, "_csrf_token": token})


def log(client, hours="7.5", day="2026-06-02"):
    return post(client, "/time-logs", {"work_date": day, "project": "Platform", "task": "Deployment", "hours": hours, "billable": "1"})


def test_home_opens_overview_without_daily_time_tracking(client):
    home = client.get("/")
    assert home.status_code == 200
    assert b'Your workspace' in home.data
    assert b'id="timesheetForm"' in client.get('/timesheet').data
    assert b'href="/time-logs"' not in home.data
    assert client.get("/time-logs").status_code == 302
    assert client.get("/time-logs").headers["Location"] == "/documents"
    assert client.get("/team-calendar").status_code == 200
    assert client.get("/leave-balances").status_code == 200


def test_logged_hours_are_used_in_excel_and_submission(client):
    created = log(client)
    assert created.status_code == 200
    exported = post(client, "/generate", {"year": "2026", "month": "6", "format": "xlsx", "source": "time_logs"})
    assert exported.status_code == 200
    values = [cell.value for row in load_workbook(BytesIO(exported.data)).active for cell in row]
    assert 7.5 in values
    submitted = post(client, "/timesheets/submit", {"year": "2026", "month": "6", "format": "pdf", "source": "time_logs"})
    assert submitted.status_code == 200
    user_id = db.get_user_by_username("employee")["id"]
    sheet = db.get_timesheet_for_period(user_id, 2026, 6)
    assert sheet["total_hours"] == 7.5


def test_day_total_cannot_exceed_twenty_four_hours(client):
    assert log(client, "20").status_code == 200
    assert log(client, "5").status_code == 400
    from hr_store import entries
    user_id = db.get_user_by_username("employee")["id"]
    assert sum(row["minutes"] for row in entries(user_id, 2026, 6)) == 1200


@pytest.mark.parametrize("hours", ["-1", "25", "NaN", "Infinity", "7:99", "0", "0.001"])
def test_invalid_hours_are_rejected(client, hours):
    assert log(client, hours).status_code == 400


def test_approved_month_locks_time_logs(client):
    assert log(client).status_code == 200
    assert post(client, "/timesheets/submit", {"year": "2026", "month": "6", "format": "pdf", "source": "time_logs"}).status_code == 200
    user_id = db.get_user_by_username("employee")["id"]
    sheet = db.get_timesheet_for_period(user_id, 2026, 6)
    db.review_timesheet(sheet["id"], "accepted", "Approved")
    assert log(client, "1").status_code == 409


def test_leave_balance_separates_used_and_pending_working_days(client):
    user_id = db.get_user_by_username("employee")["id"]
    assert post(client, "/leave-balances", {"year": "2026", "days": "20"}).status_code == 302
    for start, end, status in [("2026-06-02", "2026-06-05", "accepted"), ("2026-06-08", "2026-06-09", "submitted")]:
        row = db.create_leave_request(user_id=user_id, start_date=start, end_date=end, leave_type="Vacation", comment="", filename="test.pdf")
        if status == "accepted": db.review_leave_request(row["id"], status, "")
    response = client.get("/leave-balances?year=2026")
    assert response.status_code == 200
    from hr_store import leave_balance
    balance = leave_balance(user_id, 2026)
    assert balance == {"entitlement": 20, "used": 4, "pending": 2, "available": 14}


def test_csv_export_is_spreadsheet_safe(client):
    assert post(client, "/time-logs", {"work_date": "2026-06-02", "project": "=1+1", "task": "@malicious", "hours": "1"}).status_code == 200
    exported = client.get("/time-logs/export?year=2026&month=6")
    assert exported.status_code == 200
    assert b"'=1+1" in exported.data and b"'@malicious" in exported.data


def test_locked_entry_cannot_be_moved_into_another_month(client):
    created = log(client).get_json()
    assert post(client, "/timesheets/submit", {"year": "2026", "month": "6", "format": "pdf", "source": "time_logs"}).status_code == 200
    response = post(client, f"/time-logs/{created['id']}/edit", {"work_date": "2026-07-02", "project": "Platform", "task": "Moved", "hours": "1"})
    assert response.status_code == 409
    assert post(client, f"/time-logs/{created['id']}/delete", {}).status_code == 409


def test_time_log_ownership_follows_selected_person(client):
    created = log(client).get_json()
    with client.session_transaction() as session:
        session["user_id"] = db.get_user_by_username("stanislav")["id"]
    response = post(client, f"/time-logs/{created['id']}/delete", {})
    assert response.status_code == 404


def test_entitlement_is_not_invented_for_new_employee(client):
    from hr_store import leave_balance
    assert leave_balance(db.get_user_by_username("employee")["id"], 2026)["entitlement"] is None
