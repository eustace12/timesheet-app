"""Exercise real application views behind the company identity boundary."""
import json
from pathlib import Path

from flask import Flask
import pytest

import db
import hr_store
import production_access
import web_app


TEST_SIGNING_KEY = 'production-integration-session-key-for-tests-only'
TEST_PROXY_KEY = 'production-integration-proxy-key-for-tests-only'


def build_production_app():
    """Reuse real views and templates, with no external database or SSO calls."""
    source = web_app.app
    app = Flask('production-integration', template_folder=str(Path(web_app.__file__).parent / 'templates'),
                static_folder=str(Path(web_app.__file__).parent / 'static'))
    app.config['TESTING'] = True
    production_access.configure_access(app, db.get_user_by_username,
                                       find_timesheet=db.get_timesheet, find_leave=db.get_leave_request)
    for rule in source.url_map.iter_rules():
        if rule.endpoint == 'static':
            continue
        app.add_url_rule(rule.rule, endpoint=rule.endpoint, view_func=source.view_functions[rule.endpoint],
                         methods=rule.methods)
    app.context_processor(web_app.inject_user)
    app.add_template_filter(web_app.display_date, 'display_date')
    app.register_error_handler(web_app.timesheet_absences.AbsenceDataUnavailable, web_app.absence_unavailable)
    return app


def identity(username='employee'):
    return {'X-Urchin-Proxy-Secret': TEST_PROXY_KEY,
            'X-Auth-Request-Email': username + '@example.test'}


@pytest.fixture()
def production_client(monkeypatch, tmp_path):
    monkeypatch.setenv('APP_MODE', 'production')
    monkeypatch.setenv('SECRET_KEY', TEST_SIGNING_KEY)
    monkeypatch.setenv('SSO_PROXY_SECRET', TEST_PROXY_KEY)
    monkeypatch.setenv('SSO_EMAIL_MAPPING_JSON', json.dumps({'employee@example.test': 'employee', 'admin@example.test': 'admin'}))
    monkeypatch.setattr(web_app, 'OUTPUT_DIR', tmp_path / 'documents')
    with build_production_app().test_client() as client:
        yield client


def test_real_employee_shell_has_fixed_identity_and_no_role_switch(production_client):
    response = production_client.get('/timesheet?year=2026&month=11', headers=identity())
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert 'company-identity' in page and 'data-view="timesheet"' in page
    assert 'id="selected_person"' not in page and 'id="demo-role-selector"' not in page
    assert 'Company demo' not in page
    assert production_client.get('/admin', headers=identity()).status_code == 403
    assert production_client.get('/admin/audit', headers=identity()).status_code == 403
    assert production_client.post('/profile', headers=identity(), data={'user_id': 1}).status_code == 404
    assert production_client.post('/demo/role', headers=identity(), data={'role': 'accountant'}).status_code == 404


def test_real_employee_submission_uses_identity_despite_forged_form_and_session(production_client):
    person = db.get_user_by_username('employee')
    admin = db.get_user_by_username('admin')
    with production_client.session_transaction() as session:
        session['user_id'] = admin['id']
        session['demo_role'] = 'accountant'
    response = production_client.post('/leave/submit', headers=identity(),
                                      data={'user_id': admin['id'], 'start_date': '2026-11-09',
                                            'end_date': '2026-11-10', 'leave_type': 'Vacation'})
    assert response.status_code == 200
    assert len(db.list_user_leave_requests(person['id'])) == 1
    assert not db.list_user_leave_requests(admin['id'])
    request = db.list_user_leave_requests(person['id'])[0]
    assert production_client.get(f'/history/leave/{request["id"]}/download', headers=identity('admin')).status_code == 403
    assert production_client.get(f'/history/leave/{request["id"]}/download', headers=identity()).status_code == 200


def test_real_employee_cannot_read_or_change_other_allowance(production_client):
    admin = db.get_user_by_username('admin')
    assert production_client.get(f'/leave-balances?year=2026&user_id={admin["id"]}', headers=identity()).status_code == 403
    assert production_client.post('/leave-balances', headers=identity(),
                                  data={'year': 2026, 'days': 100, 'user_id': admin['id']}).status_code == 403
    own = production_client.get('/leave-balances?year=2026', headers=identity())
    assert own.status_code == 200 and b'Team balances' not in own.data


def test_real_accountant_sets_target_allowance_without_identity_switch(production_client):
    person = db.get_user_by_username('employee')
    admin = db.get_user_by_username('admin')
    response = production_client.post('/leave-balances', headers=identity('admin'),
                                      data={'year': 2026, 'days': 25, 'user_id': person['id']})
    assert response.status_code == 302
    assert hr_store.leave_balance(person['id'], 2026)['entitlement'] == 25
    assert hr_store.leave_balance(admin['id'], 2026)['entitlement'] is None
    page = production_client.get(response.headers['Location'], headers=identity('admin')).get_data(as_text=True)
    assert 'Team balances' in page and f'Editing {person["full_name"]}' in page
    assert f'data-person="{admin["id"]}"' in page
    events = db.list_workflow_events('entitlement', 2026, person['id'])
    assert events[-1]['actor_user_id'] == admin['id']


def test_real_accountant_review_is_attributed_and_employee_cannot_review(production_client):
    person = db.get_user_by_username('employee')
    admin = db.get_user_by_username('admin')
    response = production_client.post('/timesheets/submit', headers=identity(),
                                      data={'year': 2026, 'month': 11, 'format': 'pdf'})
    assert response.status_code == 200
    sheet = db.get_timesheet_for_period(person['id'], 2026, 11)
    values = {'status': 'accepted', 'review_comment': 'Confirmed', 'year': 2026, 'month': 11,
              'expected_submitted_at': sheet['submitted_at']}
    assert production_client.post(f'/admin/timesheets/{sheet["id"]}/review', data=values, headers=identity()).status_code == 403
    assert production_client.post(f'/admin/timesheets/{sheet["id"]}/review', data=values, headers=identity('admin')).status_code == 302
    events = db.list_workflow_events('timesheets', sheet['id'], person['id'])
    assert [(row['action'], row['actor_user_id']) for row in events] == [('submitted', person['id']), ('reviewed', admin['id'])]


def test_real_employee_planning_does_not_reveal_pending_team_requests(production_client):
    colleague = db.get_user_by_username('admin')
    row = db.create_leave_request(user_id=colleague['id'], start_date='2026-11-09',
                                 end_date='2026-11-10', leave_type='Other',
                                 comment='Private planning detail', filename='fictional.pdf')
    query = '/leave/assessment?start_date=2026-11-09&end_date=2026-11-10'
    assessment = production_client.get(query, headers=identity())
    assert assessment.status_code == 200 and assessment.json['team_count'] == 0
    calendar = production_client.get('/team-calendar?year=2026&month=11', headers=identity()).get_data(as_text=True)
    assert colleague['full_name'] not in calendar
    assert 'Private planning detail' not in calendar
    db.review_leave_request(row['id'], 'accepted', 'Approved')
    visible = production_client.get(query, headers=identity())
    assert visible.json['team_count'] == 1
    assert visible.json['team'][0]['full_name'] == colleague['full_name']
    assert 'Private planning detail' not in visible.get_data(as_text=True)


def test_production_review_without_version_cannot_write_decision(production_client):
    person = db.get_user_by_username('employee')
    row = db.upsert_timesheet(user_id=person['id'], year=2026, month=11, fmt='pdf',
                             vacation='', sick='', custom_days='', custom_reason='',
                             working_days=21, total_hours=168, filename='fictional.pdf')
    response = production_client.post(f'/admin/timesheets/{row["id"]}/review', headers=identity('admin'),
                                      data={'status': 'accepted', 'review_comment': 'Confirmed'})
    assert response.status_code == 409
    assert db.get_timesheet(row['id'])['status'] == 'submitted'


def test_accountant_cannot_break_existing_sso_identity_by_renaming_username(production_client):
    admin = db.get_user_by_username('admin')
    response = production_client.post(f"/admin/users/{admin['id']}/edit", headers=identity('admin'),
        data={'username':'renamed-admin', 'full_name':admin['full_name'], 'role':'admin'})
    assert response.status_code == 409
    assert db.get_user_by_username('admin')['id'] == admin['id']
    assert production_client.get('/download/legacy.pdf', headers=identity()).status_code == 404
