"""Verify production RPC availability without creating or changing any record."""
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if urlsplit(os.environ['SUPABASE_URL']).hostname != 'rdoamaeixdzkddvnlvto.supabase.co':
    raise SystemExit('Only the isolated fictional-data demo is allowed.')
import db
import hr_store

# Every operation is deliberately rejected before any insert/update. The
# transaction rolls back; a missing employee cannot generate a business record.
missing_person = -1
assert db.get_user_by_id(missing_person) is None
checks = [
    ('timesheet', lambda: db.upsert_timesheet(user_id=missing_person, year=2099, month=11,
        fmt='pdf', vacation='', sick='', custom_days='', custom_reason='',
        working_days=1, total_hours=8, filename='never-created.pdf', leave_snapshot=[])),
    ('leave', lambda: db.create_leave_request(user_id=missing_person, start_date='2026-11-09',
        end_date='2026-11-09', leave_type='Vacation', comment='', filename='never-created.pdf')),
    ('allowance', lambda: hr_store.set_entitlement(missing_person, 2099, '20')),
]
for label, action in checks:
    try:
        action()
    except db.WorkflowConflict:
        print(label + ' RPC reachable and rejected missing employee atomically: PASS')
    else:
        raise SystemExit(label + ' RPC did not enforce the workflow contract.')
result = db._workflow_rpc('review_document_atomic', {'p_kind':'timesheets', 'p_id':-1,
    'p_status':'accepted', 'p_comment':'', 'p_reviewed_at':'2026-11-02T00:00:00+00:00',
    'p_expected_submitted_at':None, 'p_reopen':False, 'p_holidays':[], 'p_actor_user_id':None})
assert result is None
print('Review RPC reachable; absent document stays absent: PASS')
