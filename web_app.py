#!/usr/bin/env python3
"""
Web interface for Timesheet Generator
Веб-интерфейс для генератора таймшитов
"""

from collections import defaultdict
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
from datetime import date, datetime, timedelta
import calendar
import io
import os
import secrets
import sqlite3
import unicodedata
import uuid
import zipfile
from tempfile import TemporaryDirectory

import db
import hr_store
import document_storage
import production_access
import timesheet_absences
from people_experience import company_today
from i18n import SUPPORTED_LANGUAGES, translate
from generate_leave import build_leave_filename, generate_leave_pdf
from generate_timesheet import (
    PDFTimesheetGenerator,
    ExcelTimesheetGenerator,
    build_timesheet_filename,
)
from moldova_holidays import (
    HolidayDataUnavailable,
    get_moldova_holidays,
    holidays_for_api,
    holidays_meta,
    HOLIDAY_SOURCE,
    HOLIDAY_SCOPE_LABEL,
    warmup_holiday_cache,
)

app = Flask(__name__)
app.config['APP_MODE'] = os.environ.get('APP_MODE', 'demo')
production_access.configure_access(app, db.get_user_by_username,
                                   find_timesheet=db.get_timesheet, find_leave=db.get_leave_request)
STATIC_DIR = Path(__file__).parent / 'static'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max
_secret_key = os.environ.get('SECRET_KEY')
if (os.environ.get('VERCEL') or production_access.is_production()) and (
    not _secret_key or len(_secret_key) < 32 or _secret_key in {'dev-change-me', 'change-me'}
):
    raise RuntimeError('Vercel deployments require a strong SECRET_KEY.')
app.secret_key = _secret_key or 'dev-change-me'
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=bool(os.environ.get('VERCEL')) or production_access.is_production(),
)

GENERATE_RATE_LIMIT = int(os.environ.get('GENERATE_RATE_LIMIT', '10'))
GENERATE_RATE_WINDOW = int(os.environ.get('GENERATE_RATE_WINDOW', '60'))
_generate_hits: dict[str, list[float]] = defaultdict(list)

if (os.environ.get('VERCEL') or production_access.is_production()) and not db.is_supabase_configured():
    raise RuntimeError(
        'Vercel deployments require SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY '
        'for persistent application data.'
    )

if os.environ.get('VERCEL'):
    OUTPUT_DIR = Path('/tmp/generated')
    _DB_PATH = Path('/tmp/timesheet.db')
else:
    OUTPUT_DIR = Path(__file__).parent / 'generated'
    _DB_PATH = Path(__file__).parent / 'data' / 'timesheet.db'
OUTPUT_DIR = Path(os.environ.get('OUTPUT_DIR', str(OUTPUT_DIR)))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

db.configure(_DB_PATH)
db.init_db()
if not production_access.is_production():
    db.seed_if_empty()

warmup_holiday_cache()


def _row_dict(row):
    return dict(row) if row is not None else None


def audit_actor():
    """A selected fictional profile is not proof of who made a decision."""
    user = current_user()
    return user['id'] if production_access.is_production() and user else None


@app.errorhandler(timesheet_absences.AbsenceDataUnavailable)
def absence_unavailable(error):
    app.logger.error('Approved absence lookup failed.')
    return jsonify({'error': str(error)}), 503


@app.errorhandler(document_storage.DocumentStorageError)
def document_storage_unavailable(error):
    app.logger.error('Document storage is temporarily unavailable.')
    return jsonify({'error': 'Document storage is temporarily unavailable. Please try again shortly.'}), 503


@app.errorhandler(HolidayDataUnavailable)
def holiday_calendar_unavailable(error):
    app.logger.error('The holiday calendar is temporarily unavailable.')
    return jsonify({'error': 'We could not load the holiday calendar. Your draft is kept; please try again.'}), 503


def current_user():
    if production_access.is_production():
        return production_access.production_current_user()
    user_id = session.get('user_id')
    if user_id:
        user = db.get_user_by_id(user_id)
        if user is not None:
            return _row_dict(user)
    people = db.list_users()
    if not people:
        return None
    person = next((row for row in people if row['role'] == 'employee'), people[0])
    return _row_dict(db.get_user_by_id(person['id']))


def current_language():
    return 'en'


@app.template_filter('display_date')
def display_date(value):
    if not value:
        return '—'
    try:
        return datetime.fromisoformat(str(value).replace(' UTC', '+00:00').replace('Z', '+00:00')).strftime('%d %b %Y')
    except ValueError:
        return str(value)


def csrf_token():
    """Return a per-session token required by state-changing requests."""
    token = session.get('_csrf_token')
    if token is None:
        token = secrets.token_urlsafe(32)
        session['_csrf_token'] = token
    return token


@app.context_processor
def inject_user():
    language = current_language()
    return {
        'user': current_user(),
        'language': language,
        'supported_languages': SUPPORTED_LANGUAGES,
        't': lambda key: translate(key, language),
        'csrf_token': csrf_token,
        'people': [] if production_access.is_production() else db.list_users(),
        'is_accountant': (bool(current_user() and current_user()['role'] == 'admin') if production_access.is_production()
                          else session.get('demo_role') == 'accountant'),
        'demo_role': ('accountant' if current_user() and current_user()['role'] == 'admin' else 'employee')
                     if production_access.is_production() else session.get('demo_role', 'employee'),
    }


@app.before_request
def _validate_csrf():
    if app.config.get('TESTING'):
        return None
    if request.method in {'POST', 'PUT', 'PATCH', 'DELETE'}:
        supplied_token = request.form.get('_csrf_token') or request.headers.get('X-CSRF-Token')
        expected_token = session.get('_csrf_token', '')
        if not supplied_token or not expected_token or not secrets.compare_digest(
            supplied_token, expected_token
        ):
            abort(400)
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
    return [(date(year, month, day), date(year, month, day))
            for day in sorted(timesheet_absences.parse_ranges(raw, year, month))]


def _timesheet_params(user=None):
    year = int(request.form.get('year'))
    month = int(request.form.get('month'))
    if not 1 <= year <= 9999 or not 1 <= month <= 12:
        raise ValueError('Invalid timesheet data.')
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
        for start, end in _parse_date_ranges(custom_days_raw, year, month):
            current_day = start
            while current_day <= end:
                custom_absences[current_day] = custom_reason
                current_day += timedelta(days=1)

    logged_hours = None
    if request.form.get('source') == 'time_logs':
        selected = user or current_user()
        if selected is None:
            raise ValueError('Select a person first.')
        logged_hours = hr_store.daily_hours(selected['id'], year, month)
        if not logged_hours:
            raise ValueError('Add time logs before submitting this month.')
        employee_name = selected['full_name']
        company_name = selected['company_name']

    output_format = request.form.get('format', 'pdf').strip().lower()
    params = {
        'daily_hours': logged_hours,
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
    params.update(timesheet_absences.normalize(user, year, month, vacation_raw, sick_raw,
                                               custom_days_raw, custom_reason))
    return params


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
        daily_hours=params.get('daily_hours'),
    )


def _account_for_generate():
    if app.config.get('TESTING') and not production_access.is_production():
        return None
    return current_user()


def _parse_period_values(year_raw, month_raw):
    year_text = (year_raw or '').strip()
    month_text = (month_raw or '').strip()
    if not year_text.isdigit() or not month_text.isdigit():
        return None
    year = int(year_text)
    month = int(month_text)
    if not 1 <= year <= 9999 or not 1 <= month <= 12:
        return None
    return year, month


def _admin_period_from_form():
    period = _parse_period_values(request.form.get('year'), request.form.get('month'))
    if period is not None:
        return period
    now = company_today()
    return now.year, now.month


def _stored_file_path(folder, filename):
    if not isinstance(filename, str) or not filename:
        return None
    file_name = Path(filename)
    if file_name.name != filename or file_name.is_absolute():
        return None
    directory = OUTPUT_DIR / folder
    path = directory / file_name.name
    try:
        path.resolve().relative_to(directory.resolve())
    except ValueError:
        return None
    return path if path.is_file() else None


def _document_mimetype(filename):
    if str(filename).lower().endswith('.xlsx'):
        return 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    return 'application/pdf'


def _send_stored_document(row, folder, as_attachment=True):
    """Serve a private remote document, or a legacy local generated file."""
    storage_key = row['storage_key']
    if storage_key:
        content = document_storage.download(storage_key)
        if content is None:
            abort(404)
        return send_file(
            io.BytesIO(content),
            as_attachment=as_attachment,
            download_name=row['filename'],
            mimetype=_document_mimetype(row['filename']),
        )
    path = _stored_file_path(folder, row['filename'])
    if path is None:
        abort(404)
    return send_file(
        path,
        as_attachment=as_attachment,
        download_name=row['filename'],
        mimetype=_document_mimetype(row['filename']),
    )


def _safe_display_name(value):
    normalized = unicodedata.normalize('NFKD', value or '').encode('ascii', 'ignore').decode('ascii')
    safe_name = ''.join(
        char if char.isalnum() or char in '._-' else '_'
        for char in normalized
    ).strip('._')
    return safe_name or 'employee'


def _approved_vacation_for_month(user, year, month):
    rows = timesheet_absences.approved_for_month(user, year, month)
    return timesheet_absences.compact(day for day, kind in timesheet_absences.approved_days(rows, year, month).items()
                                     if kind == 'Vacation')


@app.route('/favicon.ico')
def favicon():
    return send_from_directory(STATIC_DIR, 'favicon.ico', mimetype='image/vnd.microsoft.icon')


@app.route('/login', methods=['GET', 'POST'])
def login():
    """Company account login; the separate public demo keeps its old shortcut."""
    from flask import current_app
    access = current_app.extensions.get('account_access')
    if access is not None:
        return access.login()
    return redirect('/')


@app.route('/logout', methods=['POST'])
def logout():
    from flask import current_app
    access = current_app.extensions.get('account_access')
    if access is None:
        abort(404)
    return access.logout()


@app.route('/profile', methods=['POST'])
def select_profile():
    try:
        user_id = int(request.form.get('user_id', ''))
    except ValueError:
        abort(400)
    if db.get_user_by_id(user_id) is None:
        abort(404)
    session['user_id'] = user_id
    next_url = request.form.get('next', '/')
    if not next_url.startswith('/') or next_url.startswith('//') or '\\' in next_url:
        next_url = url_for('index')
    return redirect(next_url)


@app.route('/demo/role', methods=['POST'])
def select_demo_role():
    role = request.form.get('role', '')
    if role not in {'employee', 'accountant'}:
        return jsonify({'error': 'Choose Employee or Accountant.'}), 400
    session['demo_role'] = role
    fallback = '/admin' if role == 'accountant' else '/'
    next_url = request.form.get('next', fallback)
    if not next_url.startswith('/') or next_url.startswith('//') or '\\' in next_url:
        next_url = fallback
    return redirect(next_url)


@app.route('/language', methods=['POST'])
def set_language():
    language = request.form.get('language', '').strip()
    if language not in SUPPORTED_LANGUAGES:
        abort(400)
    session['language'] = language

    next_url = request.form.get('next', '')
    if next_url.startswith('/') and not next_url.startswith('//'):
        return redirect(next_url)

    return redirect(url_for('index'))


@app.route('/timesheet')
@app.route('/documents')
def index():
    user = current_user()
    now = company_today()
    current_year = now.year
    current_month = now.month
    try:
        current_year = int(request.args.get('year', current_year))
        current_month = int(request.args.get('month', current_month))
        date(current_year, current_month, 1)
    except (TypeError, ValueError):
        return jsonify({'error': 'Choose a valid month and year.'}), 400
    return render_template(
        'index.html',
        user=user,
        current_year=current_year,
        current_month=current_month,
        months=list(calendar.month_name)[1:],
        approved_vacation=_approved_vacation_for_month(user, current_year, current_month),
    )


@app.route('/timesheets/period')
def timesheet_period():
    try:
        year = int(request.args.get('year', ''))
        month = int(request.args.get('month', ''))
        date(year, month, 1)
    except (TypeError, ValueError):
        return jsonify({'error': 'Choose a valid month and year.'}), 400
    try:
        user = current_user()
        row = db.get_timesheet_for_period(user['id'], year, month) if user else None
        approved = timesheet_absences.approved_for_month(user, year, month)
        approved_vacation = timesheet_absences.compact(
            day for day, kind in timesheet_absences.approved_days(approved, year, month).items()
            if kind == 'Vacation')
    except Exception:
        app.logger.error('Monthly document data is temporarily unavailable.')
        return jsonify({'error': 'Monthly document data is temporarily unavailable. Your draft is kept; please try again.'}), 503
    fields = ('id', 'status', 'review_comment', 'vacation', 'sick', 'custom_days',
              'custom_reason', 'format', 'total_hours', 'working_days', 'leave_snapshot')
    return jsonify({'year': year, 'month': month, 'hours_per_day': user['hours_per_day'] if user else 8,
                    'approved_vacation': approved_vacation,
                    'approved_absences': approved,
                    'timesheet': {field: row[field] for field in fields} if row else None})


@app.route('/health')
def health():
    year = company_today().year
    try:
        holidays = get_moldova_holidays(year, 'national')
        loaded = len(holidays)
        meta = holidays_meta(year)
    except HolidayDataUnavailable:
        return jsonify({'status': 'degraded', 'holidays_loaded': 0, 'year': year,
                        'source': HOLIDAY_SOURCE, 'fetched_at': None}), 503
    return jsonify({
        'status': 'ok' if loaded > 0 else 'degraded',
        'holidays_loaded': loaded,
        'year': year,
        'source': HOLIDAY_SOURCE,
        'fetched_at': meta.get('fetched_at'),
    })


@app.route('/generate', methods=['POST'])
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
        if (
            not 1 <= params['year'] <= 9999
            or not 1 <= params['month'] <= 12
            or not 1 <= params['hours_per_day'] <= 24
        ):
            return jsonify({'error': 'Invalid timesheet data.'}), 400

        ext = 'xlsx' if params['format'] == 'xlsx' else 'pdf'
        filename = build_timesheet_filename(params['employee_name'], params['year'], params['month'], ext)
        with TemporaryDirectory(prefix='timesheet-copy-') as temporary:
            output_path = Path(temporary) / f'document.{ext}'
            generator = _make_generator(params)
            generator.generate(output_path)
            content = output_path.read_bytes()
        mimetype = (
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            if params['format'] == 'xlsx'
            else 'application/pdf'
        )
        return send_file(io.BytesIO(content), as_attachment=True, download_name=filename, mimetype=mimetype)

    except (timesheet_absences.AbsenceDataUnavailable, HolidayDataUnavailable):
        raise
    except (TypeError, ValueError) as exc:
        return jsonify({'error': str(exc) if isinstance(exc, ValueError) else 'Invalid timesheet data.'}), 400
    except Exception:
        return jsonify({'error': 'Unable to generate timesheet. Please try again.'}), 500


@app.route('/preview', methods=['POST'])
def preview_timesheet():
    try:
        year = int(request.form.get('year'))
        month = int(request.form.get('month'))
        hours_per_day = int(request.form.get('hours_per_day', 8))
        if not 1 <= year <= 9999 or not 1 <= month <= 12 or not 1 <= hours_per_day <= 24:
            raise ValueError
        custom_reason = request.form.get('custom_reason', 'Other').strip() or 'Other'
        holiday_scope = _parse_holiday_scope(request.form.get('holiday_scope'))
        days_in_month = calendar.monthrange(year, month)[1]

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
                    if not 1 <= a <= b <= days_in_month:
                        raise ValueError
                    days.update(range(a, b + 1))
                else:
                    day = int(part)
                    if not 1 <= day <= days_in_month:
                        raise ValueError
                    days.add(day)
            return days

        vacation_days = expand_ranges(request.form.get('vacation', ''))
        sick_days = expand_ranges(request.form.get('sick', ''))
        custom_days = expand_ranges(request.form.get('custom_days', ''))

        holidays_dict = get_moldova_holidays(year, holiday_scope)
        month_holidays = {d.day: name for d, name in holidays_dict.items() if d.month == month}

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

    except HolidayDataUnavailable:
        raise
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid timesheet data.'}), 400
    except Exception:
        return jsonify({'error': 'Unable to create preview. Please try again.'}), 500


@app.route('/download/<filename>')
def download_file(filename):
    file_path = OUTPUT_DIR / filename
    if file_path.exists():
        return send_file(file_path, as_attachment=True)
    return "File not found. On serverless platforms, files must be downloaded immediately after generation.", 404


@app.route('/holidays/<int:year>')
def get_holidays(year):
    if not 1 <= year <= 9999:
        return jsonify({'error': 'Choose a valid year.'}), 400
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
def leave_page():
    user = current_user()
    year = company_today().year
    return render_template('leave.html', user=user, year=year,
                           balance=hr_store.leave_balance(user['id'], year) if user else None,
                           leave_requests=db.list_user_leave_requests(user['id']) if user else [])


@app.route('/leave/submit', methods=['POST'])
def submit_leave():
    user = current_user()
    if not user:
        return jsonify({'error': 'Add a person in People before submitting documents.'}), 400
    try:
        start = date.fromisoformat((request.form.get('start_date') or '').strip())
        end = date.fromisoformat((request.form.get('end_date') or '').strip())
    except ValueError:
        return jsonify({'error': 'Invalid date'}), 400
    if end < start:
        return jsonify({'error': 'End date must be on or after start date'}), 400

    leave_type = (request.form.get('leave_type') or 'Vacation').strip() or 'Vacation'
    comment = (request.form.get('comment') or '').strip()
    if leave_type not in {'Vacation', 'Unpaid', 'Other'}:
        return jsonify({'error': 'Invalid leave type'}), 400
    if len(comment) > 1000:
        return jsonify({'error': 'Comment is too long'}), 400
    from people_experience import assess_leave
    try:
        assessment = assess_leave(user['id'], start, end, leave_type)
    except HolidayDataUnavailable:
        raise
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    except Exception:
        app.logger.error('Leave request assessment is temporarily unavailable.')
        return jsonify({'error': 'We could not check your leave dates. Your draft is kept; please try again.'}), 503
    if not assessment['can_submit']:
        return jsonify({'error': assessment['reason']}), 409 if assessment['overlap_count'] else 400
    request_id = uuid.uuid4().hex
    filename = (
        f"{build_leave_filename(user['full_name'], start, end).removesuffix('.pdf')}"
        f"_{request_id}.pdf"
    )
    storage_key = (
        f'leave/{user["id"]}/{request_id}.pdf'
        if document_storage.is_configured()
        else None
    )
    try:
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
        if storage_key:
            document_storage.upload(
                output_path, storage_key, 'application/pdf'
            )
        row = db.create_leave_request(
            user_id=user['id'],
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            leave_type=leave_type,
            comment=comment,
            filename=filename,
            storage_key=storage_key,
            actor_user_id=audit_actor(),
        )
    except db.WorkflowConflict as exc:
        if storage_key:
            document_storage.delete(storage_key)
        output_path.unlink(missing_ok=True)
        return jsonify({'error': str(exc)}), 409
    except document_storage.DocumentStorageError:
        if storage_key:
            document_storage.delete(storage_key)
        output_path.unlink(missing_ok=True)
        raise
    except Exception:
        if storage_key:
            document_storage.delete(storage_key)
        return jsonify({'error': 'Unable to save leave request. Please try again.'}), 500
    return jsonify({'ok': True, 'id': row['id']})


@app.route('/timesheets/submit', methods=['POST'])
def submit_timesheet():
    user = current_user()
    if not user:
        return jsonify({'error': 'Add a person in People before submitting documents.'}), 400
    uploaded_storage_key = None
    storage_key = None
    output_path = None
    try:
        params = _timesheet_params(user)
        if params['format'] not in ('pdf', 'xlsx'):
            return jsonify({'error': 'Invalid format. Use pdf or xlsx.'}), 400
        if (
            not 1 <= params['year'] <= 9999
            or not 1 <= params['month'] <= 12
            or not 1 <= params['hours_per_day'] <= 24
        ):
            return jsonify({'error': 'Invalid timesheet data.'}), 400

        ext = 'xlsx' if params['format'] == 'xlsx' else 'pdf'
        year = params['year']
        month = params['month']
        previous = db.get_timesheet_for_period(user['id'], year, month)
        if previous and previous['status'] == 'submitted':
            return jsonify({'error': 'This month is already awaiting review. Ask the accountant to return it for changes.'}), 409
        if previous and previous['status'] == 'accepted':
            return jsonify({
                'error': 'This timesheet was accepted and cannot be replaced. Contact the accountant.'
            }), 409

        ts_dir = OUTPUT_DIR / 'timesheets'
        ts_dir.mkdir(parents=True, exist_ok=True)
        submission_id = uuid.uuid4().hex
        filename = f"ts_{user['id']}_{year}_{month:02d}_{submission_id}.{ext}"
        output_path = ts_dir / filename
        storage_key = (
            f'timesheets/{user["id"]}/{year:04d}-{month:02d}_{submission_id}.{ext}'
            if document_storage.is_configured()
            else None
        )

        generator = _make_generator(params)
        working_days, total_hours, _ = generator.get_all_days()
        generator.generate(output_path)

        if storage_key:
            document_storage.upload(
                output_path, storage_key, _document_mimetype(filename)
            )
            uploaded_storage_key = storage_key
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
            storage_key=storage_key,
            leave_snapshot=params['leave_snapshot'],
            actor_user_id=audit_actor(),
            daily_hours_snapshot=params.get('daily_hours'),
        )
        uploaded_storage_key = None
        previous_key = previous['storage_key'] if previous else None
        if previous_key and previous_key != storage_key:
            document_storage.delete(previous_key)
        return jsonify({'ok': True, 'id': row['id']})
    except (HolidayDataUnavailable, document_storage.DocumentStorageError):
        if uploaded_storage_key:
            document_storage.delete(uploaded_storage_key)
        elif storage_key:
            document_storage.delete(storage_key)
        if output_path is not None:
            output_path.unlink(missing_ok=True)
        raise
    except (timesheet_absences.AbsenceDataUnavailable, db.WorkflowConflict) as exc:
        if uploaded_storage_key:
            document_storage.delete(uploaded_storage_key)
        if output_path is not None:
            output_path.unlink(missing_ok=True)
        return jsonify({'error': str(exc)}), 503 if isinstance(exc, timesheet_absences.AbsenceDataUnavailable) else 409
    except (TypeError, ValueError) as exc:
        return jsonify({'error': str(exc) if isinstance(exc, ValueError) else 'Invalid timesheet data.'}), 400
    except Exception:
        if uploaded_storage_key:
            document_storage.delete(uploaded_storage_key)
        if output_path is not None:
            output_path.unlink(missing_ok=True)
        return jsonify({'error': 'Unable to save timesheet. Please try again.'}), 500


@app.route('/history')
def history():
    user = current_user()
    timesheets = db.list_user_timesheets(user['id']) if user else []
    leave_requests = db.list_user_leave_requests(user['id']) if user else []
    return render_template(
        'history.html',
        user=user,
        timesheets=timesheets,
        leave_requests=leave_requests,
    )


@app.route('/history/timesheets/<int:ts_id>/download')
def download_own_timesheet(ts_id):
    row = db.get_timesheet(ts_id)
    user = current_user()
    if row is None:
        abort(404)
    if not user or row['user_id'] != user['id']:
        abort(403)
    return _send_stored_document(row, 'timesheets')


@app.route('/history/leave/<int:leave_id>/download')
def download_own_leave(leave_id):
    row = db.get_leave_request(leave_id)
    user = current_user()
    if row is None:
        abort(404)
    if not user or row['user_id'] != user['id']:
        abort(403)
    return _send_stored_document(row, 'leave')


@app.route('/admin')
def admin_page():
    now = company_today()
    try:
        year = int(request.args.get('year', now.year))
        month = int(request.args.get('month', now.month))
    except (TypeError, ValueError):
        year, month = now.year, now.month
    if not 1 <= month <= 12 or not 1 <= year <= 9999:
        return jsonify({'error': 'Choose a valid month and year.'}), 400

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
        leave_requests=db.list_leave_requests_for_month(year, month),
        missing=missing,
    )


@app.route('/admin/timesheets/<int:ts_id>/review', methods=['POST'])
def review_timesheet(ts_id):
    if production_access.is_production() and not request.form.get('expected_submitted_at'):
        return jsonify({'error': 'Refresh this request before reviewing it.'}), 409
    status = (request.form.get('status') or '').strip()
    review_comment = (request.form.get('review_comment') or '').strip()
    if status not in {'accepted', 'needs_changes'}:
        return jsonify({'error': 'Invalid review status'}), 400
    if len(review_comment) > 1000:
        return jsonify({'error': 'Review comment is too long'}), 400
    if status == 'needs_changes' and not review_comment:
        return jsonify({'error': 'Add a comment so the employee knows what to change.'}), 400
    try:
        if db.review_timesheet(ts_id, status, review_comment, request.form.get('expected_submitted_at'), actor_user_id=audit_actor()) is None:
            abort(404)
    except db.WorkflowConflict as exc:
        return jsonify({'error': str(exc)}), 409
    year, month = _admin_period_from_form()
    return redirect(url_for('admin_page', year=year, month=month))


@app.route('/admin/leave/<int:leave_id>/review', methods=['POST'])
def review_leave_request(leave_id):
    if production_access.is_production() and not request.form.get('expected_submitted_at'):
        return jsonify({'error': 'Refresh this request before reviewing it.'}), 409
    status = (request.form.get('status') or '').strip()
    review_comment = (request.form.get('review_comment') or '').strip()
    if status not in {'accepted', 'needs_changes'}:
        return jsonify({'error': 'Invalid review status'}), 400
    if len(review_comment) > 1000:
        return jsonify({'error': 'Review comment is too long'}), 400
    if status == 'needs_changes' and not review_comment:
        return jsonify({'error': 'Add a comment so the employee knows what to change.'}), 400
    try:
        if db.review_leave_request(leave_id, status, review_comment, request.form.get('expected_submitted_at'), actor_user_id=audit_actor()) is None:
            abort(404)
    except db.WorkflowConflict as exc:
        return jsonify({'error': str(exc)}), 409
    year, month = _admin_period_from_form()
    return redirect(url_for('admin_page', year=year, month=month))


@app.route('/admin/timesheets/<int:ts_id>/reopen', methods=['POST'])
def reopen_timesheet(ts_id):
    if production_access.is_production() and not request.form.get('expected_submitted_at'):
        return jsonify({'error': 'Refresh this request before reopening it.'}), 409
    comment = (request.form.get('review_comment') or '').strip()
    if not comment or len(comment) > 1000:
        return jsonify({'error': 'Add a comment so the employee knows what to change.'}), 400
    try:
        if db.reopen_timesheet(ts_id, comment, request.form.get('expected_submitted_at'), actor_user_id=audit_actor()) is None:
            abort(404)
    except db.WorkflowConflict as exc:
        return jsonify({'error': str(exc)}), 409
    year, month = _admin_period_from_form()
    return redirect(url_for('admin_page', year=year, month=month))


@app.route('/admin/export')
def export_admin_documents():
    period = _parse_period_values(request.args.get('year'), request.args.get('month'))
    if period is None:
        return jsonify({'error': 'Invalid year or month'}), 400
    year, month = period
    files = []
    missing_files = []
    archive_names = set()
    for folder, rows in (
        ('timesheets', db.list_timesheets(year, month)),
        ('leave_requests', db.list_leave_requests_for_month(year, month)),
    ):
        source_folder = 'leave' if folder == 'leave_requests' else folder
        for row in rows:
            archive_name = f"{folder}/{_safe_display_name(row['full_name'])}_{row['filename']}"
            if archive_name in archive_names:
                continue
            storage_key = row['storage_key']
            if storage_key:
                content = document_storage.download(storage_key)
                if content is None:
                    missing_files.append(row['filename'])
                    continue
                source = content
            else:
                source = _stored_file_path(source_folder, row['filename'])
                if source is None:
                    missing_files.append(row['filename'])
                    continue
            archive_names.add(archive_name)
            files.append((source, archive_name))

    if missing_files:
        return jsonify({
            'error': 'Archive could not be created because document files are missing.',
            'missing_files': missing_files,
        }), 409

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as bundle:
        for source, archive_name in files:
            if isinstance(source, bytes):
                bundle.writestr(archive_name, source)
            else:
                bundle.write(source, archive_name)
        bundle.writestr(
            'README.txt',
            f'Urchin Accounting export for {year:04d}-{month:02d}.\n'
            f'Files included: {len(files)}.\n',
        )
    archive.seek(0)
    return send_file(
        archive,
        as_attachment=True,
        download_name=f'Urchin_Accounting_{year:04d}_{month:02d}.zip',
        mimetype='application/zip',
    )


@app.route('/admin/audit')
def audit_page():
    return render_template('audit.html', events=db.list_recent_workflow_events(),
                           users_by_id={row['id']: dict(row) for row in db.list_users()})


@app.route('/admin/users', methods=['GET', 'POST'])
def admin_users():
    error = None
    if request.method == 'POST':
        try:
            db.create_user(
                request.form.get('username', ''),
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


@app.route('/admin/users/<int:user_id>/edit', methods=['GET', 'POST'])
def edit_user(user_id):
    user_to_edit = db.get_user_by_id(user_id)
    if user_to_edit is None:
        abort(404)

    error = None
    if request.method == 'POST':
        requested_role = (request.form.get('role') or '').strip()
        try:
            if production_access.is_production() and request.form.get('username', '').strip().lower() != user_to_edit['username']:
                return jsonify({'error': 'This username is linked to company SSO. Update the identity mapping through the company release process.'}), 409
            updated = db.update_user(
                user_id,
                request.form.get('username', ''),
                request.form.get('full_name', ''),
                requested_role,
            )
            if updated is None:
                abort(404)
            return redirect(url_for('admin_users'))
        except ValueError as exc:
            error = str(exc)

        user_to_edit = {
            **user_to_edit,
            'username': request.form.get('username', ''),
            'full_name': request.form.get('full_name', ''),
            'role': requested_role,
        }

    return render_template(
        'user_edit.html',
        user=current_user(),
        user_to_edit=user_to_edit,
        error=error,
    )


@app.route('/admin/users/<int:user_id>/delete', methods=['POST'])
def delete_user(user_id):
    current = current_user()
    try:
        if not db.delete_user(user_id):
            abort(404)
    except ValueError as exc:
        return render_template(
            'users.html',
            user=current,
            users=db.list_users(),
            error=str(exc),
        ), 400
    return redirect(url_for('admin_users'))


@app.route('/admin/timesheets/<int:ts_id>/download')
def download_timesheet(ts_id):
    row = db.get_timesheet(ts_id)
    if row is None:
        abort(404)
    return _send_stored_document(row, 'timesheets')


@app.route('/admin/timesheets/<int:ts_id>/view')
def view_timesheet(ts_id):
    row = db.get_timesheet(ts_id)
    if row is None:
        abort(404)
    if _document_mimetype(row['filename']) != 'application/pdf':
        return redirect(url_for('download_timesheet', ts_id=ts_id))
    return _send_stored_document(row, 'timesheets', as_attachment=False)


@app.route('/admin/leave/<int:leave_id>/download')
def download_leave(leave_id):
    row = db.get_leave_request(leave_id)
    if row is None:
        abort(404)
    return _send_stored_document(row, 'leave')


@app.route('/admin/leave/<int:leave_id>/view')
def view_leave(leave_id):
    row = db.get_leave_request(leave_id)
    if row is None:
        abort(404)
    return _send_stored_document(row, 'leave', as_attachment=False)


import hr_web
hr_web.register(app, current_user)
import runtime_health
runtime_health.register(app)
import office_web
office_web.register(app, current_user)


if __name__ == '__main__':
    app.run(debug=False, host='127.0.0.1', port=8081)
