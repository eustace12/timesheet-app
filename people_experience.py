"""Read models for employee overview and contextual leave decisions."""
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import calendar

import db
import hr_store
from moldova_holidays import get_moldova_holidays


def company_today():
    return datetime.now(ZoneInfo('Europe/Chisinau')).date()


def leave_window(start, end):
    """Fetch each covered month once, retaining each request once."""
    rows = {}
    cursor = start.replace(day=1)
    while cursor <= end:
        for row in db.list_leave_requests_for_month(cursor.year, cursor.month):
            if row['start_date'] <= end.isoformat() and row['end_date'] >= start.isoformat():
                rows[row['id']] = dict(row)
        if (cursor.year, cursor.month) == (end.year, end.month):
            break
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)
    return list(rows.values())


def assess_leave(user_id, start, end, leave_type='Vacation', request_id=None):
    if end < start:
        raise ValueError('End date must be on or after start date.')
    if (end - start).days > 366:
        raise ValueError('Choose a date range of up to one year.')
    if leave_type not in {'Vacation', 'Unpaid', 'Other'}:
        raise ValueError('Choose a valid leave type.')
    requests = leave_window(start, end)
    own = [row for row in requests if row['user_id'] == user_id and row['status'] in ('accepted','submitted')]
    existing = next((row for row in own if row['id'] == request_id), None)
    if request_id is not None and (existing is None or existing['start_date'] != start.isoformat() or existing['end_date'] != end.isoformat()):
        raise ValueError('The request does not match this employee and date range.')
    overlaps = [row for row in own if row['id'] != request_id]
    days_by_year = defaultdict(int)
    holidays = {year: get_moldova_holidays(year) for year in range(start.year, end.year + 1)}
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        if day.weekday() < 5 and day not in holidays[day.year]:
            days_by_year[day.year] += 1
    balances = []
    if leave_type == 'Vacation':
        for year in range(start.year, end.year + 1):
            balance = hr_store.leave_balance(user_id, year)
            already_reserved = existing is not None and existing['leave_type'] == 'Vacation'
            after = balance['available']
            if after is not None and not already_reserved:
                after -= days_by_year[year]
            balances.append({'year':year, **balance, 'requested':days_by_year[year], 'after_request':after})
    team = [{'full_name': row['full_name'], 'start_date':row['start_date'], 'end_date':row['end_date'], 'status':row['status']}
            for row in requests if row['user_id'] != user_id and row['status'] in ('accepted','submitted')]
    team.sort(key=lambda row: (row['start_date'], row['full_name']))
    days = sum(days_by_year.values())
    reason = ''
    if overlaps:
        reason = 'These dates overlap one of your existing requests.'
    elif leave_type == 'Vacation' and not days:
        reason = 'This annual leave has no working days. Choose different dates.'
    elif any(balance['after_request'] is not None and balance['after_request'] < 0 for balance in balances):
        reason = 'This annual leave exceeds the available allowance. Choose fewer days or ask your accountant to check the allowance.'
    return {'working_days':days, 'calendar_days':(end-start).days+1, 'balances':balances,
            'team':team[:12], 'team_count':len(team), 'can_submit':not reason, 'reason':reason,
            'overlap_count':len(overlaps), 'already_reserved':existing is not None}


def overview(user):
    today = company_today()
    rows = [dict(row) for row in db.list_user_timesheets(user['id'])] if user else []
    leaves = [dict(row) for row in db.list_user_leave_requests(user['id'])] if user else []
    sheet = next((row for row in rows if row['year'] == today.year and row['month'] == today.month), None)
    earlier = [{**row, 'period_label': f"{calendar.month_name[row['month']]} {row['year']}"}
               for row in rows if (row['year'], row['month']) < (today.year, today.month)
               and row['status'] in ('needs_changes', 'submitted')]
    earlier.sort(key=lambda row: (row['status'] != 'needs_changes', -row['year'], -row['month']))
    upcoming = sorted((row for row in leaves if row['status'] in ('submitted','accepted') and row['end_date'] >= today.isoformat()), key=lambda row: row['start_date'])
    team = [row for row in leave_window(today, today) if row['status'] == 'accepted' and (not user or row['user_id'] != user['id'])]
    activity = [{'kind':'timesheet','title':calendar.month_name[row['month']]+' '+str(row['year']),
                 'status':row['status'],'comment':row['review_comment'],'date':row['reviewed_at'] or row['submitted_at'],
                 'href':f"/timesheet?year={row['year']}&month={row['month']}"} for row in rows]
    activity += [{'kind':'leave','title':row['start_date']+' → '+row['end_date'],'status':row['status'],
                  'comment':row['review_comment'],'date':row['reviewed_at'] or row['submitted_at'],'href':'/leave'} for row in leaves]
    activity.sort(key=lambda item: str(item['date']), reverse=True)
    return {'today':today, 'period_label':today.strftime('%B %Y'), 'sheet':sheet,
            'balance':hr_store.leave_balance(user['id'],today.year) if user else None,
            'upcoming':upcoming[:3], 'team_out':team, 'activity':activity[:5],
            'earlier_submissions':earlier}


def team_balances(year):
    """Accountant read model; the caller must enforce team-data access."""
    return [{'person_id': person['id'], 'full_name': person['full_name'],
             'balance': hr_store.leave_balance(person['id'], year)}
            for person in db.list_users()]
