"""Workflow invariants must hold below route-level preflight checks."""
from concurrent.futures import ThreadPoolExecutor
import getpass
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile

import pytest

import db
import hr_store


def person():
    return db.get_user_by_username('employee')['id']


def sheet(**changes):
    values = dict(user_id=person(), year=2026, month=11, fmt='pdf',
        vacation='', sick='', custom_days='', custom_reason='', working_days=21,
        total_hours=168, filename='example.pdf')
    values.update(changes)
    return db.upsert_timesheet(**values)


def leave(start='2026-11-09', end='2026-11-10', **changes):
    values = dict(user_id=person(), start_date=start, end_date=end,
                  leave_type='Vacation', comment='', filename='leave.pdf')
    values.update(changes)
    return db.create_leave_request(**values)


def test_database_submission_guard_does_not_replace_pending_or_approved_month():
    original = sheet()
    with pytest.raises(db.WorkflowConflict):
        sheet()
    db.review_timesheet(original['id'], 'accepted', '')
    with pytest.raises(db.WorkflowConflict):
        sheet()
    assert db.get_timesheet(original['id'])['status'] == 'accepted'


def test_two_simultaneous_month_submissions_cannot_both_succeed():
    def submit(_):
        try:
            sheet()
            return 'saved'
        except db.WorkflowConflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(submit, range(2))) == ['conflict', 'saved']


def test_stale_review_cannot_override_a_returned_document_or_new_revision():
    row = sheet()
    db.review_timesheet(row['id'], 'needs_changes', 'Correct the dates.')
    with pytest.raises(db.WorkflowConflict):
        db.review_timesheet(row['id'], 'accepted', 'Stale decision')
    updated = sheet()
    with pytest.raises(db.WorkflowConflict):
        db.review_timesheet(updated['id'], 'accepted', '', expected_submitted_at='old-revision')
    assert db.get_timesheet(row['id'])['status'] == 'submitted'


def test_database_blocks_overlapping_leave_even_when_preflight_was_bypassed():
    first = leave()
    with pytest.raises(db.WorkflowConflict):
        leave('2026-11-10', '2026-11-11', leave_type='Unpaid')
    db.review_leave_request(first['id'], 'needs_changes', 'Choose other dates.')
    assert leave()['id'] != first['id']


def test_concurrent_overlapping_leave_only_creates_one_request():
    def submit(_):
        try:
            leave()
            return 'saved'
        except db.WorkflowConflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(submit, range(2))) == ['conflict', 'saved']


def test_allowance_is_enforced_for_each_year_and_cannot_be_reduced_below_reservations():
    hr_store.set_entitlement(person(), 2026, 1)
    with pytest.raises(db.WorkflowConflict):
        leave()
    row = leave('2026-11-09', '2026-11-09')
    with pytest.raises(db.WorkflowConflict):
        hr_store.set_entitlement(person(), 2026, 0)
    assert hr_store.leave_balance(person(), 2026)['available'] == 0
    db.review_leave_request(row['id'], 'needs_changes', 'Choose other dates.')
    hr_store.set_entitlement(person(), 2026, 0)
    assert hr_store.leave_balance(person(), 2026)['entitlement'] == 0


def test_leave_approval_cannot_reapply_an_old_returned_decision():
    row = leave()
    db.review_leave_request(row['id'], 'needs_changes', 'Choose other dates.')
    with pytest.raises(db.WorkflowConflict):
        db.review_leave_request(row['id'], 'accepted', '')


def test_approved_absences_keep_their_business_type():
    row = leave(leave_type='Unpaid')
    db.review_leave_request(row['id'], 'accepted', '')
    assert db.list_approved_leave_ranges(person(), 2026, 11)[0]['leave_type'] == 'Unpaid'


def test_submission_rechecks_approved_dates_after_document_generation():
    snapshot = []
    row = leave()
    db.review_leave_request(row['id'], 'accepted', '')
    with pytest.raises(db.WorkflowConflict, match='Approved leave changed'):
        sheet(leave_snapshot=snapshot)
    current = [dict(row) for row in db.list_approved_leave_ranges(person(), 2026, 11)]
    saved = sheet(leave_snapshot=current, vacation='9-10', total_hours=152)
    assert json.loads(saved['leave_snapshot']) == current


def test_review_history_survives_resubmission_and_reopening_and_cannot_be_changed():
    row = sheet(actor_user_id=person())
    reviewer = db.get_user_by_username('admin')['id']
    db.review_timesheet(row['id'], 'needs_changes', 'Correct the dates.', actor_user_id=reviewer)
    sheet()
    db.review_timesheet(row['id'], 'accepted', 'Approved.', actor_user_id=reviewer)
    db.reopen_timesheet(row['id'], 'Accounting correction.', actor_user_id=reviewer)
    events = db.list_workflow_events('timesheets', row['id'])
    assert [event['action'] for event in events] == ['submitted', 'reviewed', 'resubmitted', 'reviewed', 'reopened']
    assert events[1]['comment'] == 'Correct the dates.'
    assert events[1]['actor_user_id'] == reviewer
    with db.connect() as conn:
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            conn.execute('UPDATE workflow_events SET comment = ? WHERE id = ?', ('changed', events[0]['id']))
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            conn.execute('DELETE FROM workflow_events WHERE id = ?', (events[0]['id'],))


def test_rejected_operations_do_not_add_audit_events():
    row = sheet()
    before = db.list_workflow_events('timesheets', row['id'])
    with pytest.raises(db.WorkflowConflict):
        sheet()
    assert db.list_workflow_events('timesheets', row['id']) == before


@pytest.mark.skipif(not all(shutil.which(name) for name in ('initdb', 'pg_ctl', 'psql')),
                    reason='PostgreSQL binaries are required for RPC integration verification')
def test_production_rpc_guards_in_disposable_postgres():
    """Execute actual migrations and concurrent RPCs, without a cloud account."""
    with tempfile.TemporaryDirectory(prefix='people-pg-') as temporary:
        root = Path(temporary)
        data = root / 'pg'
        subprocess.run(['initdb', '-D', str(data), '-A', 'trust', '--no-locale'], check=True, capture_output=True)
        subprocess.run(['pg_ctl', '-D', str(data), '-l', str(root / 'postgres.log'),
                        '-o', f"-F -k {temporary} -h '' -p 55432", '-w', 'start'], check=True, capture_output=True)

        def sql(query):
            return subprocess.run(['psql', '-h', temporary, '-p', '55432', '-U', getpass.getuser(),
                                   '-d', 'postgres', '-v', 'ON_ERROR_STOP=1', '-At'],
                                  input=query, text=True, capture_output=True)

        def succeeds(query):
            result = sql(query)
            assert result.returncode == 0, result.stderr
            return result.stdout

        def conflicts(query):
            result = sql(query)
            assert result.returncode != 0 and 'WORKFLOW_CONFLICT:' in result.stderr, result.stderr

        try:
            succeeds('CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role BYPASSRLS; '
                     'CREATE SCHEMA storage; CREATE TABLE storage.buckets(id text primary key, name text, public boolean);')
            repository = Path(__file__).resolve().parents[1]
            for name in ('supabase/schema.sql', 'supabase/hr_workspace.sql',
                         'supabase/migrations/20261102160704_workflow_integrity.sql'):
                succeeds((repository / name).read_text())
            succeeds("INSERT INTO users(username,password_hash,full_name,role,created_at) "
                     "VALUES('test','','Fictional Tester','employee','2026-11-02'); "
                     'GRANT ALL ON ALL TABLES IN SCHEMA public TO service_role; '
                     'GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO service_role;')
            values = dict(user_id=1, year=2026, month=11, format='pdf', vacation='', sick='',
                          custom_days='', custom_reason='', working_days=21, total_hours=168,
                          filename='test.pdf', storage_key=None, leave_snapshot=[],
                          validate_leave_snapshot=True, submitted_at='2026-11-02T12:00:00+00:00')
            query = "SET ROLE service_role;SELECT submit_timesheet_atomic('" + json.dumps(values) + "'::jsonb);"
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(sql, [query, query]))
            assert sorted(result.returncode == 0 for result in results) == [False, True]
            succeeds("SET ROLE service_role;SELECT review_document_atomic('timesheets',1,'needs_changes','Fix dates','now');")
            conflicts("SET ROLE service_role;SELECT review_document_atomic('timesheets',1,'accepted','','now');")
            succeeds("SET ROLE service_role;SELECT set_entitlement_atomic('{\"user_id\":1,\"year\":2026,\"days\":1}','[]');")
            request = dict(user_id=1, start_date='2026-11-09', end_date='2026-11-10',
                           leave_type='Vacation', comment='', filename='leave.pdf', submitted_at='now')
            query = "SET ROLE service_role;SELECT submit_leave_atomic('" + json.dumps(request) + "'::jsonb,'[]');"
            conflicts(query)
            succeeds("SET ROLE service_role;SELECT set_entitlement_atomic('{\"user_id\":1,\"year\":2026,\"days\":20}','[]');")
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(sql, [query, query]))
            assert sorted(result.returncode == 0 for result in results) == [False, True]
            succeeds("SET ROLE service_role;SELECT review_document_atomic('leave_requests',1,'accepted','','now');")
            conflicts("SET ROLE service_role;SELECT submit_timesheet_atomic('" + json.dumps(values) + "'::jsonb);")
            result = sql("SET ROLE anon;SELECT submit_timesheet_atomic('{}');")
            assert result.returncode != 0 and 'permission denied' in result.stderr
            assert succeeds('SET ROLE service_role;SELECT count(*) FROM workflow_events;').strip().endswith('6')
            for statement in ("UPDATE workflow_events SET comment = 'changed'", 'DELETE FROM workflow_events', 'TRUNCATE workflow_events'):
                result = sql('SET ROLE service_role;' + statement)
                assert result.returncode != 0 and 'immutable' in result.stderr
        finally:
            subprocess.run(['pg_ctl', '-D', str(data), '-m', 'immediate', '-w', 'stop'], check=True, capture_output=True)
