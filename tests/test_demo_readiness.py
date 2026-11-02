"""Company demo workflows and failure cases, with disposable records."""
import db
from web_app import app


def post(client, path, data):
    client.get('/')
    with client.session_transaction() as session:
        token = session['_csrf_token']
    return client.post(path, data={**data, '_csrf_token': token})


def test_demo_roles_switch_without_changing_employee(client):
    person = db.get_user_by_username('employee')
    post(client, '/profile', {'user_id': person['id']})
    switched = post(client, '/demo/role', {'role': 'accountant', 'next': '/admin'})
    assert switched.status_code == 302
    with client.session_transaction() as session:
        assert session['demo_role'] == 'accountant'
        assert session['user_id'] == person['id']
    assert b'data-demo-role="accountant"' in client.get('/admin').data
    assert post(client, '/demo/role', {'role': 'root'}).status_code == 400
    assert post(client, '/demo/role', {'role': 'employee', 'next': 'https://example.com'}).headers['Location'] == '/'


def test_role_changes_require_csrf(client):
    app.config['TESTING'] = False
    try:
        client.get('/')
        assert client.post('/demo/role', data={'role':'accountant'}).status_code == 400
    finally:
        app.config['TESTING'] = True


def test_submitted_month_is_not_silently_replaced(client):
    person = db.get_user_by_username('employee')
    post(client, '/profile', {'user_id':person['id']})
    data = {'year':'2026','month':'7','format':'pdf'}
    submitted = post(client, '/timesheets/submit', data)
    assert submitted.status_code == 200
    original = db.get_timesheet_for_period(person['id'],2026,7)
    assert post(client, '/timesheets/submit', {**data,'vacation':'1-7'}).status_code == 409
    assert db.get_timesheet_for_period(person['id'],2026,7)['filename'] == original['filename']
    metadata = client.get('/timesheets/period?year=2026&month=7')
    assert metadata.status_code == 200
    assert metadata.json['timesheet']['status'] == 'submitted'
    assert post(client, f"/admin/timesheets/{original['id']}/review", {'status':'needs_changes','review_comment':'Please add vacation dates.'}).status_code == 302
    assert post(client, '/timesheets/submit', {**data,'vacation':'1-7'}).status_code == 200


def test_returning_a_document_requires_a_reason(client):
    person = db.get_user_by_username('employee')
    post(client, '/profile', {'user_id':person['id']})
    post(client, '/timesheets/submit', {'year':'2026','month':'7','format':'pdf'})
    sheet = db.get_timesheet_for_period(person['id'],2026,7)
    assert post(client, f"/admin/timesheets/{sheet['id']}/review", {'status':'needs_changes','review_comment':''}).status_code == 400


def test_duplicate_leave_is_rejected_without_creating_an_extra_record(client):
    person = db.get_user_by_username('employee')
    post(client, '/profile', {'user_id':person['id']})
    data={'start_date':'2026-11-09','end_date':'2026-11-10','leave_type':'Vacation'}
    assert post(client,'/leave/submit',data).status_code == 200
    assert post(client,'/leave/submit',data).status_code == 409
    assert len(db.list_user_leave_requests(person['id'])) == 1


def test_period_metadata_follows_selected_month_and_employee(client):
    person = db.get_user_by_username('employee')
    post(client,'/profile',{'user_id':person['id']})
    row=db.create_leave_request(user_id=person['id'],start_date='2026-08-03',end_date='2026-08-05',leave_type='Vacation',comment='',filename='example.pdf')
    db.review_leave_request(row['id'],'accepted','Approved')
    assert client.get('/timesheets/period?year=2026&month=8').json['approved_vacation'] == '3-5'
    assert client.get('/timesheets/period?year=2026&month=7').json['approved_vacation'] == ''
    assert client.get('/timesheets/period?year=2026&month=13').status_code == 400
