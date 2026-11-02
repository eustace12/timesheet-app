"""Apply the reviewed additive release schema only to the isolated public demo.

Run via Vercel env run; no credential is printed, pulled or written to disk.
Use --check for target/schema inspection without mutation.
"""
from pathlib import Path
import hashlib
import os
import subprocess
import sys
from urllib.parse import parse_qs, unquote, urlsplit

EXPECTED_REF = 'rdoamaeixdzkddvnlvto'
OFFICE = '--office' in sys.argv
EXPECTED_SHA = ('b5b0b42c5cabba470659108391f9eeb800b85a67dd4ce1f278bd62256c532d43' if OFFICE
                else 'd61f41b78dafa7c800c373398c4549b9c6434bce13c976b72f63a199ca599a6a')
root = Path(__file__).resolve().parents[1]
migration = root / ('supabase/migrations/20261102164908_office_plans.sql' if OFFICE
                    else 'supabase/migrations/20261102160704_workflow_integrity.sql')
url = urlsplit(os.environ['SUPABASE_URL'])
connection = urlsplit(os.environ['POSTGRES_URL'])
username = unquote(connection.username or '')
if url.hostname != EXPECTED_REF + '.supabase.co' or (
    EXPECTED_REF not in (connection.hostname or '') and not username.endswith('.' + EXPECTED_REF)
):
    raise SystemExit('Refusing a target other than the isolated public demo.')
if hashlib.sha256(migration.read_bytes()).hexdigest() != EXPECTED_SHA:
    raise SystemExit('Migration differs from the reviewed release artifact.')
environment = {key: os.environ[key] for key in ('PATH', 'LANG', 'LC_ALL', 'TMPDIR') if key in os.environ}
environment.update(PGHOST=connection.hostname, PGPORT=str(connection.port or 5432),
                   PGUSER=username, PGPASSWORD=unquote(connection.password or ''),
                   PGDATABASE=connection.path.lstrip('/'), PGCONNECT_TIMEOUT='15',
                   PGSSLMODE=parse_qs(connection.query).get('sslmode', ['require'])[0])
check = """select 'employees='||count(*) from public.users;
select 'timesheets='||count(*) from public.timesheets;
select 'leave_requests='||count(*) from public.leave_requests;
select 'snapshot_column='||count(*) from information_schema.columns
where table_schema='public' and table_name='timesheets' and column_name='leave_snapshot';
select 'workflow_events='||coalesce(to_regclass('public.workflow_events')::text,'absent');
select 'office_plans='||coalesce(to_regclass('public.office_plans')::text,'absent');
select 'anonymous_rpc='||count(*) from information_schema.routine_privileges
where routine_schema='public' and routine_name like '%_atomic' and grantee in ('PUBLIC','anon','authenticated');
"""
sql = check if '--check' in sys.argv else migration.read_text() + '\n' + check
result = subprocess.run(['psql', '-X', '-qAt', '--set', 'ON_ERROR_STOP=1', '--single-transaction'],
                        input=sql, env=environment, capture_output=True, text=True)
if result.returncode:
    raise SystemExit('Release schema check/apply failed; no credentials or database error payload was printed.')
print('Verified isolated demo target: ' + EXPECTED_REF)
print(result.stdout.strip())
print('Read-only check complete.' if '--check' in sys.argv else 'Additive release migration applied.')
