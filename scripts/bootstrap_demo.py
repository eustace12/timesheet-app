"""Initialize only the isolated Vercel demo database using runtime-injected credentials."""
from datetime import datetime, timedelta
from pathlib import Path
import os
import secrets
import subprocess
import sys
from urllib.parse import parse_qs, unquote, urlsplit
from zoneinfo import ZoneInfo

EXPECTED_REF = 'rdoamaeixdzkddvnlvto'
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
public_url = urlsplit(os.environ['SUPABASE_URL'])
if public_url.hostname != EXPECTED_REF + '.supabase.co':
    raise SystemExit('Refusing to initialize a different Supabase project')
connection = urlsplit(os.environ['POSTGRES_URL'])
username = unquote(connection.username or '')
if EXPECTED_REF not in (connection.hostname or '') and not username.endswith('.' + EXPECTED_REF):
    raise SystemExit('Postgres target does not match the isolated demo project')
password = unquote(connection.password or '')
child_env = {key: os.environ[key] for key in ('PATH', 'LANG', 'LC_ALL', 'TMPDIR') if key in os.environ}
child_env.update(PGHOST=connection.hostname, PGPORT=str(connection.port or 5432),
                 PGUSER=username, PGPASSWORD=password, PGDATABASE=connection.path.lstrip('/'),
                 PGSSLMODE=parse_qs(connection.query).get('sslmode', ['require'])[0], PGCONNECT_TIMEOUT='15')
files = [root/'supabase/schema.sql', root/'supabase/hr_workspace.sql']
command = ['psql', '-X', '--set', 'ON_ERROR_STOP=1', '--single-transaction', '-q']
for file in files:
    command.extend(['--file', str(file)])
result = subprocess.run(command, env=child_env, capture_output=True, text=True)
if result.returncode:
    message = result.stderr.replace(password, '[redacted]') if password else result.stderr
    raise SystemExit('Schema initialization failed: ' + message[:2200])
print('Base and People workspace schemas applied to the isolated demo.')

import db
import hr_store
for username, name, role in [('alice', 'Alice Martin', 'employee'),
                             ('daniel', 'Daniel Reed', 'employee'),
                             ('morgan', 'Morgan Taylor', 'admin')]:
    if db.get_user_by_username(username) is None:
        db.create_user(username, name, role)
    person = db.get_user_by_username(username)
    year = datetime.now(ZoneInfo('Europe/Chisinau')).year
    if hr_store.leave_balance(person['id'], year)['entitlement'] is None:
        hr_store.set_entitlement(person['id'], year, '20')

today = datetime.now(ZoneInfo('Europe/Chisinau')).date()
alice = db.get_user_by_username('alice')

# Use the real submission path so the sample PDF is stored and downloadable.
# Production's sensitive signing key cannot be pulled; this local test session
# only needs a transient key and never publishes or persists it.
os.environ['SECRET_KEY'] = secrets.token_urlsafe(48)
from web_app import app
with app.test_client() as client:
    client.get('/')
    with client.session_transaction() as session:
        session['user_id'] = alice['id']
        token = session['_csrf_token']
    if not db.list_user_leave_requests(alice['id']):
        start = today + timedelta(days=3)
        while start.weekday() >= 5:
            start += timedelta(days=1)
        response = client.post('/leave/submit', data={
            '_csrf_token': token, 'start_date': start.isoformat(),
            'end_date': (start + timedelta(days=2)).isoformat(), 'leave_type': 'Vacation',
            'comment': 'Demo leave request — available for approval testing.',
        })
        if response.status_code != 200:
            raise SystemExit('Sample leave submission failed: ' + str(response.status_code))
print('Three synthetic profiles, sample allowance, and stored leave PDF are ready.')
