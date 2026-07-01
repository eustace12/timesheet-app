import pytest

from web_app import app as flask_app


@pytest.fixture()
def client():
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as client:
        yield client


@pytest.fixture()
def form_base():
  return {
      "year": "2026",
      "month": "6",
      "employee_name": "Test User",
      "company_name": "Urchin Systems SA",
      "hours_per_day": "8",
  }
