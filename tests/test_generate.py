"""PDF / Excel generation tests."""


def test_generate_pdf(client, form_base):
    r = client.post("/generate", data={**form_base, "format": "pdf"})
    assert r.status_code == 200
    assert r.content_type == "application/pdf"
    assert r.data[:4] == b"%PDF"


def test_generate_xlsx_numeric(client, form_base):
    r = client.post("/generate", data={**form_base, "format": "xlsx"})
    assert r.status_code == 200
    assert "spreadsheetml" in r.content_type
    assert r.data[:2] == b"PK"


def test_generate_requires_employee(client, form_base):
    r = client.post("/generate", data={**form_base, "employee_name": "", "format": "pdf"})
    assert r.status_code == 400


def test_multi_period_absences_preview(client, form_base):
    r = client.post(
        "/preview",
        data={
            **form_base,
            "month": "6",
            "vacation": "1-5,20-24",
            "sick": "10-12",
        },
    )
    assert r.status_code == 200
    data = r.get_json()
    statuses = {d["status"] for d in data["days"]}
    assert "vacation" in statuses
    assert "sick" in statuses


def test_generate_rate_limit(client, form_base):
    from web_app import app, reset_rate_limit_for_tests

    reset_rate_limit_for_tests()
    app.config['TESTING'] = False
    try:
        for _ in range(10):
            r = client.post("/generate", data={**form_base, "format": "pdf"})
            assert r.status_code == 200
        r = client.post("/generate", data={**form_base, "format": "pdf"})
        assert r.status_code == 429
    finally:
        app.config['TESTING'] = True
        reset_rate_limit_for_tests()
