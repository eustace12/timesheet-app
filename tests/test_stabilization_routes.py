"""Retryable dependency failures must not masquerade as deleted records."""
import db
import document_storage
import hr_web
import pytest


def unavailable(*args, **kwargs):
    raise RuntimeError('synthetic dependency outage')


def select_employee(client):
    person = db.get_user_by_username('employee')
    with client.session_transaction() as session:
        session['user_id'] = person['id']
    return person


def remote_sheet(client):
    person = select_employee(client)
    return db.upsert_timesheet(user_id=person['id'], year=2026, month=11, fmt='pdf',
        vacation='', sick='', custom_days='', custom_reason='', working_days=21,
        total_hours=168, filename='fictional.pdf', storage_key='timesheets/fictional.pdf')


def test_month_metadata_database_outage_is_retryable(client, monkeypatch):
    select_employee(client)
    monkeypatch.setattr(db, 'get_timesheet_for_period', unavailable)
    response = client.get('/timesheets/period?year=2026&month=11')
    assert response.status_code == 503
    assert 'error' in response.json


def test_leave_assessment_outage_is_retryable(client, monkeypatch):
    select_employee(client)
    monkeypatch.setattr(hr_web, 'assess_leave', unavailable)
    response = client.get('/leave/assessment?start_date=2026-11-09&end_date=2026-11-10')
    assert response.status_code == 503
    assert 'error' in response.json


def test_leave_preflight_outage_creates_no_request(client, monkeypatch):
    import people_experience
    person = select_employee(client)
    monkeypatch.setattr(people_experience, 'assess_leave', unavailable)
    response = client.post('/leave/submit', data={'start_date': '2026-11-09', 'end_date': '2026-11-10'})
    assert response.status_code == 503
    assert not db.list_user_leave_requests(person['id'])


@pytest.mark.parametrize('path', ['/admin/timesheets/{id}/view', '/history/timesheets/{id}/download', '/admin/export?year=2026&month=11'])
def test_storage_timeout_is_retryable_not_a_missing_document(client, monkeypatch, path):
    row = remote_sheet(client)
    class Bucket:
        def download(self, key):
            raise TimeoutError('synthetic storage timeout')
    monkeypatch.setattr(document_storage, '_bucket', lambda: Bucket())
    response = client.get(path.format(id=row['id']))
    assert response.status_code == 503
    assert 'error' in response.json


def test_true_storage_missing_document_stays_404(client, monkeypatch):
    row = remote_sheet(client)
    class MissingObject(Exception):
        status = 404
    class Bucket:
        def download(self, key):
            raise MissingObject('synthetic missing object')
    monkeypatch.setattr(document_storage, '_bucket', lambda: Bucket())
    assert client.get(f'/admin/timesheets/{row["id"]}/view').status_code == 404


@pytest.mark.parametrize('path', ['/generate', '/preview', '/timesheets/submit'])
def test_missing_holiday_calendar_is_retryable_and_never_generates_document(client, monkeypatch, path):
    import moldova_holidays
    import web_app
    person = select_employee(client)
    def missing(*args, **kwargs):
        raise moldova_holidays.HolidayDataUnavailable('synthetic unavailable calendar')
    monkeypatch.setattr(web_app, '_make_generator', missing)
    monkeypatch.setattr(web_app, 'get_moldova_holidays', missing)
    response = client.post(path, data={'year': 2026, 'month': 11, 'employee_name': 'Fictional Employee'})
    assert response.status_code == 503
    assert 'holiday calendar' in response.json['error']
    assert not db.get_timesheet_for_period(person['id'], 2026, 11)


def test_legacy_health_reports_degraded_when_calendar_is_unavailable(client, monkeypatch):
    import moldova_holidays
    import web_app
    def missing(*args, **kwargs):
        raise moldova_holidays.HolidayDataUnavailable('synthetic unavailable calendar')
    monkeypatch.setattr(web_app, 'get_moldova_holidays', missing)
    response = client.get('/health')
    assert response.status_code == 503
    assert response.json['status'] == 'degraded'


def test_holiday_api_validation_rejects_impossible_year_before_fetch(client, monkeypatch):
    import web_app
    monkeypatch.setattr(web_app, 'holidays_meta', unavailable)
    assert client.get('/holidays/0').status_code == 400
    assert client.get('/holidays/10000').status_code == 400


@pytest.mark.parametrize('path,payload', [
    ('/timesheets/submit', {'year': 2026, 'month': 11}),
    ('/leave/submit', {'start_date': '2026-11-09', 'end_date': '2026-11-10'}),
])
def test_document_upload_outage_is_retryable_and_never_submits_record(client, monkeypatch, path, payload):
    person = select_employee(client)
    def failed_upload(*args, **kwargs):
        raise document_storage.DocumentStorageError('synthetic remote upload timeout')
    monkeypatch.setattr(document_storage, 'is_configured', lambda: True)
    monkeypatch.setattr(document_storage, 'upload', failed_upload)
    monkeypatch.setattr(document_storage, 'delete', lambda _key: None)
    response = client.post(path, data=payload)
    assert response.status_code == 503
    assert not db.list_user_timesheets(person['id'])
    assert not db.list_user_leave_requests(person['id'])
