"""SQLite persistence for users, timesheets, and leave requests."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

_db_path: Path | None = None

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
    submitted_at TEXT NOT NULL,
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
    submitted_at TEXT NOT NULL
);
"""

DEMO_USERS = (
    {
        "username": "admin",
        "password": "admin123",
        "full_name": "Accountant",
        "role": "admin",
    },
    {
        "username": "employee",
        "password": "employee123",
        "full_name": "Test User",
        "role": "employee",
    },
    {
        "username": "stanislav",
        "password": "employee123",
        "full_name": "Iuntevici Stanislav",
        "role": "employee",
    },
)


def configure(path: str | Path) -> None:
    global _db_path
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
    with connect() as conn:
        conn.executescript(SCHEMA)


def seed_if_empty() -> None:
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
                    generate_password_hash(user["password"]),
                    user["full_name"],
                    user["role"],
                    now,
                ),
            )


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def get_user_by_id(user_id: int) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def get_user_by_username(username: str) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE username = ?", (username.strip().lower(),)
        ).fetchone()


def authenticate(username: str, password: str) -> sqlite3.Row | None:
    user = get_user_by_username(username)
    if user and check_password_hash(user["password_hash"], password):
        return user
    return None


def list_users() -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT id, username, full_name, role, created_at FROM users ORDER BY role, full_name"
        ).fetchall()


def list_employees() -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT id, username, full_name FROM users WHERE role = 'employee' ORDER BY full_name"
        ).fetchall()


def create_user(username: str, password: str, full_name: str, role: str) -> sqlite3.Row:
    username = username.strip().lower()
    full_name = full_name.strip()
    role = role.strip()
    if role not in ("employee", "admin"):
        raise ValueError("Role must be employee or admin")
    if not username or not password or not full_name:
        raise ValueError("Username, password, and full name are required")
    if len(password) < 6:
        raise ValueError("Password must be at least 6 characters")
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO users (username, password_hash, full_name, role, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (username, generate_password_hash(password), full_name, role, _now()),
        )
        return conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()


def upsert_timesheet(
    *,
    user_id: int,
    year: int,
    month: int,
    fmt: str,
    vacation: str,
    sick: str,
    custom_days: str,
    custom_reason: str,
    working_days: int,
    total_hours: int,
    filename: str,
) -> sqlite3.Row:
    submitted_at = _now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO timesheets (
                user_id, year, month, format, vacation, sick, custom_days, custom_reason,
                working_days, total_hours, filename, submitted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, year, month) DO UPDATE SET
                format = excluded.format,
                vacation = excluded.vacation,
                sick = excluded.sick,
                custom_days = excluded.custom_days,
                custom_reason = excluded.custom_reason,
                working_days = excluded.working_days,
                total_hours = excluded.total_hours,
                filename = excluded.filename,
                submitted_at = excluded.submitted_at
            """,
            (
                user_id, year, month, fmt, vacation, sick, custom_days, custom_reason,
                working_days, total_hours, filename, submitted_at,
            ),
        )
        return conn.execute(
            "SELECT * FROM timesheets WHERE user_id = ? AND year = ? AND month = ?",
            (user_id, year, month),
        ).fetchone()


def get_timesheet(ts_id: int) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute(
            """
            SELECT t.*, u.full_name, u.username
            FROM timesheets t JOIN users u ON u.id = t.user_id
            WHERE t.id = ?
            """,
            (ts_id,),
        ).fetchone()


def list_timesheets(year: int, month: int) -> list[sqlite3.Row]:
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


def create_leave_request(
    *,
    user_id: int,
    start_date: str,
    end_date: str,
    leave_type: str,
    comment: str,
    filename: str,
) -> sqlite3.Row:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO leave_requests (
                user_id, start_date, end_date, leave_type, comment, filename, submitted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, start_date, end_date, leave_type, comment, filename, _now()),
        )
        row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        return conn.execute("SELECT * FROM leave_requests WHERE id = ?", (row_id,)).fetchone()


def get_leave_request(leave_id: int) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute(
            """
            SELECT l.*, u.full_name, u.username
            FROM leave_requests l JOIN users u ON u.id = l.user_id
            WHERE l.id = ?
            """,
            (leave_id,),
        ).fetchone()


def list_leave_requests(limit: int = 100) -> list[sqlite3.Row]:
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
