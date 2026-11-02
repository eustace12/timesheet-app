"""Task-first overview and contextual leave decisions."""
import db
import hr_store
from web_app import app


def select(client, username='employee'):
    client.get('/')
    with client.session_transaction() as session:
        session['user_id'] = db.get_user_by_username(username)['id']
        return session['_csrf_token']


def test_home_shows_next_actions_and_separate_monthly_editor(client):
    select(client)
    home = client.get('/')
    assert home.status_code == 200
    assert b'Your workspace' in home.data
    assert b'href="/timesheet"' in home.data
    assert b'id="timesheetForm"' not in home.data
    assert b'id="timesheetForm"' in client.get('/timesheet').data


def test_leave_assessment_reports_balance_overlap_and_team_context(client):
    select(client)
    person = db.get_user_by_username('employee')
    other = db.get_user_by_username('stanislav')
    hr_store.set_entitlement(person['id'], 2026, 20)
    row = db.create_leave_request(user_id=other['id'],start_date='2026-11-09',end_date='2026-11-10',leave_type='Vacation',comment='Private note',filename='demo.pdf')
    db.review_leave_request(row['id'],'accepted','')
    response = client.get('/leave/assessment?start_date=2026-11-09&end_date=2026-11-11&leave_type=Vacation')
    assert response.status_code == 200
    data = response.json
    assert data['working_days'] == 3
    assert data['balances'][0]['after_request'] == 17
    assert data['team'][0]['full_name'] == other['full_name']
    assert 'Private note' not in response.get_data(as_text=True)
    db.create_leave_request(user_id=person['id'],start_date='2026-11-09',end_date='2026-11-09',leave_type='Vacation',comment='',filename='own.pdf')
    assert client.get('/leave/assessment?start_date=2026-11-09&end_date=2026-11-11').json['can_submit'] is False


def test_leave_assessment_splits_years_and_does_not_double_reserve_existing_request(client):
    select(client)
    person = db.get_user_by_username('employee')
    hr_store.set_entitlement(person['id'],2026,20)
    hr_store.set_entitlement(person['id'],2027,20)
    row = db.create_leave_request(user_id=person['id'],start_date='2026-12-31',end_date='2027-01-04',leave_type='Vacation',comment='',filename='own.pdf')
    data = client.get(f"/leave/assessment?start_date=2026-12-31&end_date=2027-01-04&request_id={row['id']}").json
    assert [item['year'] for item in data['balances']] == [2026,2027]
    assert data['balances'][0]['after_request'] == hr_store.leave_balance(person['id'],2026)['available']


def test_zero_working_day_annual_leave_cannot_be_submitted(client):
    token = select(client)
    response=client.post('/leave/submit',data={'_csrf_token':token,'start_date':'2026-11-07','end_date':'2026-11-08','leave_type':'Vacation'})
    assert response.status_code == 400
    assert not db.list_user_leave_requests(db.get_user_by_username('employee')['id'])

def test_period_metadata_keeps_the_saved_totals_for_legacy_recorded_hours(client):
    select(client)
    person = db.get_user_by_username('employee')
    row = db.upsert_timesheet(user_id=person['id'],year=2026,month=10,fmt='pdf',vacation='',sick='',custom_days='',custom_reason='',working_days=1,total_hours=7.5,filename='saved.pdf')
    db.review_timesheet(row['id'],'accepted','')
    saved = client.get('/timesheets/period?year=2026&month=10').json['timesheet']
    assert saved['total_hours'] == 7.5
    assert saved['working_days'] == 1
