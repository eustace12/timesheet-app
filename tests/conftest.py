import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

import db
import moldova_holidays
from holiday_fixtures import recorded_urlopen

_offline_transport = patch.object(moldova_holidays.urllib.request, "urlopen", recorded_urlopen)
_offline_transport.start()

# Application imports initialize persistence. Isolate collection as well as tests,
# even if a developer has exported deployment credentials in their shell.
_bootstrap = TemporaryDirectory(prefix="people-pytest-bootstrap-")
_bootstrap_root = Path(_bootstrap.name)
_configure_db = db.configure
with patch.dict(os.environ, {"APP_MODE": "demo", "DEMO_MODE": "0", "VERCEL": "",
                              "SUPABASE_URL": "", "SUPABASE_SERVICE_ROLE_KEY": "",
                              "OUTPUT_DIR": str(_bootstrap_root / "generated")}), \
     patch.object(db, "configure", side_effect=lambda _path: _configure_db(_bootstrap_root / "bootstrap.db")), \
     patch.object(moldova_holidays, "_cache_dir", return_value=_bootstrap_root / "holidays"):
    from web_app import app as flask_app
    from web_app import reset_rate_limit_for_tests


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
  """Give every test a fresh SQLite database and rate-limit state."""
  monkeypatch.setenv("DEMO_MODE", "1")
  monkeypatch.setenv("SUPABASE_URL", "")
  monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")
  monkeypatch.setattr(moldova_holidays, "_cache_dir", lambda: tmp_path / "holidays")
  monkeypatch.setattr(moldova_holidays, "_raw_memory", {})
  monkeypatch.setattr(moldova_holidays, "_meta_memory", {})
  db.configure(tmp_path / "timesheet.db")
  db.init_db()
  db.seed_if_empty()
  flask_app.config["TESTING"] = True
  reset_rate_limit_for_tests()
  yield
  reset_rate_limit_for_tests()


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
