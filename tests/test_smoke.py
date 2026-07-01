"""End-to-end smoke via Flask test client."""

from web_app import app


def test_index_and_health_and_holidays(client):
  with app.app_context():
    pass
  assert client.get("/").status_code == 200
  health = client.get("/health").get_json()
  assert health["status"] in ("ok", "degraded")
  hol = client.get("/holidays/2026?scope=national").get_json()
  assert hol["holidays"]
  preview = client.post(
      "/preview",
      data={"year": "2026", "month": "6", "hours_per_day": "8",
            "vacation": "1-5", "sick": "10-12"},
  ).get_json()
  assert preview["summary"]["working_days"] < 22
