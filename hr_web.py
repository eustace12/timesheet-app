"""Routes for the focused People workspace."""
from datetime import date, datetime
import calendar
import csv
import io

from flask import Blueprint, abort, jsonify, redirect, render_template, request, send_file, url_for
from werkzeug.exceptions import HTTPException
from moldova_holidays import HolidayDataUnavailable
import db
import hr_store
from people_experience import overview, assess_leave, team_balances, company_today
import production_access


def register(app, current_user):
    bp = Blueprint('people', __name__)

    def period():
        now = company_today()
        try:
            year = int(request.args.get('year', now.year))
            month = int(request.args.get('month', now.month))
            date(year, month, 1)
        except ValueError:
            abort(400)
        return year, month

    def context(section):
        year, month = period()
        user = current_user()
        values = {'section': section, 'year': year, 'month': month,
                'period_label': f'{calendar.month_name[month]} {year}',
                'today': company_today().isoformat(),
                'balance': hr_store.leave_balance(user['id'], year) if user else None}
        if section == 'balance':
            target = balance_target()
            values['balance_person'] = target
            values['balance'] = hr_store.leave_balance(target['id'], year) if target else None
            if accountant():
                values['team_balances'] = team_balances(year)
        return values

    def accountant():
        if production_access.is_production():
            return bool(current_user() and current_user()['role'] == 'admin')
        from flask import session
        return session.get('demo_role') == 'accountant'

    def balance_target():
        user = current_user()
        raw = request.values.get('user_id')
        if raw:
            try:
                target_id = int(raw)
            except ValueError:
                abort(400)
            if production_access.is_production() and not accountant() and (not user or target_id != user['id']):
                abort(403)
            user = db.get_user_by_id(target_id)
            if user is None:
                abort(404)
        return user

    def selected_user():
        user = current_user()
        if user is None:
            abort(400, description='Add a person in People first.')
        return user

    def mutation(action):
        try:
            return jsonify({'ok': True, **action()})
        except LookupError:
            abort(404)
        except ValueError as exc:
            message = str(exc)
            return jsonify({'error': message}), 409 if 'locked' in message else 400

    @bp.route('/')
    @bp.route('/workspace')
    def dashboard():
        from office_web import week_bounds
        import office_store
        user = current_user()
        office_available = True
        try:
            first, last = week_bounds()
            office_days = [row for row in office_store.own_plans(user['id'], first, last) if row['planned']] if user else []
        except Exception:
            office_days, office_available = [], False
            app.logger.warning('Optional office overview is unavailable.')
        return render_template('overview.html', **overview(user), office_count=len(office_days), office_available=office_available)

    @bp.route('/leave/assessment')
    def leave_assessment():
        try:
            user_id = int(request.args['user_id']) if request.args.get('user_id') else selected_user()['id']
            if db.get_user_by_id(user_id) is None:
                abort(404)
            start = date.fromisoformat(request.args.get('start_date', ''))
            end = date.fromisoformat(request.args.get('end_date', ''))
            request_id = int(request.args['request_id']) if request.args.get('request_id') else None
            assessment = assess_leave(user_id, start, end, request.args.get('leave_type', 'Vacation'), request_id)
            if production_access.is_production() and not accountant():
                assessment['team'] = [person for person in assessment['team'] if person['status'] == 'accepted']
                assessment['team_count'] = len(assessment['team'])
            return jsonify(assessment)
        except HolidayDataUnavailable:
            raise
        except (ValueError, TypeError) as exc:
            return jsonify({'error': str(exc) or 'Choose valid dates.'}), 400
        except HTTPException:
            raise
        except Exception:
            app.logger.error('Leave request assessment is temporarily unavailable.')
            return jsonify({'error': 'We could not check your leave dates. Your draft is kept; please try again.'}), 503

    @bp.route('/time-logs', methods=['GET', 'POST'])
    def time_logs():
        if request.method == 'POST':
            def create():
                row = hr_store.save_entry(selected_user()['id'], request.form)
                return {'id': row['id'], 'work_date': row['work_date']}
            return mutation(create)
        return redirect(url_for('index'))

    @bp.route('/time-logs/<int:entry_id>/edit', methods=['POST'])
    def edit_log(entry_id):
        return mutation(lambda: {'id': hr_store.save_entry(selected_user()['id'], request.form, entry_id)['id']})

    @bp.route('/time-logs/<int:entry_id>/delete', methods=['POST'])
    def delete_log(entry_id):
        def remove():
            hr_store.delete_entry(selected_user()['id'], entry_id)
            return {}
        return mutation(remove)

    @bp.route('/time-logs/export')
    def export_logs():
        user = selected_user()
        year, month = period()
        output = io.StringIO(newline='')
        writer = csv.writer(output)
        writer.writerow(['Date', 'Employee', 'Project', 'Task', 'Hours', 'Billable', 'Notes'])

        def safe(value):
            text = str(value)
            return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) or text.startswith(('\t', '\r', '\n')) else text

        for row in hr_store.entries(user['id'], year, month):
            writer.writerow([row['work_date'], safe(user['full_name']), safe(row['project']), safe(row['task']),
                             f"{row['minutes'] // 60}:{row['minutes'] % 60:02d}", 'Yes' if row['billable'] else 'No', safe(row['notes'])])
        return send_file(io.BytesIO(output.getvalue().encode('utf-8-sig')), as_attachment=True,
                         download_name=f'time-logs-{year}-{month:02d}.csv', mimetype='text/csv')

    @bp.route('/leave-balances', methods=['GET', 'POST'])
    def leave_balances():
        if request.method == 'POST':
            try:
                year = int(request.form.get('year', ''))
                target = balance_target()
                if target is None:
                    abort(400)
                actor = current_user()['id'] if production_access.is_production() else None
                hr_store.set_entitlement(target['id'], year, request.form.get('days', ''), actor_user_id=actor)
            except ValueError as exc:
                return jsonify({'error': str(exc)}), 400
            return redirect(url_for('people.leave_balances', year=year, user_id=target['id']))
        return render_template('hr_workspace.html', **context('balance'))

    @bp.route('/team-calendar')
    def team_calendar():
        values = context('calendar')
        leave = db.list_leave_requests_for_month(values['year'], values['month'])
        if production_access.is_production() and not accountant():
            # Planning visibility never exposes another employee's HR notes or pending requests.
            leave = [{**dict(row), 'comment': '', 'review_comment': '', 'leave_type': 'Time off'}
                     for row in leave if row['status'] == 'accepted']
        weeks = []
        for week in calendar.Calendar().monthdatescalendar(values['year'], values['month']):
            weeks.append([{'date': day, 'events': [row for row in leave
                           if row['status'] in ('submitted', 'accepted') and row['start_date'] <= day.isoformat() <= row['end_date']]}
                          for day in week])
        values['weeks'] = weeks
        return render_template('hr_workspace.html', **values)

    app.register_blueprint(bp)
