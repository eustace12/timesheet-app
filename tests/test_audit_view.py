"""The read-only journal makes decisions and their authors inspectable."""
from flask import render_template
import pytest

from web_app import app


def render_audit(events, users=None):
    with app.test_request_context('/admin/audit'):
        return render_template('audit.html', events=events, users_by_id=users or {})


def event(**changes):
    values = {'kind': 'leave_requests', 'document_id': 7, 'user_id': 2,
              'actor_user_id': 1, 'action': 'reviewed', 'status': 'needs_changes',
              'comment': 'Please move the first day.', 'created_at': '2026-11-02T13:45:06+00:00'}
    return {**values, **changes}


def test_audit_shows_readable_decision_actor_date_and_escaped_comment():
    page = render_audit([event(comment='<script>alert(1)</script>')],
                        {1: {'full_name': 'Company Accountant'}, 2: {'full_name': 'Alice Martin'}})
    assert 'Audit trail' in page
    assert 'Company Accountant' in page and 'Alice Martin' in page
    assert '02 Nov 2026' in page and '13:45:06 UTC' in page
    assert 'Time-off request #7' in page
    assert 'Changes requested' in page
    assert '<script>alert(1)</script>' not in page
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in page
    assert '/review' not in page


@pytest.mark.parametrize('actor', [None, 999])
def test_unknown_actor_is_honestly_marked_as_legacy(actor):
    page = render_audit([event(actor_user_id=actor)], {2: 'Alice Martin'})
    assert 'Demo / legacy' in page
    assert 'Alice Martin' in page


def test_empty_journal_explains_which_actions_will_appear():
    page = render_audit([])
    assert 'No recorded activity yet' in page
    assert 'Earlier activity is not reconstructed' in page


def test_allowance_change_is_readable_without_a_document_link():
    page = render_audit([event(kind='entitlement', document_id=2026,
                              action='allowance_updated', status='updated',
                              comment='Annual allowance: 25 days')])
    assert 'Annual allowance · 2026' in page
    assert 'Allowance updated' in page and 'Annual allowance: 25 days' in page
    assert 'Employee #2' in page
