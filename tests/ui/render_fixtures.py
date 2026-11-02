"""Render real screens against disposable SQLite for DOM tests."""
import json
import os
from pathlib import Path
import sys
from datetime import datetime
from tempfile import TemporaryDirectory
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['DEMO_MODE'] = '0'
import db
import moldova_holidays
from holiday_fixtures import recorded_urlopen
with TemporaryDirectory() as directory, \
     patch.dict(os.environ, {'APP_MODE':'demo', 'DEMO_MODE':'0', 'VERCEL':'',
                              'SUPABASE_URL':'', 'SUPABASE_SERVICE_ROLE_KEY':'',
                              'OUTPUT_DIR':str(Path(directory) / 'generated')}), \
     patch.object(moldova_holidays, '_cache_dir', return_value=Path(directory) / 'holidays'), \
     patch.object(moldova_holidays.urllib.request, 'urlopen', recorded_urlopen):
    # web_app initializes persistence on import; parallel renderers must never
    # initialize the shared development database, even before fixture setup.
    configure_db = db.configure
    with patch.object(db, 'configure', side_effect=lambda _path: configure_db(Path(directory) / 'test.db')):
        from web_app import app
        from test_production_integration import build_production_app, identity, TEST_SIGNING_KEY, TEST_PROXY_KEY
    db.configure(Path(directory) / 'test.db')
    db.init_db()
    db.create_user('alice', 'Alice Example', 'employee')
    db.create_user('bob', 'Bob Example', 'employee')
    now = datetime.now()
    for username in ['alice', 'bob']:
        person = db.get_user_by_username(username)
        db.upsert_timesheet(user_id=person['id'],year=now.year,month=now.month,fmt='pdf',vacation='',sick='',custom_days='',custom_reason='',working_days=22,total_hours=176,filename='demo-'+username+'.pdf')
    result = {}
    with app.test_client() as client:
        for username in ['alice', 'bob']:
            person = db.get_user_by_username(username)
            with client.session_transaction() as session:
                session['user_id'] = person['id']
            result[str(person['id'])] = {path: client.get(path).get_data(as_text=True)
                for path in ['/', '/timesheet', '/leave', '/leave-balances', '/team-calendar', '/office', '/history', '/admin', '/admin/users']}
        with client.session_transaction() as session:
            session['user_id'] = db.get_user_by_username('alice')['id']
        db.create_user('charlie', 'Charlie Example', 'employee')
        result['updated_people'] = client.get('/admin/users').get_data(as_text=True)
        db.review_timesheet(1,'accepted','Approved in tests')
        result['reviewed_admin'] = client.get('/admin').get_data(as_text=True)
        leave = db.create_leave_request(user_id=db.get_user_by_username('alice')['id'],
            start_date=now.strftime('%Y-%m-01'), end_date=now.strftime('%Y-%m-01'),
            leave_type='Unpaid', comment='Synthetic approved request', filename='fixture-only.pdf')
        db.review_leave_request(leave['id'], 'accepted', 'Approved in fixture')
        result['approved_leave_admin'] = client.get('/admin').get_data(as_text=True)
    db.create_user('accountant', 'Company Accountant', 'admin')
    with patch.dict(os.environ, {'APP_MODE':'production', 'SECRET_KEY':TEST_SIGNING_KEY,
                    'SSO_PROXY_SECRET':TEST_PROXY_KEY,
                    'SSO_EMAIL_MAPPING_JSON':json.dumps({'alice@example.test':'alice', 'accountant@example.test':'accountant'})}):
        production = build_production_app()
        result['production'] = {}
        result['production_people'] = {}
        with production.test_client() as client:
            for role, username in [('employee', 'alice'), ('accountant', 'accountant')]:
                result['production_people'][role] = db.get_user_by_username(username)['id']
                result['production'][role] = {path: client.get(path, headers=identity(username)).get_data(as_text=True)
                    for path in ['/', '/timesheet', '/leave', '/leave-balances', '/team-calendar', '/office', '/history', '/admin', '/admin/users', '/admin/audit']}
            db.create_user('dora', 'Dora Example', 'employee')
            result['production_updated_people'] = client.get('/admin/users', headers=identity('accountant')).get_data(as_text=True)
    print(json.dumps(result))
