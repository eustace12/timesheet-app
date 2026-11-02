"""The shared workspace opens every section without authentication."""

import pytest

import db
from web_app import app


@pytest.fixture(autouse=True)
def production_request_handling(client, monkeypatch):
    monkeypatch.setitem(app.config, "TESTING", False)


@pytest.mark.parametrize("path", ["/", "/leave", "/history", "/admin", "/admin/users"])
def test_sections_open_without_login(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert b'type="password"' not in response.data
    assert b'action="/logout"' not in response.data


def test_old_login_url_redirects_to_generator(client):
    response = client.get("/login")
    assert response.status_code == 302
    assert response.headers["Location"] == "/"
    assert b'type="password"' not in client.get("/login", follow_redirects=True).data


def test_profile_selection_needs_no_password_and_keeps_admin_available(client):
    client.get("/")
    with client.session_transaction() as session:
        token = session["_csrf_token"]
    employee = db.get_user_by_username("employee")
    response = client.post("/profile", data={"user_id": employee["id"], "_csrf_token": token})
    assert response.status_code == 302
    assert b"Test User" in client.get("/").data
    assert client.get("/admin").status_code == 200
    assert client.get("/admin/users").status_code == 200


def test_create_person_does_not_require_a_password(client):
    client.get("/admin/users")
    with client.session_transaction() as session:
        token = session["_csrf_token"]
    response = client.post("/admin/users", data={
        "username": "new-person", "full_name": "New Person", "role": "employee",
        "_csrf_token": token,
    })
    assert response.status_code == 302
    person = db.get_user_by_username("new-person")
    assert person["full_name"] == "New Person"
    assert b'type="password"' not in client.get(f"/admin/users/{person['id']}/edit").data


def test_empty_database_still_opens_generator_and_people(client, tmp_path):
    db.configure(tmp_path / "empty.db")
    db.init_db()
    assert client.get("/").status_code == 200
    assert client.get("/admin/users").status_code == 200


def test_state_changes_still_require_csrf(client):
    client.get("/")
    assert client.post("/profile", data={"user_id": 1}).status_code == 400
    assert client.post("/admin/users", data={"username": "bad"}).status_code == 400
