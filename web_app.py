#!/usr/bin/env python3
"""
Web interface for Timesheet Generator
Веб-интерфейс для генератора таймшитов
"""

from collections import defaultdict
from time import time

from flask import Flask, render_template, request, send_file, jsonify, send_from_directory
from pathlib import Path
from datetime import date, datetime
import calendar
import os

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

GENERATE_RATE_LIMIT = int(os.environ.get('GENERATE_RATE_LIMIT', '10'))
GENERATE_RATE_WINDOW = int(os.environ.get('GENERATE_RATE_WINDOW', '60'))
_generate_hits: dict[str, list[float]] = defaultdict(list)

if os.environ.get('VERCEL'):
    OUTPUT_DIR = Path('/tmp/generated')
else:
    OUTPUT_DIR = Path(__file__).parent / 'generated'
OUTPUT_DIR.mkdir(exist_ok=True)

warmup_holiday_cache()


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


@app.route('/favicon.ico')
def favicon():
    return send_from_directory(STATIC_DIR, 'favicon.ico', mimetype='image/vnd.microsoft.icon')


@app.route('/')
def index():
    current_year = datetime.now().year
    current_month = datetime.now().month
    return render_template(
        'index.html',
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
def generate_timesheet():
    limited = _check_generate_rate_limit()
    if limited:
        return limited

    try:
        year = int(request.form.get('year'))
        month = int(request.form.get('month'))
        employee_name = request.form.get('employee_name', '').strip()
        company_name = request.form.get('company_name', 'Urchin Systems SA').strip()
        hours_per_day = int(request.form.get('hours_per_day', 8))
        holiday_scope = _parse_holiday_scope(request.form.get('holiday_scope'))

        if not employee_name:
            return jsonify({'error': 'Employee name is required'}), 400

        vacation_dates = []
        vacation_input = request.form.get('vacation', '').strip()
        if vacation_input:
            for date_range in vacation_input.split(','):
                date_range = date_range.strip()
                if '-' in date_range:
                    start_day, end_day = map(int, date_range.split('-'))
                    vacation_dates.append((date(year, month, start_day), date(year, month, end_day)))
                else:
                    day = int(date_range)
                    single_date = date(year, month, day)
                    vacation_dates.append((single_date, single_date))

        sick_dates = []
        sick_input = request.form.get('sick', '').strip()
        if sick_input:
            for date_range in sick_input.split(','):
                date_range = date_range.strip()
                if '-' in date_range:
                    start_day, end_day = map(int, date_range.split('-'))
                    sick_dates.append((date(year, month, start_day), date(year, month, end_day)))
                else:
                    day = int(date_range)
                    single_date = date(year, month, day)
                    sick_dates.append((single_date, single_date))

        custom_absences = {}
        custom_days = request.form.get('custom_days', '').strip()
        custom_reason = request.form.get('custom_reason', '').strip()
        if custom_days and custom_reason:
            for day_str in custom_days.split(','):
                day = int(day_str.strip())
                custom_absences[date(year, month, day)] = custom_reason

        output_format = request.form.get('format', 'pdf').strip().lower()
        if output_format not in ('pdf', 'xlsx'):
            return jsonify({'error': 'Invalid format. Use pdf or xlsx.'}), 400

        ext = 'xlsx' if output_format == 'xlsx' else 'pdf'
        filename = build_timesheet_filename(employee_name, year, month, ext)
        output_path = OUTPUT_DIR / filename

        gen_cls = ExcelTimesheetGenerator if output_format == 'xlsx' else PDFTimesheetGenerator
        generator = gen_cls(
            year=year,
            month=month,
            employee_name=employee_name,
            company_name=company_name,
            hours_per_day=hours_per_day,
            vacation_dates=vacation_dates,
            sick_dates=sick_dates,
            custom_absences=custom_absences,
            holiday_scope=holiday_scope,
        )
        generator.generate(output_path)

        mimetype = (
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            if output_format == 'xlsx'
            else 'application/pdf'
        )
        return send_file(output_path, as_attachment=True, download_name=filename, mimetype=mimetype)

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/preview', methods=['POST'])
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
def download_file(filename):
    file_path = OUTPUT_DIR / filename
    if file_path.exists():
        return send_file(file_path, as_attachment=True)
    return "File not found. On serverless platforms, files must be downloaded immediately after generation.", 404


@app.route('/holidays/<int:year>')
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


if __name__ == '__main__':
    print("Starting Timesheet Generator Web Interface...")
    print("Open in browser: http://localhost:8080")
    print("Press Ctrl+C to stop")
    app.run(debug=True, host='0.0.0.0', port=8080)
