"""Regressions found while rehearsing persisted accounting transitions."""
from datetime import date
from concurrent.futures import ThreadPoolExecutor
import getpass
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

import pytest

import db
import hr_store
import office_store
import timesheet_absences


def test_last_supported_december_can_be_read_and_submitted_without_overflow():
    person = db.get_user_by_username('employee')
    assert db.list_leave_requests_for_month(9999, 12) == []
    assert db.list_approved_leave_ranges(person['id'], 9999, 12) == []
    sheet = db.upsert_timesheet(user_id=person['id'], year=9999, month=12, fmt='pdf',
        vacation='', sick='', custom_days='', custom_reason='', working_days=0,
        total_hours=0, filename='fictional.pdf', leave_snapshot=[])
    assert sheet['year'] == 9999 and sheet['month'] == 12


def test_last_calendar_day_in_approved_absence_does_not_overflow():
    rows = [{'id': 1, 'start_date': '9999-12-31', 'end_date': '9999-12-31', 'leave_type': 'Unpaid'}]
    assert timesheet_absences.approved_days(rows, 9999, 12) == {31: 'Unpaid'}


@pytest.mark.parametrize('types', [('Vacation', 'Unpaid'), ('Unpaid', 'Other')])
def test_legacy_conflicting_approved_absences_fail_instead_of_picking_a_type(types):
    rows = [{'id': index, 'start_date': '2026-11-09', 'end_date': '2026-11-10', 'leave_type': kind}
            for index, kind in enumerate(types, 1)]
    with pytest.raises(ValueError, match='conflict'):
        timesheet_absences.approved_days(rows, 2026, 11)


def test_adjacent_month_leave_query_keeps_only_dates_overlapping_its_month():
    person = db.get_user_by_username('employee')
    row = db.create_leave_request(user_id=person['id'], start_date='2026-12-31',
        end_date='2027-01-04', leave_type='Unpaid', comment='', filename='fictional.pdf')
    db.review_leave_request(row['id'], 'accepted', '')
    assert db.list_approved_leave_ranges(person['id'], 2026, 11) == []
    assert timesheet_absences.approved_days([dict(row) for row in db.list_approved_leave_ranges(person['id'], 2026, 12)], 2026, 12) == {31:'Unpaid'}
    assert timesheet_absences.approved_days([dict(row) for row in db.list_approved_leave_ranges(person['id'], 2027, 1)], 2027, 1) == {1:'Unpaid',2:'Unpaid',3:'Unpaid',4:'Unpaid'}


def test_concurrent_allowance_reduction_cannot_make_pending_leave_negative():
    person = db.get_user_by_username('employee')
    hr_store.set_entitlement(person['id'], 2026, 1)
    def write(action):
        try:
            if action == 'allowance':
                hr_store.set_entitlement(person['id'], 2026, 0)
            else:
                db.create_leave_request(user_id=person['id'], start_date='2026-11-09',
                    end_date='2026-11-09', leave_type='Vacation', comment='', filename='fictional.pdf')
            return 'saved'
        except db.WorkflowConflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(write, ['allowance', 'leave'])) == ['conflict', 'saved']
    assert hr_store.leave_balance(person['id'], 2026)['available'] == 0


def test_office_changes_remain_independent_of_approved_accounting_period():
    person = db.get_user_by_username('employee')
    saved = db.upsert_timesheet(user_id=person['id'], year=2026, month=11, fmt='pdf',
        vacation='', sick='', custom_days='', custom_reason='', working_days=21,
        total_hours=168, filename='fictional.pdf')
    db.review_timesheet(saved['id'], 'accepted', '')
    before = dict(db.get_timesheet(saved['id']))
    events = db.list_workflow_events('timesheets', saved['id'])
    plan = office_store.save_plan(person['id'], '2026-11-09', True, '09:00', '18:00')
    office_store.save_plan(person['id'], '2026-11-09', False, expected_updated_at=plan['updated_at'])
    assert dict(db.get_timesheet(saved['id'])) == before
    assert db.list_workflow_events('timesheets', saved['id']) == events


def test_recorded_hours_changed_during_document_generation_cannot_be_submitted():
    person = db.get_user_by_username('employee')
    hr_store.save_entry(person['id'], {'work_date':'2026-11-09','project':'Fictional','task':'Testing','hours':'6.5'})
    hours_used_to_generate_document = hr_store.daily_hours(person['id'], 2026, 11)
    hr_store.save_entry(person['id'], {'work_date':'2026-11-09','project':'Fictional','task':'Follow-up','hours':'1.5'})
    with pytest.raises(db.WorkflowConflict, match='Recorded hours changed'):
        db.upsert_timesheet(user_id=person['id'], year=2026, month=11, fmt='pdf', vacation='',
            sick='', custom_days='', custom_reason='', working_days=1, total_hours=6.5,
            filename='fictional.pdf', daily_hours_snapshot=hours_used_to_generate_document)
    assert db.get_timesheet_for_period(person['id'], 2026, 11) is None
    assert hr_store.daily_hours(person['id'], 2026, 11)[date(2026, 11, 9)] == 8


def test_unchanged_recorded_hours_allow_fractional_minutes_without_float_conflict():
    person = db.get_user_by_username('employee')
    hr_store.save_entry(person['id'], {'work_date':'2026-11-09','project':'Fictional','task':'Testing','hours':'0:20'})
    hours_used_to_generate_document = hr_store.daily_hours(person['id'], 2026, 11)
    saved = db.upsert_timesheet(user_id=person['id'], year=2026, month=11, fmt='pdf', vacation='',
        sick='', custom_days='', custom_reason='', working_days=1, total_hours=1/3,
        filename='fictional.pdf', daily_hours_snapshot=hours_used_to_generate_document)
    assert saved['status'] == 'submitted'


@pytest.mark.skipif(not all(shutil.which(name) for name in ('initdb', 'pg_ctl', 'psql')),
                    reason='PostgreSQL binaries are required for production RPC verification')
def test_postgres_upper_period_and_concurrent_allowance_match_sqlite():
    with tempfile.TemporaryDirectory(prefix='stabilize-pg-') as temporary:
        data = Path(temporary) / 'pg'
        subprocess.run(['initdb', '-D', str(data), '-A', 'trust', '--no-locale'], check=True, capture_output=True)
        subprocess.run(['pg_ctl', '-D', str(data), '-l', str(Path(temporary) / 'postgres.log'),
                        '-o', f"-F -k {temporary} -h '' -p 55432", '-w', 'start'], check=True, capture_output=True)
        def sql(query):
            return subprocess.run(['psql', '-h', temporary, '-p', '55432', '-U', getpass.getuser(),
                                   '-d', 'postgres', '-v', 'ON_ERROR_STOP=1', '-At'],
                                  input=query, text=True, capture_output=True)
        def checked(query):
            result = sql(query)
            assert result.returncode == 0, result.stderr
            return result.stdout
        try:
            checked('CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role BYPASSRLS; '
                    'CREATE SCHEMA storage; CREATE TABLE storage.buckets(id text primary key,name text,public boolean);')
            repository = Path(__file__).resolve().parents[1]
            for file in [repository / 'supabase/schema.sql', repository / 'supabase/hr_workspace.sql',
                         *sorted((repository / 'supabase/migrations').glob('*.sql'))]:
                checked(file.read_text())
            # Check the migration's own session grants before the broad grants
            # below, which exist only for the older workflow test fixtures.
            session_rows = "('" + 'a' * 64 + "','11111111-1111-4111-8111-111111111111','synthetic-encrypted-token',19,1)," \
                "('" + 'b' * 64 + "','11111111-1111-4111-8111-111111111111','synthetic-encrypted-token',20,1)," \
                "('" + 'c' * 64 + "','11111111-1111-4111-8111-111111111111','synthetic-encrypted-token',21,1)"
            assert checked('SET ROLE service_role;INSERT INTO account_sessions VALUES ' + session_rows + ';'
                "DELETE FROM account_sessions WHERE expires_at <= 20;SELECT count(*)::text || ':' || min(expires_at)::text FROM account_sessions;").strip().endswith('1:21')
            assert checked("SELECT relrowsecurity FROM pg_class WHERE oid='public.account_sessions'::regclass;").strip() == 't'
            for role in ('anon', 'authenticated'):
                for statement in ('SELECT * FROM account_sessions;', 'DELETE FROM account_sessions;',
                                  'INSERT INTO account_sessions VALUES ' + session_rows.split(',(', 1)[0] + ';'):
                    denied = sql('SET ROLE ' + role + ';' + statement)
                    assert denied.returncode != 0 and 'permission denied' in denied.stderr
            checked("INSERT INTO users(username,password_hash,full_name,role,created_at) VALUES('test','','Fictional Tester','employee','2026-11-02'); "
                    'GRANT ALL ON ALL TABLES IN SCHEMA public TO service_role; '
                    'GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO service_role;')
            values = dict(user_id=1, year=9999, month=12, format='pdf', vacation='', sick='',
                          custom_days='', custom_reason='', working_days=0, total_hours=0,
                          filename='fictional.pdf', leave_snapshot=[], validate_leave_snapshot=True,
                          submitted_at='2026-11-02T12:00:00+00:00')
            result = checked("SET ROLE service_role;SELECT submit_timesheet_atomic('" + json.dumps(values) + "')->>'year';")
            assert result.strip().endswith('9999')
            checked("SET ROLE service_role;SELECT set_entitlement_atomic('{\"user_id\":1,\"year\":2026,\"days\":1}','[]');")
            leave = dict(user_id=1, start_date='2026-11-09', end_date='2026-11-09', leave_type='Vacation',
                         comment='', filename='fictional.pdf', submitted_at='2026-11-02T12:00:00+00:00')
            queries = ["SET ROLE service_role;SELECT set_entitlement_atomic('{\"user_id\":1,\"year\":2026,\"days\":0}','[]');",
                       "SET ROLE service_role;SELECT submit_leave_atomic('" + json.dumps(leave) + "','[]');"]
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(sql, queries))
            assert sorted(result.returncode == 0 for result in results) == [False, True]
            assert all(result.returncode == 0 or 'WORKFLOW_CONFLICT:' in result.stderr for result in results)
            checked("INSERT INTO time_entries(user_id,work_date,project,task,minutes,billable,notes,created_at,updated_at) "
                    "VALUES(1,'2026-11-16','Fictional','Testing',390,0,'','now','now');")
            values.update(year=2026, month=11, working_days=1, total_hours=6.5,
                          validate_daily_hours_snapshot=True, minute_snapshot={'2026-11-16': 390})
            checked("SET ROLE service_role;UPDATE time_entries SET minutes=480 WHERE work_date='2026-11-16';")
            changed = sql("SET ROLE service_role;SELECT submit_timesheet_atomic('" + json.dumps(values) + "');")
            assert changed.returncode != 0 and 'Recorded hours changed' in changed.stderr
            values.update(total_hours=8, minute_snapshot={'2026-11-16':480})
            checked("SET ROLE service_role;SELECT submit_timesheet_atomic('" + json.dumps(values) + "');")
            # An edit racing the initial monthly submission must either win
            # before its snapshot check, or wait and fail on the period lock.
            checked("INSERT INTO time_entries(user_id,work_date,project,task,minutes,billable,notes,created_at,updated_at) "
                    "VALUES(1,'2026-12-14','Fictional','Testing',390,0,'','now','now');")
            values.update(month=12, total_hours=6.5, minute_snapshot={'2026-12-14':390})
            queries = ["SET ROLE service_role;UPDATE time_entries SET minutes=480 WHERE work_date='2026-12-14';",
                       "SET ROLE service_role;SELECT submit_timesheet_atomic('" + json.dumps(values) + "');"]
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(sql, queries))
            assert sorted(result.returncode == 0 for result in results) == [False, True]
            assert all(result.returncode == 0 or 'Recorded hours changed' in result.stderr or 'Timesheet period is locked' in result.stderr for result in results)
            assert checked("SET ROLE service_role;SELECT count(*) FROM timesheets t JOIN "
                "(SELECT user_id,SUM(minutes) AS minutes FROM time_entries WHERE work_date BETWEEN '2026-12-01' AND '2026-12-31' GROUP BY user_id) h "
                "ON t.user_id=h.user_id WHERE t.year=2026 AND t.month=12 AND t.total_hours*60 <> h.minutes;").strip().endswith('0')
        finally:
            subprocess.run(['pg_ctl', '-D', str(data), '-m', 'immediate', '-w', 'stop'], check=True, capture_output=True)
