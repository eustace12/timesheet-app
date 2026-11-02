"""Server-owned absence normalization for editable monthly documents."""
import calendar
from datetime import date, timedelta

import db


class AbsenceDataUnavailable(RuntimeError):
    pass


def parse_ranges(raw, year, month):
    """Bound every range before expansion; never silently ignore reversed dates."""
    maximum = calendar.monthrange(year, month)[1]
    result = set()
    for part in (raw or '').split(','):
        if not part.strip():
            continue
        values = part.strip().split('-')
        if len(values) not in (1, 2) or not all(value.strip().isdigit() for value in values):
            raise ValueError('Choose valid absence dates.')
        start, end = int(values[0]), int(values[-1])
        if not 1 <= start <= end <= maximum:
            raise ValueError('Absence dates must be within the selected month.')
        result.update(range(start, end + 1))
    return result


def compact(days):
    ranges = []
    for day in sorted(days):
        if ranges and day == ranges[-1][1] + 1:
            ranges[-1][1] = day
        else:
            ranges.append([day, day])
    return ','.join(str(start) if start == end else f'{start}-{end}' for start, end in ranges)


def approved_for_month(user, year, month):
    if not user:
        return []
    try:
        return [dict(row) for row in db.list_approved_leave_ranges(user['id'], year, month)]
    except Exception as exc:
        raise AbsenceDataUnavailable('Approved absence data is unavailable. Your draft is kept; try again shortly.') from exc


def approved_days(rows, year, month):
    result = {}
    first, last = date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
    for row in rows:
        start = max(first, date.fromisoformat(row['start_date']))
        end = min(last, date.fromisoformat(row['end_date']))
        while start <= end:
            kind = row.get('leave_type', 'Vacation')
            if start.day in result and result[start.day] != kind:
                raise ValueError('Approved absence types conflict. Contact the accountant before submitting.')
            result[start.day] = kind
            if start == end:
                break
            start += timedelta(days=1)
    return result


def normalize(user, year, month, vacation, sick, custom, reason):
    snapshot = approved_for_month(user, year, month)
    approved = approved_days(snapshot, year, month)
    vacation_days, sick_days, other_days = (parse_ranges(raw, year, month) for raw in (vacation, sick, custom))
    if other_days and not reason:
        raise ValueError('Add a reason for the other absence dates.')
    approved_vacation = {day for day, kind in approved.items() if kind == 'Vacation'}
    approved_other = set(approved) - approved_vacation
    # The server is authoritative even if a stale or altered client omits dates.
    vacation_days.update(approved_vacation)
    if (vacation_days & sick_days or vacation_days & other_days or sick_days & other_days
            or approved_other & (vacation_days | sick_days | other_days)):
        raise ValueError('Absence types overlap. Remove the conflicting dates before submitting.')
    custom_absences = {date(year, month, day): reason for day in other_days}
    custom_absences.update({date(year, month, day): 'Unpaid leave' if kind == 'Unpaid' else 'Other approved leave'
                            for day, kind in approved.items() if kind != 'Vacation'})
    return {
        'vacation_raw': compact(vacation_days), 'sick_raw': compact(sick_days),
        'custom_days_raw': compact(other_days), 'custom_absences': custom_absences,
        'leave_snapshot': snapshot,
        'vacation_dates': [(date(year, month, day), date(year, month, day)) for day in sorted(vacation_days)],
        'sick_dates': [(date(year, month, day), date(year, month, day)) for day in sorted(sick_days)],
    }
