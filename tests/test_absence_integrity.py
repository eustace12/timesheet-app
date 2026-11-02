from datetime import date

import pytest
import db
import web_app


def approved(person, kind, start, end):
    row = db.create_leave_request(user_id=person['id'], start_date=start, end_date=end,
                                  leave_type=kind, comment='', filename='test.pdf')
    db.review_leave_request(row['id'], 'accepted', '')
    return row


def test_submission_includes_approved_leave_even_when_client_omits_it(client):
    person = dict(db.get_user_by_id(db.list_employees()[0]['id']))
    approved(person, 'Vacation', '2026-11-09', '2026-11-10')
    with client.session_transaction() as session:
        session['user_id'] = person['id']
    response = client.post('/timesheets/submit', data={'year': '2026', 'month': '11', 'format': 'pdf'})
    assert response.status_code == 200
    saved = db.get_timesheet_for_period(person['id'], 2026, 11)
    assert saved['vacation'] == '9-10'
    assert saved['total_hours'] == 152


def test_approved_unpaid_and_other_days_keep_their_types(client):
    person = dict(db.get_user_by_id(db.list_employees()[0]['id']))
    approved(person, 'Unpaid', '2026-11-09', '2026-11-09')
    approved(person, 'Other', '2026-11-10', '2026-11-10')
    with client.session_transaction() as session:
        session['user_id'] = person['id']
    with web_app.app.test_request_context('/generate', method='POST', data={'year': '2026', 'month': '11'}):
        params = web_app._timesheet_params(person)
    assert params['vacation_raw'] == ''
    assert params['custom_absences'][date(2026, 11, 9)] == 'Unpaid leave'
    assert params['custom_absences'][date(2026, 11, 10)] == 'Other approved leave'
    metadata = client.get('/timesheets/period?year=2026&month=11').get_json()
    assert metadata['approved_vacation'] == ''
    assert {row['leave_type'] for row in metadata['approved_absences']} == {'Unpaid', 'Other'}


def test_absence_lookup_failure_blocks_instead_of_creating_full_month(client, monkeypatch):
    def unavailable(*args):
        raise RuntimeError('database unavailable')
    monkeypatch.setattr(db, 'list_approved_leave_ranges', unavailable)
    assert client.get('/timesheets/period?year=2026&month=11').status_code == 503
    assert client.post('/timesheets/submit', data={'year': '2026', 'month': '11'}).status_code == 503


@pytest.mark.parametrize('raw', ['10-9', '0', '32', '1-99999'])
def test_generate_rejects_invalid_absence_ranges(client, raw):
    assert client.post('/generate', data={'year': '2026', 'month': '11',
                                        'employee_name': 'Test Employee', 'vacation': raw}).status_code == 400
