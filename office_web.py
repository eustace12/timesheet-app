"""Voluntary office planning; independent of time and leave accounting."""
from datetime import date, timedelta
import calendar

from flask import Blueprint, jsonify, render_template, request

import db
import office_store
from people_experience import company_today


def week_bounds(raw=None):
    try:
        chosen = date.fromisoformat(raw) if raw else company_today()
        first = chosen - timedelta(days=chosen.weekday())
        last = first + timedelta(days=6)
        if first < date(2020, 1, 1) or last > date(2099, 12, 31):
            raise ValueError
        return first, last
    except (TypeError, ValueError, OverflowError):
        raise ValueError('Choose a valid week between 2020 and 2099.') from None


def week_data(user, raw=None):
    first, last = week_bounds(raw)
    team = office_store.plans(first, last)
    own = office_store.own_plans(user['id'], first, last) if user else []
    today = company_today()
    days = []
    for offset in range(7):
        day = first + timedelta(days=offset)
        people = [row for row in team if row['work_date'] == day.isoformat()]
        days.append({'date': day.isoformat(), 'weekday': calendar.day_abbr[day.weekday()],
                     'day': day.day, 'month': day.strftime('%b'),
                     'label': day.strftime('%A, %d %B'), 'weekend': offset >= 5,
                     'today': day == today, 'people': people})
    return {'week_start': first.isoformat(), 'week_end': last.isoformat(),
            'period_label': first.strftime('%d %b') + ' – ' + last.strftime('%d %b %Y'),
            'days': days, 'my_plans': {row['work_date']: row for row in own},
            'person_id': user['id'] if user else None,
            'person_name': user['full_name'] if user else ''}


def register(app, current_user):
    bp = Blueprint('office', __name__)

    @bp.get('/office')
    def page():
        try:
            data = week_data(current_user(), request.args.get('week'))
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400
        except Exception:
            app.logger.error('Office planning is unavailable.')
            return jsonify({'error': 'Office plans are temporarily unavailable. Please try again shortly.'}), 503
        first = date.fromisoformat(data['week_start'])
        return render_template('office.html', office=data,
                               previous_week=(first - timedelta(days=7)).isoformat(),
                               next_week=(first + timedelta(days=7)).isoformat())

    @bp.get('/office/data')
    def data():
        try:
            return jsonify(week_data(current_user(), request.args.get('week')))
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400
        except Exception:
            app.logger.error('Office planning is unavailable.')
            return jsonify({'error': 'Office plans are temporarily unavailable. Please try again shortly.'}), 503

    @bp.post('/office/plans')
    def save():
        user = current_user()
        if user is None:
            return jsonify({'error': 'Select an employee before planning a visit.'}), 400
        values = request.form
        if values.get('expected_person_id') and values['expected_person_id'] != str(user['id']):
            return jsonify({'error': 'The selected employee changed. Refresh this week before continuing.'}), 409
        if values.get('planned') not in {'0', '1'} or 'expected_updated_at' not in values:
            return jsonify({'error': 'Refresh this week before changing your plan.'}), 400
        try:
            row = office_store.save_plan(user['id'], values.get('work_date', ''),
                values['planned'] == '1', values.get('start_time'), values.get('end_time'),
                values.get('expected_updated_at', ''))
            return jsonify({'ok': True, 'plan': row})
        except db.WorkflowConflict as exc:
            return jsonify({'error': str(exc)}), 409
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400
        except Exception:
            app.logger.error('Office plan persistence is unavailable.')
            return jsonify({'error': 'Could not save your plan. Your changes are kept; please try again.'}), 503

    app.register_blueprint(bp)
