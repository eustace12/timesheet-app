"""Optional office plans stay independent from payroll and attendance."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import getpass
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

import pytest

import db
import office_store


@pytest.fixture(autouse=True)
def schema():
    with db.connect() as conn:
        office_store.init_schema(conn)


def person():
    return db.get_user_by_username('employee')['id']


def save(**changes):
    fields = dict(user_id=person(), work_date='2026-11-09', planned=True)
    fields.update(changes)
    return office_store.save_plan(**fields)


def test_day_without_times_is_optional_and_visible_to_team():
    row = save()
    assert row['planned'] is True
    assert row['start_time'] is None and row['end_time'] is None
    assert office_store.plans(date(2026, 11, 9), date(2026, 11, 15))[0]['full_name'] == 'Test User'
    assert not db.list_user_timesheets(person())
    assert not db.list_user_leave_requests(person())


def test_interval_is_normalized_and_same_retry_does_not_change_version():
    row = save(start_time=' 09:00 ', end_time='18:30')
    repeated = save(start_time='09:00', end_time='18:30', expected_updated_at='')
    assert repeated == row
    assert row['start_time'] == '09:00'


@pytest.mark.parametrize('fields', [
    {'start_time': '09:00'}, {'end_time': '18:00'},
    {'start_time': '18:00', 'end_time': '09:00'},
    {'start_time': '09:00', 'end_time': '09:00'},
    {'start_time': '9:00', 'end_time': '18:00'},
    {'start_time': '09:60', 'end_time': '18:00'},
    {'start_time': '09:00', 'end_time': '24:00'},
    {'planned': 'true'}, {'planned': 1}, {'user_id': True},
    {'work_date': '2019-12-31'}, {'work_date': '2100-01-01'},
    {'work_date': '20261109'}, {'work_date': '2026-02-30'},
    {'expected_updated_at': None},
])
def test_invalid_plans_do_not_write_any_data(fields):
    with pytest.raises(ValueError):
        save(**fields)
    assert not office_store.own_plans(person(), date(2026, 11, 1), date(2026, 11, 30))


def test_stale_tabs_cannot_lose_changes_and_soft_unmark_preserves_undo_times():
    original = save(start_time='09:00', end_time='18:00')
    updated = save(start_time='10:00', end_time='19:00', expected_updated_at=original['updated_at'])
    with pytest.raises(db.WorkflowConflict):
        save(planned=False, expected_updated_at=original['updated_at'])
    inactive = save(planned=False, expected_updated_at=updated['updated_at'])
    assert inactive['planned'] is False and inactive['start_time'] == '10:00'
    assert office_store.plans(date(2026, 11, 9), date(2026, 11, 15)) == []
    assert office_store.own_plans(person(), date(2026, 11, 9), date(2026, 11, 15)) == [inactive]
    assert save(planned=False, expected_updated_at=updated['updated_at']) == inactive
    restored = save(start_time=inactive['start_time'], end_time=inactive['end_time'], expected_updated_at=inactive['updated_at'])
    assert restored['planned'] is True and restored['start_time'] == '10:00'


def test_concurrent_different_initial_plans_only_save_one():
    def create(start):
        try:
            save(start_time=start, end_time='18:00')
            return 'saved'
        except db.WorkflowConflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(create, ['09:00', '10:00'])) == ['conflict', 'saved']


def test_versions_remain_unique_when_server_clock_is_fixed_or_moves_backwards(monkeypatch):
    monkeypatch.setattr(db, '_now', lambda: '2026-11-02T12:00:00.000000+00:00')
    original = save()
    second = save(start_time='09:00', end_time='18:00', expected_updated_at=original['updated_at'])
    third = save(start_time='10:00', end_time='18:00', expected_updated_at=second['updated_at'])
    assert original['updated_at'] < second['updated_at'] < third['updated_at']
    with pytest.raises(db.WorkflowConflict):
        save(planned=False, expected_updated_at=original['updated_at'])


def test_clearing_optional_times_and_deleting_person_do_not_preserve_office_records():
    row = save(start_time='09:00', end_time='18:00')
    cleared = save(expected_updated_at=row['updated_at'])
    assert cleared['start_time'] is None and cleared['end_time'] is None
    assert db.delete_user(person())
    with db.connect() as conn:
        assert conn.execute('SELECT count(*) FROM office_plans').fetchone()[0] == 0


def test_query_ranges_are_bounded_and_inclusive():
    save(work_date='2026-11-01')
    save(work_date='2026-11-30')
    assert len(office_store.plans(date(2026, 11, 1), date(2026, 11, 30))) == 2
    for start, end in [(date(2026, 11, 1), date(2026, 12, 2)), (date(2026, 11, 2), date(2026, 11, 1))]:
        with pytest.raises(ValueError):
            office_store.plans(start, end)


@pytest.mark.skipif(not all(shutil.which(name) for name in ('initdb', 'pg_ctl', 'psql')),
                    reason='PostgreSQL binaries are required for RPC integration verification')
def test_office_rpc_in_disposable_postgres():
    """Exercise the real production migration and CAS under concurrent clients."""
    with tempfile.TemporaryDirectory(prefix='office-pg-') as temporary:
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

        def rpc(day='2026-11-09', planned=True, start='09:00', end='18:00', expected='', token='2026-11-02T12:00:00.000000+00:00'):
            def literal(value):
                return 'null' if value is None else "'" + str(value).replace("'", "''") + "'"
            args = ','.join(['1', literal(day), 'true' if planned else 'false', literal(start),
                            literal(end), literal(expected), literal(token)])
            return 'SET ROLE service_role;SELECT set_office_plan_atomic(' + args + ');'

        def row(query):
            return json.loads(succeeds(query).strip().splitlines()[-1])

        def conflict(query):
            result = sql(query)
            assert result.returncode != 0 and 'WORKFLOW_CONFLICT:' in result.stderr, result.stderr

        try:
            succeeds('CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role BYPASSRLS; '
                     'CREATE TABLE users(id bigint primary key, full_name text); '
                     "INSERT INTO users VALUES(1,'Fictional Employee'); "
                     'GRANT SELECT,UPDATE,DELETE ON users TO service_role;')
            migration = Path(__file__).resolve().parents[1] / 'supabase/migrations/20261102164908_office_plans.sql'
            succeeds(migration.read_text())
            original = row(rpc())
            assert row(rpc()) == original
            updated = row(rpc(start='10:00', expected=original['updated_at']))
            assert updated['updated_at'] != original['updated_at']
            conflict(rpc(planned=False, start=None, end=None, expected=original['updated_at']))
            inactive = row(rpc(planned=False, start=None, end=None, expected=updated['updated_at']))
            assert inactive['planned'] is False and inactive['start_time'] == '10:00'
            assert row(rpc(planned=False, start=None, end=None, expected=updated['updated_at'])) == inactive
            for query in [rpc(day='2019-12-31'), rpc(start='09:00', end=None), rpc(start='9:00'), rpc(start='18:00', end='09:00')]:
                conflict(query)
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(sql, [rpc(day='2026-11-10'), rpc(day='2026-11-10', start='10:00')]))
            assert sorted(result.returncode == 0 for result in results) == [False, True]
            assert succeeds('SET ROLE service_role;SELECT count(*) FROM office_plans;').strip().endswith('2')
            for role in ['anon', 'authenticated']:
                for query in ["SELECT set_office_plan_atomic(1,'2026-11-09',true,null,null,'','2026-11-02T12:00:00+00:00');", 'SELECT * FROM office_plans;']:
                    result = sql('SET ROLE ' + role + ';' + query)
                    assert result.returncode != 0 and 'permission denied' in result.stderr
            succeeds('SET ROLE service_role;DELETE FROM users WHERE id=1;')
            assert succeeds('SET ROLE service_role;SELECT count(*) FROM office_plans;').strip().endswith('0')
        finally:
            subprocess.run(['pg_ctl', '-D', str(data), '-m', 'immediate', '-w', 'stop'], check=True, capture_output=True)
