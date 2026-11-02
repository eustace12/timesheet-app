"""Optional office intentions; independent of attendance, leave and payroll."""
from datetime import date, datetime, timedelta
import re

import db


SCHEMA = """
CREATE TABLE IF NOT EXISTS office_plans (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    work_date TEXT NOT NULL CHECK(work_date BETWEEN '2020-01-01' AND '2099-12-31'),
    planned INTEGER NOT NULL CHECK(planned IN (0, 1)),
    start_time TEXT,
    end_time TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(user_id, work_date),
    CHECK((start_time IS NULL AND end_time IS NULL) OR
          (start_time IS NOT NULL AND end_time IS NOT NULL AND end_time > start_time))
);
CREATE INDEX IF NOT EXISTS office_plans_date_idx ON office_plans(work_date, planned);
"""


def init_schema(conn):
    conn.executescript(SCHEMA)


def _date(value):
    if isinstance(value, datetime):
        raise ValueError('Choose an office date without a time.')
    if isinstance(value, str):
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError('Choose a valid office date.')
        try:
            value = date.fromisoformat(value)
        except ValueError:
            raise ValueError('Choose a valid office date.') from None
    if not isinstance(value, date) or not 2020 <= value.year <= 2099:
        raise ValueError('Choose an office date between 2020 and 2099.')
    return value


def _range(start, end):
    start, end = _date(start), _date(end)
    if not 0 <= (end - start).days < 31:
        raise ValueError('Choose a date range of up to 31 days.')
    return start.isoformat(), end.isoformat()


def _times(start, end):
    def normalize(value):
        if value is None or value == '':
            return None
        if not isinstance(value, str):
            raise ValueError('Enter arrival and departure as HH:MM.')
        value = value.strip()
        if not value:
            return None
        if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value):
            raise ValueError('Enter arrival and departure as HH:MM.')
        return value
    start, end = normalize(start), normalize(end)
    if (start is None) != (end is None):
        raise ValueError('Set both arrival and departure, or leave both empty.')
    if start is not None and end <= start:
        raise ValueError('Departure must be later than arrival on the same day.')
    return start, end


def _row(row):
    return {'user_id': row['user_id'], 'work_date': str(row['work_date']),
            'planned': bool(row['planned']), 'start_time': row['start_time'],
            'end_time': row['end_time'], 'updated_at': row['updated_at']}


def plans(start: date, end: date):
    """Team plans in a bounded inclusive range; no inactive rows are exposed."""
    start, end = _range(start, end)
    if db._using_supabase():
        rows = db._remote_rows(db._client().table('office_plans').select('*')
                               .eq('planned', True).gte('work_date', start).lte('work_date', end).execute())
        rows = db._with_user_details(rows)
    else:
        with db.connect() as conn:
            rows = conn.execute('SELECT o.*, u.full_name FROM office_plans o JOIN users u ON u.id = o.user_id '
                                'WHERE o.planned = 1 AND o.work_date BETWEEN ? AND ?', (start, end)).fetchall()
    return sorted(({**_row(row), 'full_name': row['full_name']} for row in rows),
                  key=lambda row: (row['work_date'], row['full_name'], row['user_id']))


def own_plans(user_id, start: date, end: date):
    """Retain inactive rows and their versions, allowing safe undo and retries."""
    start, end = _range(start, end)
    if db._using_supabase():
        rows = db._remote_rows(db._client().table('office_plans').select('*').eq('user_id', user_id)
                               .gte('work_date', start).lte('work_date', end).order('work_date').execute())
    else:
        with db.connect() as conn:
            rows = conn.execute('SELECT * FROM office_plans WHERE user_id = ? AND work_date BETWEEN ? AND ? '
                                'ORDER BY work_date', (user_id, start, end)).fetchall()
    return [_row(row) for row in rows]


def save_plan(user_id, work_date, planned, start_time=None, end_time=None, expected_updated_at=''):
    """Compare-and-swap a day; identical retries return the existing version."""
    if type(user_id) is not int or user_id <= 0:
        raise ValueError('Choose a valid employee.')
    work_date = _date(work_date).isoformat()
    if type(planned) is not bool:
        raise ValueError('Choose whether this day is planned.')
    start_time, end_time = _times(start_time, end_time)
    if not isinstance(expected_updated_at, str) or len(expected_updated_at) > 100:
        raise ValueError('Include the version of this office plan.')
    updated_at = db._now()
    if db._using_supabase():
        return _row(db._workflow_rpc('set_office_plan_atomic', {
            'p_user_id': user_id, 'p_work_date': work_date, 'p_planned': planned,
            'p_start_time': start_time, 'p_end_time': end_time,
            'p_expected_updated_at': expected_updated_at, 'p_updated_at': updated_at,
        }))
    with db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        if conn.execute('SELECT id FROM users WHERE id = ?', (user_id,)).fetchone() is None:
            raise ValueError('This employee no longer exists.')
        current = conn.execute('SELECT * FROM office_plans WHERE user_id = ? AND work_date = ?',
                               (user_id, work_date)).fetchone()
        if current:
            if not planned and start_time is None:
                start_time, end_time = current['start_time'], current['end_time']
            if (bool(current['planned']), current['start_time'], current['end_time']) == (planned, start_time, end_time):
                return _row(current)
            if current['updated_at'] != expected_updated_at:
                raise db.WorkflowConflict('This office plan changed in another tab. Refresh this week and try again.')
            if datetime.fromisoformat(updated_at) <= datetime.fromisoformat(current['updated_at']):
                updated_at = (datetime.fromisoformat(current['updated_at']) + timedelta(microseconds=1)).isoformat(timespec='microseconds')
        elif expected_updated_at:
            raise db.WorkflowConflict('This office plan changed in another tab. Refresh this week and try again.')
        conn.execute('INSERT INTO office_plans(user_id,work_date,planned,start_time,end_time,updated_at) VALUES(?,?,?,?,?,?) '
                     'ON CONFLICT(user_id,work_date) DO UPDATE SET planned=excluded.planned,start_time=excluded.start_time,'
                     'end_time=excluded.end_time,updated_at=excluded.updated_at',
                     (user_id, work_date, int(planned), start_time, end_time, updated_at))
        return _row(conn.execute('SELECT * FROM office_plans WHERE user_id = ? AND work_date = ?',
                                 (user_id, work_date)).fetchone())
