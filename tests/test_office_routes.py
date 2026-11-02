from datetime import date
import re

import db
import office_store
import web_app
from test_production_integration import production_client, identity


def payload(**changes):
    return {'work_date': '2026-11-03', 'planned': '1', 'expected_updated_at': '', **changes}


def test_optional_week_page_and_empty_team(client):
    result = client.get('/office?week=2026-11-04')
    assert result.status_code == 200
    assert b'Optional' in result.data and b'no approval needed' in result.data
    data = client.get('/office/data?week=2026-11-04').json
    assert data['week_start'] == '2026-11-02' and data['week_end'] == '2026-11-08'
    assert len(data['days']) == 7
    assert all(not day['people'] for day in data['days'])
    assert not data['my_plans']


def test_mark_remove_undo_and_time_edits_stay_in_same_week(client):
    result = client.post('/office/plans', data=payload()).json
    assert result['ok'] and result['plan']['start_time'] is None
    first = result['plan']
    timed = client.post('/office/plans', data=payload(start_time='10:00', end_time='17:30', expected_updated_at=first['updated_at'])).json['plan']
    assert timed['planned'] and timed['start_time'] == '10:00'
    removed = client.post('/office/plans', data=payload(planned='0', expected_updated_at=timed['updated_at'])).json['plan']
    data = client.get('/office/data?week=2026-11-02').json
    assert not any(day['people'] for day in data['days'])
    assert not data['my_plans']['2026-11-03']['planned']
    restored = client.post('/office/plans', data=payload(start_time='10:00', end_time='17:30', expected_updated_at=removed['updated_at'])).json['plan']
    cleared = client.post('/office/plans', data=payload(expected_updated_at=restored['updated_at'])).json['plan']
    assert cleared['planned'] and cleared['start_time'] is None and cleared['end_time'] is None


def test_week_crosses_year_without_shifting_dates(client):
    result = client.get('/office/data?week=2026-12-31').json
    assert result['week_start'] == '2026-12-28' and result['week_end'] == '2027-01-03'
    assert result['days'][-1]['date'] == '2027-01-03'


def test_bad_input_and_stale_tab_do_not_change_plan(client):
    assert client.get('/office?week=not-a-date').status_code == 400
    assert client.post('/office/plans', data=payload(start_time='18:00', end_time='09:00')).status_code == 400
    saved = client.post('/office/plans', data=payload()).json['plan']
    conflict = client.post('/office/plans', data=payload(planned='0'))
    assert conflict.status_code == 409
    assert client.get('/office/data?week=2026-11-02').json['my_plans']['2026-11-03']['updated_at'] == saved['updated_at']


def test_profile_changed_in_another_demo_tab_cannot_write_wrong_person(client):
    assert client.post('/office/plans', data=payload(expected_person_id='999999')).status_code == 409
    assert not client.get('/office/data?week=2026-11-02').json['my_plans']


def test_store_outage_returns_retryable_response(client, monkeypatch):
    def unavailable(*args, **kwargs):
        raise RuntimeError('database failure')
    monkeypatch.setattr(office_store, 'save_plan', unavailable)
    assert client.post('/office/plans', data=payload()).status_code == 503
    monkeypatch.setattr(office_store, 'plans', unavailable)
    assert client.get('/office/data?week=2026-11-02').status_code == 503


def test_optional_office_outage_does_not_break_the_home_workspace(client, monkeypatch):
    def unavailable(*args, **kwargs):
        raise RuntimeError('office table unavailable')
    monkeypatch.setattr(office_store, 'own_plans', unavailable)
    response = client.get('/')
    assert response.status_code == 200 and b'Office plans are temporarily unavailable' in response.data
    assert b'YOUR MONTH' in response.data


def test_office_writes_require_the_existing_csrf_protection(client, monkeypatch):
    monkeypatch.setitem(web_app.app.config, 'TESTING', False)
    page = client.get('/office?week=2026-11-02')
    token = re.search(rb'name="_csrf_token" value="([^\"]+)"', page.data).group(1).decode()
    assert client.post('/office/plans', data=payload()).status_code == 400
    assert client.post('/office/plans', data=payload(_csrf_token=token)).status_code == 200


def test_company_employee_uses_own_sso_identity_and_can_read_team(production_client):
    own = db.get_user_by_username('employee')
    colleague = db.get_user_by_username('admin')
    office_store.save_plan(colleague['id'], '2026-11-03', True, '09:00', '16:00')
    response = production_client.post('/office/plans', headers=identity(), data=payload(user_id=colleague['id']))
    assert response.status_code == 200 and response.json['plan']['user_id'] == own['id']
    data = production_client.get('/office/data?week=2026-11-02', headers=identity()).json
    tuesday = next(day for day in data['days'] if day['date'] == '2026-11-03')
    assert {row['user_id'] for row in tuesday['people']} == {own['id'], colleague['id']}
    assert data['my_plans']['2026-11-03']['user_id'] == own['id']
    assert production_client.get('/office/data?week=2026-11-02').status_code == 401
    assert production_client.get('/admin', headers=identity()).status_code == 403


def test_accountant_plans_own_visit_without_changing_employees(production_client):
    admin = db.get_user_by_username('admin')
    response = production_client.post('/office/plans', headers=identity('admin'), data=payload())
    assert response.status_code == 200 and response.json['plan']['user_id'] == admin['id']
    assert not db.list_user_timesheets(admin['id'])
    assert not db.list_user_leave_requests(admin['id'])
