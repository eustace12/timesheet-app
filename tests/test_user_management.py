"""Admin user management tests."""

from uuid import uuid4

import db
import pytest


def test_admin_can_edit_and_delete_a_user(client):
    username = f"employee-{uuid4().hex[:10]}"
    created = client.post(
        "/admin/users",
        data={
            "username": username,
            "full_name": "Original Name",
            "role": "employee",
        },
    )
    assert created.status_code == 302
    user = db.get_user_by_username(username)

    updated = client.post(
        f"/admin/users/{user['id']}/edit",
        data={
            "username": username,
            "full_name": "Updated Name",
            "role": "employee",
        },
    )
    assert updated.status_code == 302
    assert db.get_user_by_id(user["id"])["full_name"] == "Updated Name"

    deleted = client.post(f"/admin/users/{user['id']}/delete")
    assert deleted.status_code == 302
    assert db.get_user_by_id(user["id"]) is None


def test_last_administrator_cannot_be_deleted_or_demoted():
    admin = db.get_user_by_username("admin")

    with pytest.raises(ValueError, match="administrator"):
        db.delete_user(admin["id"])
    with pytest.raises(ValueError, match="administrator"):
        db.update_user(admin["id"], "admin", "Accountant", "employee")
