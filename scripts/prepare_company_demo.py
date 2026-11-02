"""Add non-destructive fictional examples only to the isolated People demo."""
from datetime import datetime, date, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo
import contextlib
import io
import os
import secrets
import sys

EXPECTED_REF = 'rdoamaeixdzkddvnlvto'
if urlsplit(os.environ.get('SUPABASE_URL', '')).hostname != EXPECTED_REF + '.supabase.co':
    raise SystemExit('Refusing to seed a different database')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['SECRET_KEY'] = secrets.token_urlsafe(48)  # local seed client only
import db
import hr_store
from moldova_holidays import get_moldova_holidays
from web_app import app, _approved_vacation_for_month

today = datetime.now(ZoneInfo('Europe/Chisinau')).date()
for username, name, role in [('alice','Alice Martin','employee'),('daniel','Daniel Reed','employee'),
                             ('morgan','Morgan Taylor','admin'),('sam','Sam Lee','employee')]:
    existing = db.get_user_by_username(username)
    if existing and existing['full_name'] != name:
        raise SystemExit('A demo profile ID is already used by another person; existing records were kept.')
    if not existing:
        db.create_user(username, name, role)
    person = db.get_user_by_username(username)
    if hr_store.leave_balance(person['id'], today.year)['entitlement'] is None:
        hr_store.set_entitlement(person['id'], today.year, '20')

holidays = get_moldova_holidays(today.year)
candidate = today
while candidate.month == today.month and (candidate.weekday() >= 5 or candidate in holidays):
    candidate += timedelta(days=1)
if candidate.month != today.month:
    candidate = today
    while candidate.weekday() >= 5 or candidate in holidays:
        candidate -= timedelta(days=1)
created = {'timesheets':0, 'leave_requests':0}
with app.test_client() as client, contextlib.redirect_stdout(io.StringIO()):
    client.get('/')
    with client.session_transaction() as session:
        csrf = session['_csrf_token']
    def select(person):
        with client.session_transaction() as session:
            session['user_id'] = person['id']
    def post(path, values, expected=200):
        response = client.post(path, data={'_csrf_token':csrf, **values})
        if response.status_code != expected:
            raise RuntimeError('Demo setup failed at '+path+' (HTTP '+str(response.status_code)+')')
        return response.get_json() if response.is_json else None
    for username in ['alice', 'sam']:
        person = db.get_user_by_username(username)
        if db.get_timesheet_for_period(person['id'], today.year, today.month):
            continue
        select(person)
        requests = db.list_user_leave_requests(person['id'])
        covered = any(row['status'] in ('accepted','submitted') and row['start_date'] <= candidate.isoformat() <= row['end_date'] for row in requests)
        if not covered:
            post('/leave/submit', {'start_date':candidate.isoformat(),'end_date':candidate.isoformat(),
                 'leave_type':'Vacation','comment':'Demo: one planned day off, awaiting review.'})
            created['leave_requests'] += 1
        approved = _approved_vacation_for_month(person, today.year, today.month)
        vacation = ','.join(value for value in [approved,str(candidate.day)] if value)
        post('/timesheets/submit', {'year':str(today.year),'month':str(today.month),'format':'pdf','vacation':vacation})
        created['timesheets'] += 1
        break
    daniel = db.get_user_by_username('daniel')
    if db.get_timesheet_for_period(daniel['id'], today.year, today.month) is None:
        select(daniel)
        sheet = post('/timesheets/submit', {'year':str(today.year),'month':str(today.month),'format':'xlsx'})
        post('/admin/timesheets/'+str(sheet['id'])+'/review', {'status':'needs_changes',
             'review_comment':'Please verify your absence dates, then resubmit.','year':str(today.year),'month':str(today.month)},302)
        created['timesheets'] += 1
print('Fictional company demo prepared; existing records preserved. New examples:', created)
