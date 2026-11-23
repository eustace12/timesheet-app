"""Shared employee and administrator document workflow tests."""

from pathlib import Path

import db
from web_app import OUTPUT_DIR, app


def _select_person(client, username):
    client.get("/")
    with client.session_transaction() as session:
        csrf_token = session["_csrf_token"]
    return client.post(
        "/profile",
        data={"user_id": db.get_user_by_username(username)["id"], "_csrf_token": csrf_token},
        follow_redirects=False,
    )


def _post(client, path, data):
    with client.session_transaction() as session:
        csrf_token = session["_csrf_token"]
    return client.post(path, data={**data, "_csrf_token": csrf_token})


def test_employee_submission_can_be_reviewed_and_viewed_by_admin(client):
    app.config["TESTING"] = False
    try:
        assert _select_person(client, "employee").status_code == 302
        submitted = _post(
            client,
            "/timesheets/submit",
            {"year": "2030", "month": "4", "format": "pdf"},
        )
        assert submitted.status_code == 200
        timesheet = db.get_timesheet_for_period(
            db.get_user_by_username("employee")["id"], 2030, 4
        )

        assert _select_person(client, "admin").status_code == 302
        inbox = client.get("/admin?year=2030&month=4")
        assert inbox.status_code == 200
        assert b"Test User" in inbox.data

        viewed = client.get(f"/admin/timesheets/{timesheet['id']}/view")
        assert viewed.status_code == 200
        assert viewed.data[:4] == b"%PDF"
        assert "attachment" not in viewed.headers.get("Content-Disposition", "")

        reviewed = _post(
            client,
            f"/admin/timesheets/{timesheet['id']}/review",
            {"status": "accepted", "review_comment": "Approved", "year": "2030", "month": "4"},
        )
        assert reviewed.status_code == 302
        assert db.get_timesheet(timesheet["id"])["status"] == "accepted"

        assert _select_person(client, "employee").status_code == 302
        resubmitted = _post(
            client,
            "/timesheets/submit",
            {"year": "2030", "month": "4", "format": "pdf"},
        )
        assert resubmitted.status_code == 409
        assert db.get_timesheet(timesheet["id"])["status"] == "accepted"
    finally:
        app.config["TESTING"] = True


def test_selected_employee_can_open_admin_pages(client):
    app.config["TESTING"] = False
    try:
        assert _select_person(client, "employee").status_code == 302
        assert client.get("/admin").status_code == 200
        assert client.get("/admin/users").status_code == 200
    finally:
        app.config["TESTING"] = True


def test_csrf_token_is_required_for_profile_selection_and_submissions(client):
    app.config["TESTING"] = False
    try:
        assert client.post(
            "/profile", data={"user_id": db.get_user_by_username("employee")["id"]}
        ).status_code == 400
        assert _select_person(client, "employee").status_code == 302
        assert client.post(
            "/timesheets/submit",
            data={"year": "2030", "month": "4", "format": "pdf"},
        ).status_code == 400
    finally:
        app.config["TESTING"] = True


def test_admin_inbox_limits_decided_requests_to_the_selected_month(client):
    app.config["TESTING"] = False
    try:
        assert _select_person(client, "employee").status_code == 302
        for start_date, end_date in (("2030-04-08", "2030-04-12"), ("2030-05-06", "2030-05-10")):
            submitted = _post(
                client,
                "/leave/submit",
                {
                    "start_date": start_date,
                    "end_date": end_date,
                    "leave_type": "Vacation",
                    "comment": "",
                },
            )
            assert submitted.status_code == 200
        may = next(row for row in db.list_user_leave_requests(db.get_user_by_username("employee")["id"])
                   if row["start_date"] == "2030-05-06")

        assert _select_person(client, "admin").status_code == 302
        assert _post(client, f"/admin/leave/{may['id']}/review",
                     {"status": "accepted", "review_comment": "", "year": "2030", "month": "5"}).status_code == 302
        inbox = client.get("/admin?year=2030&month=4")
        assert b"2030-04-08" in inbox.data
        assert b"2030-05-06" not in inbox.data
    finally:
        app.config["TESTING"] = True


def test_admin_inbox_keeps_pending_requests_from_other_months(client):
    app.config["TESTING"] = False
    try:
        assert _select_person(client, "employee").status_code == 302
        assert _post(client, "/leave/submit", {"start_date": "2030-05-06", "end_date": "2030-05-10",
                                               "leave_type": "Vacation", "comment": ""}).status_code == 200
        assert _post(client, "/timesheets/submit",
                     {"year": "2030", "month": "3", "format": "pdf"}).status_code == 200

        assert _select_person(client, "admin").status_code == 302
        inbox = client.get("/admin?year=2030&month=4").get_data(as_text=True)
        assert "2030-05-06" in inbox
        assert "March 2030" in inbox
        assert 'Awaiting review <span>2</span>' in inbox
    finally:
        app.config["TESTING"] = True


def test_leave_request_can_be_viewed_and_reviewed_by_admin(client):
    app.config["TESTING"] = False
    try:
        assert _select_person(client, "employee").status_code == 302
        submitted = _post(
            client,
            "/leave/submit",
            {
                "start_date": "2030-06-10",
                "end_date": "2030-06-14",
                "leave_type": "Vacation",
                "comment": "Summer leave",
            },
        )
        assert submitted.status_code == 200
        leave_request = db.list_user_leave_requests(
            db.get_user_by_username("employee")["id"]
        )[0]
        assert b"Summer leave" in client.get("/history").data

        assert _select_person(client, "admin").status_code == 302
        viewed = client.get(f"/admin/leave/{leave_request['id']}/view")
        assert viewed.status_code == 200
        assert viewed.data[:4] == b"%PDF"

        reviewed = _post(
            client,
            f"/admin/leave/{leave_request['id']}/review",
            {"status": "accepted", "review_comment": "Approved", "year": "2030", "month": "6"},
        )
        assert reviewed.status_code == 302
        assert db.get_leave_request(leave_request["id"])["status"] == "accepted"
    finally:
        app.config["TESTING"] = True


def test_revised_leave_keeps_prior_document_after_return(client):
    app.config["TESTING"] = False
    try:
        assert _select_person(client, "employee").status_code == 302
        for comment in ("First request", "Updated request"):
            submitted = _post(
                client,
                "/leave/submit",
                {
                    "start_date": "2030-08-12",
                    "end_date": "2030-08-16",
                    "leave_type": "Vacation",
                    "comment": comment,
                },
            )
            assert submitted.status_code == 200
            if comment == "First request":
                first = db.list_user_leave_requests(db.get_user_by_username("employee")["id"])[0]
                db.review_leave_request(first["id"], "needs_changes", "Please update the note.")

        rows = db.list_user_leave_requests(db.get_user_by_username("employee")["id"])
        assert len(rows) == 2
        assert len({row["filename"] for row in rows}) == 2
        assert all(
            client.get(f"/history/leave/{row['id']}/download").data[:4] == b"%PDF"
            for row in rows
        )
    finally:
        app.config["TESTING"] = True


def test_leave_request_rejects_unknown_type_and_oversized_comment(client):
    app.config["TESTING"] = False
    try:
        assert _select_person(client, "employee").status_code == 302
        invalid_type = _post(
            client,
            "/leave/submit",
            {
                "start_date": "2030-04-08",
                "end_date": "2030-04-12",
                "leave_type": "Medical leave",
                "comment": "",
            },
        )
        assert invalid_type.status_code == 400

        oversized_comment = _post(
            client,
            "/leave/submit",
            {
                "start_date": "2030-04-08",
                "end_date": "2030-04-12",
                "leave_type": "Vacation",
                "comment": "x" * 1001,
            },
        )
        assert oversized_comment.status_code == 400
    finally:
        app.config["TESTING"] = True


def test_admin_export_reports_missing_document_instead_of_partial_archive(client):
    app.config["TESTING"] = False
    try:
        assert _select_person(client, "employee").status_code == 302
        submitted = _post(
            client,
            "/timesheets/submit",
            {"year": "2030", "month": "7", "format": "pdf"},
        )
        assert submitted.status_code == 200
        timesheet = db.get_timesheet_for_period(
            db.get_user_by_username("employee")["id"], 2030, 7
        )
        Path(OUTPUT_DIR / "timesheets" / timesheet["filename"]).unlink()

        assert _select_person(client, "admin").status_code == 302
        exported = client.get("/admin/export?year=2030&month=7")
        assert exported.status_code == 409
        assert "missing" in exported.get_json()["error"].lower()
    finally:
        app.config["TESTING"] = True


def test_history_keeps_more_than_two_years_of_timesheets():
    user = db.get_user_by_username("employee")
    for index in range(25):
        year = 2030 + index // 12
        month = index % 12 + 1
        db.upsert_timesheet(
            user_id=user["id"],
            year=year,
            month=month,
            fmt="pdf",
            vacation="",
            sick="",
            custom_days="",
            custom_reason="",
            working_days=20,
            total_hours=160,
            filename=f"{year}-{month}.pdf",
        )

    assert len(db.list_user_timesheets(user["id"])) == 25


def _submit_leave(client, start_date="2030-05-06", end_date="2030-05-10"):
    assert _post(client, "/leave/submit", {"start_date": start_date, "end_date": end_date,
                                           "leave_type": "Vacation", "comment": ""}).status_code == 200
    return db.list_user_leave_requests(db.get_user_by_username("employee")["id"])[0]


def test_employee_can_withdraw_pending_leave_and_journal_keeps_it(client):
    app.config["TESTING"] = False
    try:
        assert _select_person(client, "employee").status_code == 302
        row = _submit_leave(client)
        document = OUTPUT_DIR / "leave" / row["filename"]
        assert document.is_file()
        assert b"Withdraw" in client.get("/leave").data

        response = _post(client, f"/leave/{row['id']}/withdraw",
                         {"expected_submitted_at": row["submitted_at"], "next": "/history"})
        assert response.status_code == 302 and response.headers["Location"].endswith("/history")
        assert db.get_leave_request(row["id"]) is None
        assert not document.exists()
        events = db.list_workflow_events("leave_requests", row["id"])
        assert [event["action"] for event in events] == ["submitted", "withdrawn"]
    finally:
        app.config["TESTING"] = True


def test_withdrawal_is_limited_to_the_owner_and_pending_requests(client):
    app.config["TESTING"] = False
    try:
        assert _select_person(client, "employee").status_code == 302
        row = _submit_leave(client)
        assert _post(client, f"/leave/{row['id']}/withdraw",
                     {"expected_submitted_at": "2000-01-01T00:00:00+00:00"}).status_code == 409

        assert _select_person(client, "admin").status_code == 302
        assert _post(client, f"/leave/{row['id']}/withdraw", {}).status_code == 404
        assert _post(client, f"/admin/leave/{row['id']}/review",
                     {"status": "accepted", "review_comment": "", "year": "2030", "month": "5"}).status_code == 302

        assert _select_person(client, "employee").status_code == 302
        response = _post(client, f"/leave/{row['id']}/withdraw", {})
        assert response.status_code == 409
        assert db.get_leave_request(row["id"])["status"] == "accepted"
        assert b"Withdraw" not in client.get("/leave").data
    finally:
        app.config["TESTING"] = True


def test_profile_timesheet_ignores_annual_leave_without_an_approved_request(client):
    app.config["TESTING"] = False
    try:
        assert _select_person(client, "employee").status_code == 302
        assert _post(client, "/timesheets/submit", {"year": "2030", "month": "3", "format": "pdf",
                                                    "vacation": "4-8", "sick": "11"}).status_code == 200
        employee = db.get_user_by_username("employee")
        saved = db.get_timesheet_for_period(employee["id"], 2030, 3)
        assert saved["vacation"] == ""
        assert saved["sick"] == "11"
    finally:
        app.config["TESTING"] = True


def test_period_steppers_cross_year_boundaries_and_stay_in_range(client):
    assert _select_person(client, "admin").status_code == 302
    inbox = client.get("/admin?year=2027&month=1").get_data(as_text=True)
    assert 'href="/admin?year=2026&amp;month=12"' in inbox
    assert 'href="/admin?year=2027&amp;month=2"' in inbox
    assert "View period" not in inbox
    calendar_page = client.get("/team-calendar?year=2026&month=12").get_data(as_text=True)
    assert 'href="/team-calendar?year=2027&amp;month=1"' in calendar_page
    first = client.get("/admin?year=2020&month=1").get_data(as_text=True)
    assert 'aria-label="Previous month"' not in first and 'href="/admin?year=2020&amp;month=2"' in first
    person = db.get_user_by_username("employee")["id"]
    balances = client.get(f"/leave-balances?year=2026&user_id={person}").get_data(as_text=True)
    assert f'href="/leave-balances?year=2025&amp;user_id={person}"' in balances
