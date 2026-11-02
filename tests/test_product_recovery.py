"""Recovery actions remain visible across period changes and role workflows."""
from datetime import date

import db
import hr_store
import people_experience


def select_employee(client):
    client.get('/')
    person = db.get_user_by_username('employee')
    with client.session_transaction() as session:
        session['user_id'] = person['id']
    return person


def add_sheet(person, month, status='submitted'):
    row = db.upsert_timesheet(user_id=person['id'], year=2026, month=month, fmt='pdf',
                             vacation='', sick='', custom_days='', custom_reason='',
                             working_days=20, total_hours=160, filename=f'month-{month}.pdf')
    if status != 'submitted':
        db.review_timesheet(row['id'], status, 'Please confirm your absence dates.')
    return row


def test_previous_returned_month_remains_actionable_on_home(client, monkeypatch):
    person = select_employee(client)
    monkeypatch.setattr(people_experience, 'company_today', lambda: date(2026, 11, 2))
    add_sheet(person, 9, 'accepted')
    add_sheet(person, 10, 'needs_changes')
    add_sheet(person, 8)
    add_sheet(person, 12, 'needs_changes')
    home = client.get('/').get_data(as_text=True)
    assert 'Earlier submissions' in home
    section = home.split('Earlier submissions', 1)[1].split('</section>', 1)[0]
    assert 'October 2026' in section and 'August 2026' in section
    assert 'September 2026' not in section and 'December 2026' not in section
    assert '/timesheet?year=2026&amp;month=10' in section
    assert 'Please confirm your absence dates.' in section


def test_returned_leave_can_be_revised_from_time_off(client):
    person = select_employee(client)
    row = db.create_leave_request(user_id=person['id'], start_date='2026-11-09',
                                 end_date='2026-11-10', leave_type='Vacation',
                                 comment='Planned break', filename='request.pdf')
    db.review_leave_request(row['id'], 'needs_changes', 'Move the start date.')
    page = client.get('/leave').get_data(as_text=True)
    assert 'Prepare a revised request' in page
    assert 'data-leave-copy=' in page
    assert 'Move the start date.' in page


def test_team_balances_show_reserved_days_and_missing_allowance(client):
    person = select_employee(client)
    hr_store.set_entitlement(person['id'], 2026, 5)
    db.create_leave_request(user_id=person['id'], start_date='2026-11-09',
                            end_date='2026-11-10', leave_type='Vacation', comment='', filename='request.pdf')
    rows = people_experience.team_balances(2026)
    employee = next(row for row in rows if row['person_id'] == person['id'])
    assert employee['balance']['available'] == 3
    assert employee['balance']['pending'] == 2
    assert any(row['balance']['entitlement'] is None for row in rows)
    assert len(rows) == len(db.list_users())


def test_leave_assessment_blocks_request_before_it_overdraws_allowance(client):
    person = select_employee(client)
    hr_store.set_entitlement(person['id'], 2026, 1)
    response = client.get('/leave/assessment?start_date=2026-11-09&end_date=2026-11-10&leave_type=Vacation')
    assert response.status_code == 200
    assert response.json['can_submit'] is False
    assert response.json['balances'][0]['after_request'] == -1
    assert 'exceeds' in response.json['reason']


def test_team_allowance_form_keeps_target_separate_from_accountant_identity(client):
    from flask import render_template
    from web_app import app

    accountant = db.get_user_by_username('stanislav')
    person = db.get_user_by_username('employee')
    hr_store.set_entitlement(person['id'], 2026, 20)
    with app.test_request_context('/leave-balances?year=2026'):
        page = render_template('hr_workspace.html', section='balance', year=2026, month=11,
                               user=accountant, balance_person=person,
                               balance=hr_store.leave_balance(person['id'], 2026),
                               team_balances=people_experience.team_balances(2026))
    assert f'Manage allowance for {person["full_name"]}' in page
    assert f'user_id={person["id"]}' in page
    settings_form = page.split('class="settings-form">', 1)[1].split('</form>', 1)[0]
    assert f'name="user_id" value="{person["id"]}"' in settings_form
    assert f'Editing {person["full_name"]}' in page
    period_filter = page.split('class="period-filter"', 1)[1].split('</form>', 1)[0]
    assert f'name="user_id" value="{person["id"]}"' in period_filter
