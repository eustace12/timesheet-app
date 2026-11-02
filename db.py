"""SQLite or Supabase persistence for users, timesheets, and leave requests."""

from __future__ import annotations

import sqlite3
import os
import json
import calendar
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


_db_path: Path | None = None
_supabase_client: Any | None = None


class WorkflowConflict(ValueError):
    """The current persisted state no longer permits this operation."""


def _workflow_rpc(name, payload):
    """Atomic production operations; never fall back to non-atomic REST writes."""
    try:
        return _remote_one(_client().rpc(name, payload).execute())
    except Exception as exc:
        message = str(exc)
        if 'WORKFLOW_CONFLICT:' in message:
            detail = message.split('WORKFLOW_CONFLICT:', 1)[1].split('"', 1)[0].split("'", 1)[0].strip()
            raise WorkflowConflict(detail or 'This request changed. Refresh and try again.') from None
        raise


def working_dates(start, end):
    """National-calendar working dates, shared by reservations and validation."""
    from moldova_holidays import get_moldova_holidays
    holidays = {year: get_moldova_holidays(year) for year in range(start.year, end.year + 1)}
    result = set()
    day = start
    while day <= end:
        if day.weekday() < 5 and day not in holidays[day.year]:
            result.add(day)
        if day == end:
            break
        day += timedelta(days=1)
    return result


def _nonworking_dates(years):
    from moldova_holidays import get_moldova_holidays
    return [day.isoformat() for year in years for day in get_moldova_holidays(year)]


def _validate_leave_dates(start_date, end_date, leave_type):
    start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
    if leave_type not in {'Vacation', 'Unpaid', 'Other'} or end < start or (end - start).days > 366:
        raise ValueError('Choose a valid leave type and a date range of up to one year.')
    if leave_type == 'Vacation' and not working_dates(start, end):
        raise ValueError('Annual leave must include at least one working day.')
    return start, end


def _validate_leave_reservation(conn, user_id, start_date, end_date, leave_type, request_id=None):
    start, end = _validate_leave_dates(start_date, end_date, leave_type)
    rows = conn.execute("SELECT * FROM leave_requests WHERE user_id = ? AND status IN ('submitted','accepted') AND id != ?",
                        (user_id, request_id or -1)).fetchall()
    if any(row['start_date'] <= end_date and row['end_date'] >= start_date for row in rows):
        raise WorkflowConflict('These dates overlap one of your existing requests.')
    if leave_type == 'Vacation':
        for year in range(start.year, end.year + 1):
            entitlement = conn.execute('SELECT days FROM leave_entitlements WHERE user_id = ? AND year = ?', (user_id, year)).fetchone()
            if entitlement is None:
                continue
            year_start, year_end = date(year, 1, 1), date(year, 12, 31)
            reserved = working_dates(max(start, year_start), min(end, year_end))
            for row in rows:
                if row['leave_type'] == 'Vacation':
                    reserved |= working_dates(max(date.fromisoformat(row['start_date']), year_start), min(date.fromisoformat(row['end_date']), year_end))
            if len(reserved) > entitlement['days']:
                raise WorkflowConflict(f'Annual leave exceeds the available allowance for {year}.')


def is_supabase_configured() -> bool:
    """Return whether both server-side Supabase credentials are available."""
    return bool(
        os.environ.get("SUPABASE_URL")
        and os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    )


def _using_supabase() -> bool:
    url_set = bool(os.environ.get("SUPABASE_URL"))
    key_set = bool(os.environ.get("SUPABASE_SERVICE_ROLE_KEY"))
    if url_set != key_set:
        raise RuntimeError(
            "Both SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be configured together"
        )
    return url_set


def _client():
    """Create the official Supabase client lazily for production requests."""
    global _supabase_client
    if not _using_supabase():
        raise RuntimeError("Supabase is not configured")
    if _supabase_client is None:
        try:
            from supabase import create_client
        except ImportError as exc:
            raise RuntimeError(
                "Supabase is configured but the 'supabase' package is not installed"
            ) from exc
        _supabase_client = create_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_SERVICE_ROLE_KEY"],
        )
    return _supabase_client


def _remote_rows(response: Any) -> list[dict[str, Any]]:
    data = getattr(response, "data", None)
    if not data:
        return []
    return data if isinstance(data, list) else [data]


def _remote_one(response: Any) -> dict[str, Any] | None:
    rows = _remote_rows(response)
    return rows[0] if rows else None


def _with_user_details(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach names in a single users request, avoiding per-row lookups."""
    if not rows:
        return rows
    user_ids = list({row["user_id"] for row in rows})
    users = _remote_rows(
        _client().table("users").select("id,full_name,username").in_("id", user_ids).execute()
    )
    details = {user["id"]: user for user in users}
    return [
        {
            **row,
            "full_name": details.get(row["user_id"], {}).get("full_name", ""),
            "username": details.get(row["user_id"], {}).get("username", ""),
        }
        for row in rows
    ]

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    full_name TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('employee', 'admin')),
    company_name TEXT NOT NULL DEFAULT 'Urchin Systems SA',
    hours_per_day INTEGER NOT NULL DEFAULT 8,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS timesheets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    year INTEGER NOT NULL,
    month INTEGER NOT NULL,
    format TEXT NOT NULL,
    vacation TEXT NOT NULL DEFAULT '',
    sick TEXT NOT NULL DEFAULT '',
    custom_days TEXT NOT NULL DEFAULT '',
    custom_reason TEXT NOT NULL DEFAULT '',
    working_days INTEGER NOT NULL,
    total_hours INTEGER NOT NULL,
    filename TEXT NOT NULL,
    storage_key TEXT,
    leave_snapshot TEXT NOT NULL DEFAULT '[]',
    submitted_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'submitted'
        CHECK(status IN ('submitted', 'accepted', 'needs_changes')),
    review_comment TEXT NOT NULL DEFAULT '',
    reviewed_at TEXT,
    UNIQUE(user_id, year, month)
);

CREATE TABLE IF NOT EXISTS leave_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    leave_type TEXT NOT NULL,
    comment TEXT NOT NULL DEFAULT '',
    filename TEXT NOT NULL,
    storage_key TEXT,
    submitted_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'submitted'
        CHECK(status IN ('submitted', 'accepted', 'needs_changes')),
    review_comment TEXT NOT NULL DEFAULT '',
    reviewed_at TEXT
);
CREATE TABLE IF NOT EXISTS workflow_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    document_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL REFERENCES users(id),
    actor_user_id INTEGER REFERENCES users(id),
    action TEXT NOT NULL,
    status TEXT NOT NULL,
    comment TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS workflow_events_document_idx ON workflow_events(kind, document_id, id);
CREATE TRIGGER IF NOT EXISTS workflow_events_no_update BEFORE UPDATE ON workflow_events
BEGIN SELECT RAISE(ABORT, 'Workflow history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS workflow_events_no_delete BEFORE DELETE ON workflow_events
BEGIN SELECT RAISE(ABORT, 'Workflow history is immutable'); END;
"""

DEMO_USERS = (
    {
        "username": "admin",
        "full_name": "Accountant",
        "role": "admin",
    },
    {
        "username": "employee",
        "full_name": "Test User",
        "role": "employee",
    },
    {
        "username": "stanislav",
        "full_name": "Iuntevici Stanislav",
        "role": "employee",
    },
)


def configure(path: str | Path) -> None:
    global _db_path
    if _using_supabase():
        return
    _db_path = Path(path)
    _db_path.parent.mkdir(parents=True, exist_ok=True)


def connect() -> sqlite3.Connection:
    if _db_path is None:
        raise RuntimeError("Database is not configured")
    conn = sqlite3.connect(_db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    if _using_supabase():
        return
    with connect() as conn:
        conn.executescript(SCHEMA)
        _migrate_review_columns(conn)
        import hr_store
        hr_store.init_schema(conn)
        import office_store
        office_store.init_schema(conn)
        import account_sessions
        account_sessions.init_schema(conn)


def _migrate_review_columns(conn: sqlite3.Connection) -> None:
    """Add review workflow fields to databases created before this feature."""
    for table in ("timesheets", "leave_requests"):
        columns = {
            row["name"] for row in conn.execute(f"PRAGMA table_info({table})")
        }
        if "status" not in columns:
            conn.execute(
                f"""
                ALTER TABLE {table}
                ADD COLUMN status TEXT NOT NULL DEFAULT 'submitted'
                    CHECK(status IN ('submitted', 'accepted', 'needs_changes'))
                """
            )
        if "review_comment" not in columns:
            conn.execute(
                f"""
                ALTER TABLE {table}
                ADD COLUMN review_comment TEXT NOT NULL DEFAULT ''
                """
            )
        if "reviewed_at" not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN reviewed_at TEXT")
        if "storage_key" not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN storage_key TEXT")
        if table == 'timesheets' and 'leave_snapshot' not in columns:
            conn.execute("ALTER TABLE timesheets ADD COLUMN leave_snapshot TEXT NOT NULL DEFAULT '[]'")


def seed_if_empty() -> None:
    if _using_supabase() or os.environ.get("DEMO_MODE") != "1":
        return
    with connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if n:
            return
        now = _now()
        for user in DEMO_USERS:
            conn.execute(
                """
                INSERT INTO users (username, password_hash, full_name, role, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    user["username"],
                    "!passwordless",
                    user["full_name"],
                    user["role"],
                    now,
                ),
            )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def record_workflow_event(conn, kind, document_id, user_id, action, status, comment='', actor_user_id=None):
    conn.execute('INSERT INTO workflow_events(kind,document_id,user_id,actor_user_id,action,status,comment,created_at) VALUES(?,?,?,?,?,?,?,?)',
                 (kind, document_id, user_id, actor_user_id, action, status, comment, _now()))


def list_workflow_events(kind, document_id, user_id=None):
    if kind not in {'timesheets', 'leave_requests', 'entitlement'}:
        raise ValueError('Unknown document kind.')
    if _using_supabase():
        query = _client().table('workflow_events').select('*').eq('kind', kind).eq('document_id', document_id)
        if user_id is not None:
            query = query.eq('user_id', user_id)
        return _remote_rows(query.order('id').execute())
    with connect() as conn:
        query = 'SELECT * FROM workflow_events WHERE kind = ? AND document_id = ?'
        values = [kind, document_id]
        if user_id is not None:
            query += ' AND user_id = ?'
            values.append(user_id)
        return [dict(row) for row in conn.execute(query + ' ORDER BY id', values)]


def list_recent_workflow_events(limit=100):
    limit = max(1, min(int(limit), 200))
    if _using_supabase():
        return _remote_rows(_client().table('workflow_events').select('*')
                            .order('created_at', desc=True).order('id', desc=True).limit(limit).execute())
    with connect() as conn:
        return [dict(row) for row in conn.execute(
            'SELECT * FROM workflow_events ORDER BY created_at DESC, id DESC LIMIT ?', (limit,))]


def get_user_by_id(user_id: int) -> sqlite3.Row | None:
    if _using_supabase():
        return _remote_one(
            _client().table("users").select("*").eq("id", user_id).execute()
        )
    with connect() as conn:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def get_user_by_username(username: str) -> sqlite3.Row | None:
    username = username.strip().lower()
    if _using_supabase():
        return _remote_one(
            _client().table("users").select("*").eq("username", username).execute()
        )
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()


def list_users() -> list[sqlite3.Row]:
    if _using_supabase():
        return _remote_rows(
            _client()
            .table("users")
            .select("id,username,full_name,role,created_at")
            .order("role")
            .order("full_name")
            .execute()
        )
    with connect() as conn:
        return conn.execute(
            "SELECT id, username, full_name, role, created_at FROM users ORDER BY role, full_name"
        ).fetchall()


def list_employees() -> list[sqlite3.Row]:
    if _using_supabase():
        return _remote_rows(
            _client()
            .table("users")
            .select("id,username,full_name")
            .eq("role", "employee")
            .order("full_name")
            .execute()
        )
    with connect() as conn:
        return conn.execute(
            "SELECT id, username, full_name FROM users WHERE role = 'employee' ORDER BY full_name"
        ).fetchall()


def create_user(username: str, full_name: str, role: str) -> sqlite3.Row:
    username = username.strip().lower()
    full_name = full_name.strip()
    role = role.strip()
    if role not in ("employee", "admin"):
        raise ValueError("Role must be employee or admin")
    if not username or not full_name:
        raise ValueError("Username and full name are required")
    # Retain the legacy non-null column without creating a usable credential.
    password_hash = "!passwordless"
    if _using_supabase():
        if get_user_by_username(username) is not None:
            raise ValueError("Username already exists")
        return _remote_one(
            _client()
            .table("users")
            .insert(
                {
                    "username": username,
                    "password_hash": password_hash,
                    "full_name": full_name,
                    "role": role,
                    "created_at": _now(),
                }
            )
            .execute()
        )
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO users (username, password_hash, full_name, role, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (username, password_hash, full_name, role, _now()),
        )
        return conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()


def update_user(
    user_id: int, username: str, full_name: str, role: str
) -> sqlite3.Row | None:
    """Update a person without changing legacy stored credentials."""
    username = username.strip().lower()
    full_name = full_name.strip()
    role = role.strip()
    if role not in ("employee", "admin"):
        raise ValueError("Role must be employee or admin")
    if not username or not full_name:
        raise ValueError("Username and full name are required")

    existing = get_user_by_id(user_id)
    if existing is None:
        return None
    if existing["role"] == "admin" and role != "admin" and not _has_another_admin(user_id):
        raise ValueError("At least one administrator account is required.")
    username_owner = get_user_by_username(username)
    if username_owner is not None and username_owner["id"] != user_id:
        raise ValueError("Username already exists")

    updates = {"username": username, "full_name": full_name, "role": role}
    if _using_supabase():
        _client().table("users").update(updates).eq("id", user_id).execute()
        return get_user_by_id(user_id)

    with connect() as conn:
        assignments = ", ".join(f"{field} = ?" for field in updates)
        conn.execute(
            f"UPDATE users SET {assignments} WHERE id = ?",
            (*updates.values(), user_id),
        )
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def delete_user(user_id: int) -> bool:
    """Delete a user who has no submitted documents."""
    user = get_user_by_id(user_id)
    if user is None:
        return False
    import hr_store
    if hr_store.has_entries(user_id):
        raise ValueError("This person has time logs and cannot be deleted.")
    if user["role"] == "admin" and not _has_another_admin(user_id):
        raise ValueError("At least one administrator account is required.")
    if _using_supabase():
        events = _remote_rows(_client().table('workflow_events').select('id')
                              .or_(f'user_id.eq.{user_id},actor_user_id.eq.{user_id}').limit(1).execute())
        if events:
            raise ValueError('This person has accounting history and cannot be deleted.')
        timesheets = _remote_rows(
            _client().table("timesheets").select("id").eq("user_id", user_id).limit(1).execute()
        )
        leave_requests = _remote_rows(
            _client().table("leave_requests").select("id").eq("user_id", user_id).limit(1).execute()
        )
        if timesheets or leave_requests:
            raise ValueError(
                "This user has submitted documents and cannot be deleted from the archive."
            )
        _client().table("users").delete().eq("id", user_id).execute()
        return True

    with connect() as conn:
        if conn.execute('SELECT 1 FROM workflow_events WHERE user_id = ? OR actor_user_id = ? LIMIT 1', (user_id, user_id)).fetchone():
            raise ValueError('This person has accounting history and cannot be deleted.')
        has_documents = conn.execute(
            """
            SELECT EXISTS(SELECT 1 FROM timesheets WHERE user_id = ?)
                OR EXISTS(SELECT 1 FROM leave_requests WHERE user_id = ?)
            """,
            (user_id, user_id),
        ).fetchone()[0]
        if has_documents:
            raise ValueError(
                "This user has submitted documents and cannot be deleted from the archive."
            )
        return conn.execute("DELETE FROM users WHERE id = ?", (user_id,)).rowcount > 0


def _has_another_admin(user_id: int) -> bool:
    if _using_supabase():
        return bool(
            _remote_rows(
                _client()
                .table("users")
                .select("id")
                .eq("role", "admin")
                .neq("id", user_id)
                .limit(1)
                .execute()
            )
        )
    with connect() as conn:
        return conn.execute(
            "SELECT EXISTS(SELECT 1 FROM users WHERE role = 'admin' AND id != ?)",
            (user_id,),
        ).fetchone()[0] == 1


def _recorded_minutes_snapshot(hours, year, month):
    if hours is None:
        return None
    if not isinstance(hours, dict):
        raise ValueError('Recorded hours snapshot is invalid.')
    minutes = {}
    for raw_day, raw_hours in hours.items():
        day = raw_day if isinstance(raw_day, date) else date.fromisoformat(raw_day)
        amount = float(raw_hours)
        if (day.year, day.month) != (year, month) or not math.isfinite(amount) or not 0 < amount <= 24:
            raise ValueError('Recorded hours snapshot is invalid.')
        rounded = round(amount * 60)
        if abs(amount * 60 - rounded) > 0.00001:
            raise ValueError('Recorded hours snapshot is invalid.')
        minutes[day.isoformat()] = rounded
    return minutes


def upsert_timesheet(
    *, user_id: int, year: int, month: int, fmt: str, vacation: str,
    sick: str, custom_days: str, custom_reason: str, working_days: int,
    total_hours: int, filename: str, storage_key: str | None = None,
    leave_snapshot: list | None = None,
    actor_user_id: int | None = None,
    daily_hours_snapshot: dict | None = None,
) -> sqlite3.Row:
    date(year, month, 1)
    if fmt not in {'pdf', 'xlsx'}:
        raise ValueError('Choose PDF or Excel.')
    minute_snapshot = _recorded_minutes_snapshot(daily_hours_snapshot, year, month)
    values = dict(user_id=user_id, year=year, month=month, format=fmt,
                  vacation=vacation, sick=sick, custom_days=custom_days,
                  custom_reason=custom_reason, working_days=working_days,
                  total_hours=total_hours, filename=filename, storage_key=storage_key,
                  leave_snapshot=leave_snapshot or [], submitted_at=_now())
    if _using_supabase():
        return _workflow_rpc('submit_timesheet_atomic', {'p_values': {
            **values, 'validate_leave_snapshot': leave_snapshot is not None, 'actor_user_id': actor_user_id,
            'validate_daily_hours_snapshot': minute_snapshot is not None, 'minute_snapshot': minute_snapshot}})
    values['leave_snapshot'] = json.dumps(values['leave_snapshot'])
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        existing = conn.execute('SELECT status FROM timesheets WHERE user_id = ? AND year = ? AND month = ?',
                                (user_id, year, month)).fetchone()
        if existing and existing['status'] != 'needs_changes':
            raise WorkflowConflict('This month is already submitted or approved. Refresh before continuing.')
        if minute_snapshot is not None:
            month_start, month_end = _month_bounds(year, month)
            recorded = {row['work_date']: row['minutes'] for row in conn.execute(
                'SELECT work_date, SUM(minutes) AS minutes FROM time_entries WHERE user_id = ? '
                'AND work_date BETWEEN ? AND ? GROUP BY work_date', (user_id, month_start, month_end))}
            if recorded != minute_snapshot:
                raise WorkflowConflict('Recorded hours changed while this document was generated. Refresh and submit again.')
        if leave_snapshot is not None:
            month_start, month_end = _month_bounds(year, month)
            approved = conn.execute("SELECT id, start_date, end_date, leave_type FROM leave_requests WHERE user_id = ? AND status = 'accepted' AND start_date <= ? AND end_date >= ?",
                                    (user_id, month_end, month_start)).fetchall()
            def normalized(rows):
                return sorted((row['id'], row['start_date'], row['end_date'], row['leave_type']) for row in rows)
            if normalized(approved) != normalized(leave_snapshot):
                raise WorkflowConflict('Approved leave changed while this document was generated. Refresh and submit again.')
        columns = ', '.join(values)
        placeholders = ', '.join('?' for _ in values)
        updates = ', '.join(f'{key} = excluded.{key}' for key in values if key not in {'user_id', 'year', 'month'})
        conn.execute(f"INSERT INTO timesheets ({columns}) VALUES ({placeholders}) "
                     f"ON CONFLICT(user_id, year, month) DO UPDATE SET {updates}, "
                     "status = 'submitted', review_comment = '', reviewed_at = NULL", tuple(values.values()))
        saved = conn.execute('SELECT * FROM timesheets WHERE user_id = ? AND year = ? AND month = ?',
                             (user_id, year, month)).fetchone()
        record_workflow_event(conn, 'timesheets', saved['id'], user_id,
                              'resubmitted' if existing else 'submitted', 'submitted', actor_user_id=actor_user_id)
        return saved


def get_timesheet(ts_id: int) -> sqlite3.Row | None:
    if _using_supabase():
        row = _remote_one(
            _client().table("timesheets").select("*").eq("id", ts_id).execute()
        )
        return _with_user_details([row])[0] if row else None
    with connect() as conn:
        return conn.execute(
            """
            SELECT t.*, u.full_name, u.username
            FROM timesheets t JOIN users u ON u.id = t.user_id
            WHERE t.id = ?
            """,
            (ts_id,),
        ).fetchone()


def get_timesheet_for_period(
    user_id: int, year: int, month: int
) -> sqlite3.Row | None:
    """Return a user's previous submission for a month, if any."""
    if _using_supabase():
        return _remote_one(
            _client()
            .table("timesheets")
            .select("*")
            .eq("user_id", user_id)
            .eq("year", year)
            .eq("month", month)
            .execute()
        )
    with connect() as conn:
        return conn.execute(
            """
            SELECT * FROM timesheets
            WHERE user_id = ? AND year = ? AND month = ?
            """,
            (user_id, year, month),
        ).fetchone()


def _review_document(table, document_id, status, review_comment, expected_submitted_at=None, reopen=False, actor_user_id=None):
    if status not in {'accepted', 'needs_changes'}:
        raise ValueError('Choose a valid review decision.')
    comment = review_comment.strip()
    if len(comment) > 1000 or (status == 'needs_changes' and not comment):
        raise ValueError('Add a comment so the employee knows what to change.')
    if _using_supabase():
        row = _remote_one(_client().table(table).select('*').eq('id', document_id).execute())
        if not row:
            return None
        years = range(date.fromisoformat(row['start_date']).year, date.fromisoformat(row['end_date']).year + 1) if table == 'leave_requests' else []
        return _workflow_rpc('review_document_atomic', {
            'p_kind': table, 'p_id': document_id, 'p_status': status,
            'p_comment': comment, 'p_reviewed_at': _now(),
            'p_expected_submitted_at': expected_submitted_at, 'p_reopen': reopen,
            'p_holidays': _nonworking_dates(years),
            'p_actor_user_id': actor_user_id,
        })
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute(f'SELECT * FROM {table} WHERE id = ?', (document_id,)).fetchone()
        if row is None:
            return None
        expected_status = 'accepted' if reopen else 'submitted'
        if row['status'] != expected_status or (expected_submitted_at is not None and row['submitted_at'] != expected_submitted_at):
            raise WorkflowConflict('This request changed after you opened it. Refresh before reviewing it.')
        if table == 'leave_requests' and status == 'accepted':
            _validate_leave_reservation(conn, row['user_id'], row['start_date'], row['end_date'], row['leave_type'], row['id'])
        conn.execute(f'UPDATE {table} SET status = ?, review_comment = ?, reviewed_at = ? WHERE id = ?',
                     (status, comment, _now(), document_id))
        record_workflow_event(conn, table, document_id, row['user_id'], 'reopened' if reopen else 'reviewed',
                              status, comment, actor_user_id)
        return conn.execute(f'SELECT * FROM {table} WHERE id = ?', (document_id,)).fetchone()


def review_timesheet(ts_id: int, status: str, review_comment: str, expected_submitted_at=None, actor_user_id=None):
    return _review_document('timesheets', ts_id, status, review_comment, expected_submitted_at, actor_user_id=actor_user_id)


def reopen_timesheet(ts_id: int, review_comment: str, expected_submitted_at=None, actor_user_id=None):
    return _review_document('timesheets', ts_id, 'needs_changes', review_comment, expected_submitted_at, reopen=True, actor_user_id=actor_user_id)


def list_timesheets(year: int, month: int) -> list[sqlite3.Row]:
    if _using_supabase():
        rows = _remote_rows(
            _client()
            .table("timesheets")
            .select("*")
            .eq("year", year)
            .eq("month", month)
            .execute()
        )
        return sorted(_with_user_details(rows), key=lambda row: row["full_name"])
    with connect() as conn:
        return conn.execute(
            """
            SELECT t.*, u.full_name, u.username
            FROM timesheets t JOIN users u ON u.id = t.user_id
            WHERE t.year = ? AND t.month = ?
            ORDER BY u.full_name
            """,
            (year, month),
        ).fetchall()


def list_user_timesheets(user_id: int) -> list[sqlite3.Row]:
    if _using_supabase():
        return _remote_rows(
            _client()
            .table("timesheets")
            .select("*")
            .eq("user_id", user_id)
            .order("year", desc=True)
            .order("month", desc=True)
            .execute()
        )
    with connect() as conn:
        return conn.execute(
            """
            SELECT t.*
            FROM timesheets t
            WHERE t.user_id = ?
            ORDER BY t.year DESC, t.month DESC
            """,
            (user_id,),
        ).fetchall()


def create_leave_request(
    *, user_id: int, start_date: str, end_date: str, leave_type: str,
    comment: str, filename: str, storage_key: str | None = None,
    actor_user_id: int | None = None,
) -> sqlite3.Row:
    start, end = _validate_leave_dates(start_date, end_date, leave_type)
    values = dict(user_id=user_id, start_date=start_date, end_date=end_date,
                  leave_type=leave_type, comment=comment, filename=filename,
                  storage_key=storage_key, submitted_at=_now())
    if _using_supabase():
        return _workflow_rpc('submit_leave_atomic', {'p_values': {**values, 'actor_user_id': actor_user_id},
            'p_holidays': _nonworking_dates(range(start.year, end.year + 1))})
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        _validate_leave_reservation(conn, user_id, start_date, end_date, leave_type)
        columns = ', '.join(values)
        placeholders = ', '.join('?' for _ in values)
        cursor = conn.execute(f'INSERT INTO leave_requests ({columns}) VALUES ({placeholders})', tuple(values.values()))
        record_workflow_event(conn, 'leave_requests', cursor.lastrowid, user_id, 'submitted', 'submitted', actor_user_id=actor_user_id)
        return conn.execute('SELECT * FROM leave_requests WHERE id = ?', (cursor.lastrowid,)).fetchone()


def get_leave_request(leave_id: int) -> sqlite3.Row | None:
    if _using_supabase():
        row = _remote_one(
            _client().table("leave_requests").select("*").eq("id", leave_id).execute()
        )
        return _with_user_details([row])[0] if row else None
    with connect() as conn:
        return conn.execute(
            """
            SELECT l.*, u.full_name, u.username
            FROM leave_requests l JOIN users u ON u.id = l.user_id
            WHERE l.id = ?
            """,
            (leave_id,),
        ).fetchone()


def review_leave_request(leave_id: int, status: str, review_comment: str, expected_submitted_at=None, actor_user_id=None):
    return _review_document('leave_requests', leave_id, status, review_comment, expected_submitted_at, actor_user_id=actor_user_id)


def list_leave_requests(limit: int = 100) -> list[sqlite3.Row]:
    if _using_supabase():
        rows = _remote_rows(
            _client()
            .table("leave_requests")
            .select("*")
            .order("submitted_at", desc=True)
            .limit(limit)
            .execute()
        )
        return _with_user_details(rows)
    with connect() as conn:
        return conn.execute(
            """
            SELECT l.*, u.full_name, u.username
            FROM leave_requests l JOIN users u ON u.id = l.user_id
            ORDER BY l.submitted_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()


def list_user_leave_requests(user_id: int) -> list[sqlite3.Row]:
    if _using_supabase():
        return _remote_rows(
            _client()
            .table("leave_requests")
            .select("*")
            .eq("user_id", user_id)
            .order("submitted_at", desc=True)
            .execute()
        )
    with connect() as conn:
        return conn.execute(
            """
            SELECT l.*
            FROM leave_requests l
            WHERE l.user_id = ?
            ORDER BY l.submitted_at DESC
            """,
            (user_id,),
        ).fetchall()


def list_leave_requests_for_month(year: int, month: int) -> list[sqlite3.Row]:
    month_start, month_end = _month_bounds(year, month)
    if _using_supabase():
        rows = _remote_rows(
            _client()
            .table("leave_requests")
            .select("*")
            .lte("start_date", month_end)
            .gte("end_date", month_start)
            .order("start_date")
            .execute()
        )
        return sorted(
            _with_user_details(rows), key=lambda row: (row["start_date"], row["full_name"])
        )
    with connect() as conn:
        return conn.execute(
            """
            SELECT l.*, u.full_name, u.username
            FROM leave_requests l JOIN users u ON u.id = l.user_id
            WHERE l.start_date <= ? AND l.end_date >= ?
            ORDER BY l.start_date, u.full_name
            """,
            (month_end, month_start),
        ).fetchall()


def list_approved_leave_ranges(
    user_id: int, year: int, month: int
) -> list[sqlite3.Row]:
    month_start, month_end = _month_bounds(year, month)
    if _using_supabase():
        return _remote_rows(
            _client()
            .table("leave_requests")
            .select("id,start_date,end_date,leave_type")
            .eq("user_id", user_id)
            .eq("status", "accepted")
            .lte("start_date", month_end)
            .gte("end_date", month_start)
            .order("start_date")
            .execute()
        )
    with connect() as conn:
        return conn.execute(
            """
            SELECT id, start_date, end_date, leave_type
            FROM leave_requests
            WHERE user_id = ?
              AND status = 'accepted'
              AND start_date <= ?
              AND end_date >= ?
            ORDER BY start_date
            """,
            (user_id, month_end, month_start),
        ).fetchall()


def _month_bounds(year: int, month: int) -> tuple[str, str]:
    """Inclusive bounds avoid constructing a year beyond Python's date range."""
    return date(year, month, 1).isoformat(), date(year, month, calendar.monthrange(year, month)[1]).isoformat()
