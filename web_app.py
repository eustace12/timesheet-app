#!/usr/bin/env python3
"""
Web interface for Timesheet Generator
Веб-интерфейс для генератора таймшитов
"""

from collections import defaultdict
from functools import wraps
from time import time

from flask import (
    Flask,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    session,
    url_for,
)
from pathlib import Path
from datetime import date, datetime
import calendar
import os
import sqlite3

import db
from generate_leave import build_leave_filename, generate_leave_pdf
from generate_timesheet import (
    PDFTimesheetGenerator,
    ExcelTimesheetGenerator,
    build_timesheet_filename,
)
from moldova_holidays import (
    get_moldova_holidays,
    holidays_for_api,
    holidays_meta,
    HOLIDAY_SOURCE,
    HOLIDAY_SCOPE_LABEL,
    warmup_holiday_cache,
)

app = Flask(__name__)
STATIC_DIR = Path(__file__).parent / 'static'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max
app.secret_key = os.environ.get('SECRET_KEY', 'dev-change-me')

GENERATE_RATE_LIMIT = int(os.environ.get('GENERATE_RATE_LIMIT', '10'))
GENERATE_RATE_WINDOW = int(os.environ.get('GENERATE_RATE_WINDOW', '60'))
_generate_hits: dict[str, list[float]] = defaultdict(list)

_JSON_AUTH_PATHS = {'/generate', '/preview', '/timesheets/submit', '/leave/submit'}
_PUBLIC_ENDPOINTS = {'login', 'logout', 'health', 'favicon', 'static'}

if os.environ.get('VERCEL'):
    OUTPUT_DIR = Path('/tmp/generated')
    _DB_PATH = Path('/tmp/timesheet.db')
else:
    OUTPUT_DIR = Path(__file__).parent / 'generated'
    _DB_PATH = Path(__file__).parent / 'data' / 'timesheet.db'
OUTPUT_DIR.mkdir(exist_ok=True)

db.configure(_DB_PATH)
db.init_db()
db.seed_if_empty()

warmup_holiday_cache()


def _row_dict(row):
    return dict(row) if row is not None else None


def current_user():
    user_id = session.get('user_id')
    if not user_id:
        return None
    return _row_dict(db.get_user_by_id(user_id))


def _unauthenticated_response():
    if request.method == 'POST' and request.path in _JSON_AUTH_PATHS:
        return jsonify({'error': 'Authentication required'}), 401
    return redirect(url_for('login'))


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if app.config.get('TESTING'):
            return view(*args, **kwargs)
        if current_user() is None:
            return _unauthenticated_response()
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if app.config.get('TESTING'):
            return view(*args, **kwargs)
        user = current_user()
        if user is None:
            return _unauthenticated_response()
        if user['role'] != 'admin':
            abort(403)
        return view(*args, **kwargs)
    return wrapped


@app.context_processor
def inject_user():
    return {'user': current_user()}


@app.before_request
def _require_login():
    if app.config.get('TESTING'):
        return None
    if request.endpoint in _PUBLIC_ENDPOINTS or request.endpoint is None:
        return None
    if current_user() is None:
        return _unauthenticated_response()
    return None


def _parse_holiday_scope(raw: str | None) -> str:
    return 'national'


def _check_generate_rate_limit():
    if app.config.get('TESTING'):
        return None
    ip = request.headers.get('X-Forwarded-For', request.remote_addr or 'local').split(',')[0].strip()
    now = time()
    hits = _generate_hits[ip]
    hits[:] = [t for t in hits if now - t < GENERATE_RATE_WINDOW]
    if len(hits) >= GENERATE_RATE_LIMIT:
        return jsonify({'error': 'Too many downloads. Please wait a minute and try again.'}), 429
    hits.append(now)
    return None


def reset_rate_limit_for_tests():
    """Clear rate-limit counters (tests only)."""
    _generate_hits.clear()


def _parse_date_ranges(raw, year, month):
    dates = []
    raw = (raw or '').strip()
    if not raw:
        return dates
    for date_range in raw.split(','):
        date_range = date_range.strip()
        if '-' in date_range:
            start_day, end_day = map(int, date_range.split('-'))
            dates.append((date(year, month, start_day), date(year, month, end_day)))
        else:
            day = int(date_range)
            single_date = date(year, month, day)
            dates.append((single_date, single_date))
    return dates


def _timesheet_params(user=None):
    year = int(request.form.get('year'))
    month = int(request.form.get('month'))
    if user:
        employee_name = user['full_name']
        company_name = user['company_name']
        hours_per_day = int(user['hours_per_day'])
    else:
        employee_name = request.form.get('employee_name', '').strip()
        company_name = request.form.get('company_name', 'Urchin Systems SA').strip()
        hours_per_day = int(request.form.get('hours_per_day', 8))

    vacation_raw = request.form.get('vacation', '').strip()
    sick_raw = request.form.get('sick', '').strip()
    custom_days_raw = request.form.get('custom_days', '').strip()
    custom_reason = request.form.get('custom_reason', '').strip()

    custom_absences = {}
    if custom_days_raw and custom_reason:
        for day_str in custom_days_raw.split(','):
            day = int(day_str.strip())
            custom_absences[date(year, month, day)] = custom_reason

    output_format = request.form.get('format', 'pdf').strip().lower()
    return {
        'year': year,
        'month': month,
        'employee_name': employee_name,
        'company_name': company_name,
        'hours_per_day': hours_per_day,
        'holiday_scope': _parse_holiday_scope(request.form.get('holiday_scope')),
        'vacation_raw': vacation_raw,
        'sick_raw': sick_raw,
        'custom_days_raw': custom_days_raw,
        'custom_reason': custom_reason,
        'vacation_dates': _parse_date_ranges(vacation_raw, year, month),
        'sick_dates': _parse_date_ranges(sick_raw, year, month),
        'custom_absences': custom_absences,
        'format': output_format,
    }


def _make_generator(params):
    gen_cls = ExcelTimesheetGenerator if params['format'] == 'xlsx' else PDFTimesheetGenerator
    return gen_cls(
        year=params['year'],
        month=params['month'],
        employee_name=params['employee_name'],
        company_name=params['company_name'],
        hours_per_day=params['hours_per_day'],
        vacation_dates=params['vacation_dates'],
        sick_dates=params['sick_dates'],
        custom_absences=params['custom_absences'],
        holiday_scope=params['holiday_scope'],
    )


def _account_for_generate():
    if app.config.get('TESTING'):
        return None
    return current_user()


@app.route('/favicon.ico')
def favicon():
    return send_from_directory(STATIC_DIR, 'favicon.ico', mimetype='image/vnd.microsoft.icon')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = db.authenticate(
            request.form.get('username', ''),
            request.form.get('password', ''),
        )
        if user is None:
            return render_template('login.html', error='Invalid username or password')
        session['user_id'] = user['id']
        if user['role'] == 'admin':
            return redirect(url_for('admin_page'))
        return redirect(url_for('index'))
    user = current_user()
    if user:
        if user['role'] == 'admin':
            return redirect(url_for('admin_page'))
        return redirect(url_for('index'))
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/')
@login_required
def index():
    user = current_user()
    if user and user['role'] == 'admin':
        return redirect(url_for('admin_page'))
    current_year = datetime.now().year
    current_month = datetime.now().month
    return render_template(
        'index.html',
        user=user,
        current_year=current_year,
        current_month=current_month,
        months=list(calendar.month_name)[1:],
    )


@app.route('/health')
def health():
    year = datetime.now().year
    holidays = get_moldova_holidays(year, 'national')
    loaded = len(holidays)
    meta = holidays_meta(year)
    return jsonify({
        'status': 'ok' if loaded > 0 else 'degraded',
        'holidays_loaded': loaded,
        'year': year,
        'source': HOLIDAY_SOURCE,
        'fetched_at': meta.get('fetched_at'),
    })


@app.route('/generate', methods=['POST'])
@login_required
def generate_timesheet():
    limited = _check_generate_rate_limit()
    if limited:
        return limited

    try:
        params = _timesheet_params(_account_for_generate())
        if not params['employee_name']:
            return jsonify({'error': 'Employee name is required'}), 400
        if params['format'] not in ('pdf', 'xlsx'):
            return jsonify({'error': 'Invalid format. Use pdf or xlsx.'}), 400

        ext = 'xlsx' if params['format'] == 'xlsx' else 'pdf'
        filename = build_timesheet_filename(params['employee_name'], params['year'], params['month'], ext)
        output_path = OUTPUT_DIR / filename

        generator = _make_generator(params)
        generator.generate(output_path)

        mimetype = (
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            if params['format'] == 'xlsx'
            else 'application/pdf'
        )
        return send_file(output_path, as_attachment=True, download_name=filename, mimetype=mimetype)

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/preview', methods=['POST'])
@login_required
def preview_timesheet():
    try:
        year = int(request.form.get('year'))
        month = int(request.form.get('month'))
        hours_per_day = int(request.form.get('hours_per_day', 8))
        custom_reason = request.form.get('custom_reason', 'Other').strip() or 'Other'
        holiday_scope = _parse_holiday_scope(request.form.get('holiday_scope'))

        def expand_ranges(raw):
            days = set()
            if not raw:
                return days
            for part in raw.split(','):
                part = part.strip()
                if not part:
                    continue
                if '-' in part:
                    a, b = map(int, part.split('-', 1))
                    days.update(range(a, b + 1))
                else:
                    days.add(int(part))
            return days

        vacation_days = expand_ranges(request.form.get('vacation', ''))
        sick_days = expand_ranges(request.form.get('sick', ''))
        custom_days = expand_ranges(request.form.get('custom_days', ''))

        holidays_dict = get_moldova_holidays(year, holiday_scope)
        month_holidays = {d.day: name for d, name in holidays_dict.items() if d.month == month}

        days_in_month = calendar.monthrange(year, month)[1]
        day_names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

        result_days = []
        working_count = 0
        holiday_count = 0

        for d in range(1, days_in_month + 1):
            dt = date(year, month, d)
            dow = dt.weekday()

            is_weekend = dow >= 5
            is_holiday = d in month_holidays
            is_vacation = d in vacation_days
            is_sick = d in sick_days
            is_custom = d in custom_days

            if is_vacation:
                status = 'vacation'
                hours = 0
            elif is_sick:
                status = 'sick'
                hours = 0
            elif is_custom:
                status = 'custom'
                hours = 0
            elif is_holiday:
                status = 'holiday'
                hours = 0
                holiday_count += 1
            elif is_weekend:
                status = 'weekend'
                hours = 0
            else:
                status = 'working'
                hours = hours_per_day
                working_count += 1

            result_days.append({
                'day': d,
                'date_str': f'{day_names[dow]}, {dt.strftime("%b %d")}',
                'status': status,
                'hours': hours,
                'holiday_name': month_holidays.get(d),
                'custom_reason': custom_reason if is_custom else None,
            })

        return jsonify({
            'month': calendar.month_name[month],
            'year': year,
            'days': result_days,
            'summary': {
                'total_days': days_in_month,
                'working_days': working_count,
                'total_hours': working_count * hours_per_day,
                'vacation_days': len(vacation_days),
                'sick_days': len(sick_days),
                'custom_days': len(custom_days),
                'holiday_days': holiday_count,
            },
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/download/<filename>')
@login_required
def download_file(filename):
    file_path = OUTPUT_DIR / filename
    if file_path.exists():
        return send_file(file_path, as_attachment=True)
    return "File not found. On serverless platforms, files must be downloaded immediately after generation.", 404


@app.route('/holidays/<int:year>')
@login_required
def get_holidays(year):
    scope = _parse_holiday_scope(request.args.get('scope'))
    meta = holidays_meta(year)
    holidays = holidays_for_api(year, scope)
    return jsonify({
        'source': HOLIDAY_SOURCE,
        'year': year,
        'scope': scope,
        'scope_label': HOLIDAY_SCOPE_LABEL,
        'fetched_at': meta.get('fetched_at'),
        'holidays': holidays,
        'warning': None if holidays else 'Holiday data unavailable. Check network or try again.',
    })


@app.route('/leave')
@login_required
def leave_page():
    return render_template('leave.html', user=current_user())


@app.route('/leave/submit', methods=['POST'])
@login_required
def submit_leave():
    user = current_user()
    if not user:
        return jsonify({'error': 'Authentication required'}), 401
    try:
        start = date.fromisoformat((request.form.get('start_date') or '').strip())
        end = date.fromisoformat((request.form.get('end_date') or '').strip())
    except ValueError:
        return jsonify({'error': 'Invalid date'}), 400
    if end < start:
        return jsonify({'error': 'End date must be on or after start date'}), 400

    leave_type = (request.form.get('leave_type') or 'Vacation').strip() or 'Vacation'
    comment = (request.form.get('comment') or '').strip()
    filename = build_leave_filename(user['full_name'], start, end)
    leave_dir = OUTPUT_DIR / 'leave'
    leave_dir.mkdir(parents=True, exist_ok=True)
    output_path = leave_dir / filename
    generate_leave_pdf(
        output_path,
        employee_name=user['full_name'],
        company_name=user['company_name'],
        start_date=start,
        end_date=end,
        leave_type=leave_type,
        comment=comment,
    )
    row = db.create_leave_request(
        user_id=user['id'],
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        leave_type=leave_type,
        comment=comment,
        filename=filename,
    )
    return jsonify({'ok': True, 'id': row['id']})


@app.route('/timesheets/submit', methods=['POST'])
@login_required
def submit_timesheet():
    user = current_user()
    if not user:
        return jsonify({'error': 'Authentication required'}), 401
    try:
        params = _timesheet_params(user)
        if params['format'] not in ('pdf', 'xlsx'):
            return jsonify({'error': 'Invalid format. Use pdf or xlsx.'}), 400

        ext = 'xlsx' if params['format'] == 'xlsx' else 'pdf'
        year = params['year']
        month = params['month']
        ts_dir = OUTPUT_DIR / 'timesheets'
        ts_dir.mkdir(parents=True, exist_ok=True)
        stem = f"ts_{user['id']}_{year}_{month:02d}"
        filename = f'{stem}.{ext}'
        output_path = ts_dir / filename

        generator = _make_generator(params)
        working_days, total_hours, _ = generator.get_all_days()
        generator.generate(output_path)
        for leftover in ts_dir.glob(f'{stem}.*'):
            if leftover.name != filename:
                leftover.unlink(missing_ok=True)

        row = db.upsert_timesheet(
            user_id=user['id'],
            year=year,
            month=month,
            fmt=params['format'],
            vacation=params['vacation_raw'],
            sick=params['sick_raw'],
            custom_days=params['custom_days_raw'],
            custom_reason=params['custom_reason'],
            working_days=working_days,
            total_hours=total_hours,
            filename=filename,
        )
        return jsonify({'ok': True, 'id': row['id']})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/admin')
@admin_required
def admin_page():
    now = datetime.now()
    try:
        year = int(request.args.get('year', now.year))
        month = int(request.args.get('month', now.month))
    except (TypeError, ValueError):
        year, month = now.year, now.month
    if not 1 <= month <= 12:
        month = now.month

    timesheets = db.list_timesheets(year, month)
    submitted_ids = {row['user_id'] for row in timesheets}
    missing = [emp for emp in db.list_employees() if emp['id'] not in submitted_ids]
    return render_template(
        'admin.html',
        user=current_user(),
        year=year,
        month=month,
        months=list(calendar.month_name)[1:],
        timesheets=timesheets,
        leave_requests=db.list_leave_requests(),
        missing=missing,
    )


@app.route('/admin/users', methods=['GET', 'POST'])
@admin_required
def admin_users():
    error = None
    if request.method == 'POST':
        try:
            db.create_user(
                request.form.get('username', ''),
                request.form.get('password', ''),
                request.form.get('full_name', ''),
                request.form.get('role', ''),
            )
            return redirect(url_for('admin_users'))
        except ValueError as exc:
            error = str(exc)
        except sqlite3.IntegrityError:
            error = 'Username already exists'
    return render_template(
        'users.html',
        user=current_user(),
        users=db.list_users(),
        error=error,
    )


@app.route('/admin/timesheets/<int:ts_id>/download')
@admin_required
def download_timesheet(ts_id):
    row = db.get_timesheet(ts_id)
    if row is None:
        abort(404)
    path = OUTPUT_DIR / 'timesheets' / row['filename']
    if not path.exists():
        abort(404)
    return send_file(path, as_attachment=True, download_name=row['filename'])


@app.route('/admin/leave/<int:leave_id>/download')
@admin_required
def download_leave(leave_id):
    row = db.get_leave_request(leave_id)
    if row is None:
        abort(404)
    path = OUTPUT_DIR / 'leave' / row['filename']
    if not path.exists():
        abort(404)
    return send_file(path, as_attachment=True, download_name=row['filename'])


if __name__ == '__main__':
    print("Starting Timesheet Generator Web Interface...")
    print("Open in browser: http://localhost:8080")
    print("Demo: admin / admin123  (accountant inbox)")
    print("      employee / employee123")
    print("      stanislav / employee123")
    print("Press Ctrl+C to stop")
    app.run(debug=True, host='0.0.0.0', port=8080)
