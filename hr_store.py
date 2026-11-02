"""Time logs and configurable leave entitlements for the People workspace."""
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
import calendar
import sqlite3

import db
from moldova_holidays import get_moldova_holidays


SCHEMA = """
CREATE TABLE IF NOT EXISTS time_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    work_date TEXT NOT NULL,
    project TEXT NOT NULL,
    task TEXT NOT NULL,
    minutes INTEGER NOT NULL CHECK(minutes BETWEEN 1 AND 1440),
    billable INTEGER NOT NULL DEFAULT 0 CHECK(billable IN (0, 1)),
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS time_entries_user_date_idx ON time_entries(user_id, work_date);
CREATE TABLE IF NOT EXISTS leave_entitlements (
    user_id INTEGER NOT NULL REFERENCES users(id),
    year INTEGER NOT NULL CHECK(year BETWEEN 1 AND 9999),
    days REAL NOT NULL CHECK(days BETWEEN 0 AND 366),
    PRIMARY KEY(user_id, year)
);
"""


def init_schema(conn):
    conn.executescript(SCHEMA)
    for operation in ('INSERT', 'UPDATE', 'DELETE'):
        row = 'OLD' if operation == 'DELETE' else 'NEW'
        conditions = f"""
        SELECT RAISE(ABORT, 'Timesheet period is locked') WHERE EXISTS (
            SELECT 1 FROM timesheets WHERE user_id = {row}.user_id
            AND year = CAST(substr({row}.work_date, 1, 4) AS INTEGER)
            AND month = CAST(substr({row}.work_date, 6, 2) AS INTEGER)
            AND status IN ('submitted', 'accepted')
        );
        """
        if operation == 'UPDATE':
            conditions += """
            SELECT RAISE(ABORT, 'Timesheet period is locked') WHERE EXISTS (
                SELECT 1 FROM timesheets WHERE user_id = OLD.user_id
                AND year = CAST(substr(OLD.work_date, 1, 4) AS INTEGER)
                AND month = CAST(substr(OLD.work_date, 6, 2) AS INTEGER)
                AND status IN ('submitted', 'accepted')
            );
            """
        if operation != 'DELETE':
            conditions += f"""
            SELECT RAISE(ABORT, 'Daily total cannot exceed 24 hours') WHERE (
                SELECT COALESCE(SUM(minutes), 0) FROM time_entries
                WHERE user_id = NEW.user_id AND work_date = NEW.work_date
                {'AND id != OLD.id' if operation == 'UPDATE' else ''}
            ) + NEW.minutes > 1440;
            SELECT RAISE(ABORT, 'This date has approved leave') WHERE EXISTS (
                SELECT 1 FROM leave_requests WHERE user_id = NEW.user_id
                AND start_date <= NEW.work_date AND end_date >= NEW.work_date
                AND status = 'accepted'
            );
            """
        conn.executescript(f"""
        DROP TRIGGER IF EXISTS time_entries_guard_{operation.lower()};
        CREATE TRIGGER time_entries_guard_{operation.lower()}
        BEFORE {operation} ON time_entries BEGIN {conditions} END;
        """)


def parse_minutes(raw):
    raw = str(raw or '').strip()
    try:
        if ':' in raw:
            hours, minutes = raw.split(':')
            if not hours.isdigit() or not minutes.isdigit() or not 0 <= int(minutes) < 60:
                raise ValueError
            total = Decimal(int(hours) * 60 + int(minutes))
        else:
            total = Decimal(raw) * 60
        if not total.is_finite() or total != total.to_integral_value() or not 1 <= total <= 1440:
            raise ValueError
        return int(total)
    except (InvalidOperation, ValueError):
        raise ValueError('Enter hours between 0:01 and 24:00, using HH:MM or decimal hours.') from None


def period_bounds(year, month):
    return date(year, month, 1).isoformat(), date(year, month, calendar.monthrange(year, month)[1]).isoformat()


def entries(user_id, year, month):
    start, end = period_bounds(year, month)
    if db._using_supabase():
        return db._remote_rows(db._client().table('time_entries').select('*').eq('user_id', user_id)
                               .gte('work_date', start).lte('work_date', end).order('work_date').order('id').execute())
    with db.connect() as conn:
        return [dict(row) for row in conn.execute(
            'SELECT * FROM time_entries WHERE user_id = ? AND work_date BETWEEN ? AND ? ORDER BY work_date, id',
            (user_id, start, end))]


def save_entry(user_id, form, entry_id=None):
    work_date = date.fromisoformat(form.get('work_date', '')).isoformat()
    project = form.get('project', '').strip()
    task = form.get('task', '').strip()
    notes = form.get('notes', '').strip()
    if not project or not task or len(project) > 160 or len(task) > 240 or len(notes) > 2000:
        raise ValueError('Project and task are required; keep notes within 2000 characters.')
    values = {'user_id': user_id, 'work_date': work_date, 'project': project, 'task': task,
              'minutes': parse_minutes(form.get('hours')), 'billable': int(form.get('billable') == '1'),
              'notes': notes, 'updated_at': db._now()}
    if db._using_supabase():
        try:
            query = db._client().table('time_entries')
            if entry_id is None:
                values['created_at'] = db._now()
                result = query.insert(values).execute()
            else:
                result = query.update(values).eq('id', entry_id).eq('user_id', user_id).execute()
            row = db._remote_one(result)
            if row is None:
                raise LookupError('Time log not found')
            return row
        except LookupError:
            raise
        except Exception as exc:
            message = str(exc)
            for allowed in ('Timesheet period is locked', 'Daily total cannot exceed 24 hours', 'This date has approved leave'):
                if allowed in message:
                    raise ValueError(allowed) from None
            raise ValueError('Unable to save time log.') from None
    try:
        with db.connect() as conn:
            if entry_id is None:
                values['created_at'] = db._now()
                columns = ', '.join(values)
                placeholders = ', '.join('?' for _ in values)
                cursor = conn.execute(f'INSERT INTO time_entries ({columns}) VALUES ({placeholders})', tuple(values.values()))
                entry_id = cursor.lastrowid
            else:
                assignments = ', '.join(f'{key} = ?' for key in values)
                if not conn.execute(f'UPDATE time_entries SET {assignments} WHERE id = ? AND user_id = ?',
                                    (*values.values(), entry_id, user_id)).rowcount:
                    raise LookupError('Time log not found')
            return dict(conn.execute('SELECT * FROM time_entries WHERE id = ?', (entry_id,)).fetchone())
    except sqlite3.IntegrityError as exc:
        raise ValueError(str(exc)) from None


def delete_entry(user_id, entry_id):
    if db._using_supabase():
        try:
            result = db._client().table('time_entries').delete().eq('id', entry_id).eq('user_id', user_id).execute()
            if not db._remote_rows(result):
                raise LookupError('Time log not found')
        except LookupError:
            raise
        except Exception as exc:
            if 'Timesheet period is locked' in str(exc):
                raise ValueError('Timesheet period is locked') from None
            raise ValueError('Unable to delete time log.') from None
        return
    try:
        with db.connect() as conn:
            if not conn.execute('DELETE FROM time_entries WHERE id = ? AND user_id = ?', (entry_id, user_id)).rowcount:
                raise LookupError('Time log not found')
    except sqlite3.IntegrityError as exc:
        raise ValueError(str(exc)) from None


def has_entries(user_id):
    if db._using_supabase():
        return bool(db._remote_rows(db._client().table('time_entries').select('id').eq('user_id', user_id).limit(1).execute()))
    with db.connect() as conn:
        return conn.execute('SELECT 1 FROM time_entries WHERE user_id = ? LIMIT 1', (user_id,)).fetchone() is not None


def daily_hours(user_id, year, month):
    result = {}
    for row in entries(user_id, year, month):
        day = date.fromisoformat(row['work_date'])
        result[day] = result.get(day, 0) + row['minutes'] / 60
    return result


def set_entitlement(user_id, year, raw, actor_user_id=None):
    try:
        days = Decimal(str(raw))
        if not days.is_finite() or not 0 <= days <= 366 or days * 2 != (days * 2).to_integral_value():
            raise ValueError
        date(year, 1, 1)
    except (InvalidOperation, ValueError):
        raise ValueError('Enter an annual allowance from 0 to 366 in half-day increments.') from None
    values = {'user_id': user_id, 'year': year, 'days': float(days)}
    if db._using_supabase():
        db._workflow_rpc('set_entitlement_atomic', {'p_values': {**values, 'actor_user_id': actor_user_id}, 'p_holidays': db._nonworking_dates([year])})
    else:
        with db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            reserved = set()
            for row in conn.execute("SELECT start_date, end_date FROM leave_requests WHERE user_id = ? AND leave_type = 'Vacation' AND status IN ('submitted','accepted')", (user_id,)):
                reserved |= db.working_dates(max(date.fromisoformat(row['start_date']), date(year, 1, 1)),
                                             min(date.fromisoformat(row['end_date']), date(year, 12, 31)))
            if len(reserved) > days:
                raise db.WorkflowConflict('The allowance cannot be lower than approved and pending annual leave.')
            conn.execute('INSERT INTO leave_entitlements (user_id, year, days) VALUES (?, ?, ?) '
                         'ON CONFLICT(user_id, year) DO UPDATE SET days = excluded.days', tuple(values.values()))
            db.record_workflow_event(conn, 'entitlement', year, user_id, 'allowance_updated', 'updated',
                                     f'Annual allowance: {float(days):g} days', actor_user_id)


def leave_balance(user_id, year):
    if db._using_supabase():
        entitlement = db._remote_one(db._client().table('leave_entitlements').select('days')
                                    .eq('user_id', user_id).eq('year', year).execute())
    else:
        with db.connect() as conn:
            entitlement = conn.execute('SELECT days FROM leave_entitlements WHERE user_id = ? AND year = ?', (user_id, year)).fetchone()
    used, pending = set(), set()
    holidays = get_moldova_holidays(year)
    for row in db.list_user_leave_requests(user_id):
        if row['leave_type'] != 'Vacation' or row['status'] not in ('accepted', 'submitted'):
            continue
        day = max(date.fromisoformat(row['start_date']), date(year, 1, 1))
        end = min(date.fromisoformat(row['end_date']), date(year, 12, 31))
        target = used if row['status'] == 'accepted' else pending
        while day <= end:
            if day.weekday() < 5 and day not in holidays:
                target.add(day)
            if day == end:
                break
            day += timedelta(days=1)
    pending -= used
    allowance = float(entitlement['days']) if entitlement else None
    return {'entitlement': allowance, 'used': len(used), 'pending': len(pending),
            'available': allowance - len(used) - len(pending) if allowance is not None else None}
