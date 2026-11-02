"""SSO trust boundary and permissions, using isolated SQLite employee records."""
import json

from flask import Flask, jsonify, render_template_string, session
import pytest

import db
from production_access import configure_access, production_current_user


KEY = 'test-session-signing-key-32-characters-only'
PROXY_KEY = 'test-proxy-shared-key-32-characters-only'


def make_app(monkeypatch, **callbacks):
    monkeypatch.setenv('APP_MODE', 'production')
    monkeypatch.setenv('SECRET_KEY', KEY)
    monkeypatch.setenv('SSO_PROXY_SECRET', PROXY_KEY)
    monkeypatch.setenv('SSO_EMAIL_MAPPING_JSON', json.dumps({'employee@example.test': 'employee', 'admin@example.test': 'admin'}))
    app = Flask(__name__)
    app.config['TESTING'] = True
    configure_access(app, db.get_user_by_username, **callbacks)

    @app.route('/', methods=['GET', 'POST'])
    @app.route('/admin', methods=['GET', 'POST'])
    @app.route('/admin/users', methods=['GET', 'POST'])
    @app.route('/admin/timesheets/1/view')
    @app.route('/admin/leave/1/download')
    @app.route('/leave-balances', methods=['GET', 'POST'])
    @app.route('/leave/assessment')
    @app.route('/profile', methods=['POST'])
    @app.route('/demo/role', methods=['POST'])
    def workspace():
        user = production_current_user()
        return jsonify({'user_id': user['id'], 'role': user['role']})

    @app.get('/health/live')
    def live():
        return jsonify({'status': 'ok'})

    @app.get('/context')
    def context():
        return render_template_string('{{is_production}}|{{demo_role}}|{{can_manage_people}}')

    return app


def identity(email='employee@example.test', **headers):
    return {'X-Urchin-Proxy-Secret': PROXY_KEY, 'X-Auth-Request-Email': email, **headers}


@pytest.mark.parametrize('headers', [{}, {'X-Auth-Request-Email': 'admin@example.test'}, {'X-Urchin-Proxy-Secret': 'wrong', 'X-Auth-Request-Email': 'admin@example.test'}])
def test_identity_header_without_proxy_proof_is_never_trusted(monkeypatch, headers):
    app = make_app(monkeypatch)
    response = app.test_client().get('/admin', headers=headers)
    assert response.status_code == 401
    assert b'Company sign-in required' in response.data


@pytest.mark.parametrize('email', ['employee', 'employee@example.test,admin@example.test', ' employee@example.test', 'employee@example.test ', 'employee@@example.test', 'employee@localhost', ''])
def test_malformed_or_multiple_identity_headers_fail_closed(monkeypatch, email):
    response = make_app(monkeypatch).test_client().get('/', headers=identity(email))
    assert response.status_code == 401


def test_unmapped_email_does_not_match_another_person_by_local_part(monkeypatch):
    response = make_app(monkeypatch).test_client().get('/', headers=identity('employee@outside.test'))
    assert response.status_code == 403


def test_exact_email_username_supports_company_accounts_without_mapping(monkeypatch):
    app = make_app(monkeypatch)
    user = db.create_user('new.person@example.test', 'New Person', 'employee')
    response = app.test_client().get('/', headers=identity('NEW.PERSON@example.test'))
    assert response.status_code == 200
    assert response.json['user_id'] == user['id']


def test_session_and_role_header_cannot_impersonate_accountant(monkeypatch):
    app = make_app(monkeypatch)
    client = app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = db.get_user_by_username('admin')['id']
        session['demo_role'] = 'accountant'
    headers = identity(**{'X-Auth-Request-Groups': 'admin', 'X-Auth-Request-Role': 'admin'})
    response = client.get('/', headers=headers)
    assert response.json['role'] == 'employee'
    assert client.get('/admin', headers=headers).status_code == 403
    assert client.get('/admin/users', headers=headers).status_code == 403


def test_employee_cannot_switch_profile_role_or_edit_allowance(monkeypatch):
    client = make_app(monkeypatch).test_client()
    for path in ('/profile', '/demo/role'):
        assert client.post(path, headers=identity(), data={'user_id': '1', 'role': 'accountant'}).status_code == 404
    assert client.post('/leave-balances', headers=identity()).status_code == 403
    assert client.get('/leave-balances', headers=identity()).status_code == 200


def test_accountant_permissions_are_read_from_employee_record(monkeypatch):
    app = make_app(monkeypatch)
    client = app.test_client()
    assert client.get('/admin', headers=identity('admin@example.test')).status_code == 200
    assert client.post('/leave-balances', headers=identity('admin@example.test')).status_code == 200
    assert client.get('/context', headers=identity('admin@example.test')).text == 'True|accountant|True'
    assert client.get('/context', headers=identity()).text == 'True|employee|False'


def test_saved_document_preview_requires_ownership(monkeypatch):
    own_id = db.get_user_by_username('employee')['id']
    app = make_app(monkeypatch, find_timesheet=lambda _id: {'user_id': own_id}, find_leave=lambda _id: {'user_id': 999})
    client = app.test_client()
    assert client.get('/admin/timesheets/1/view', headers=identity()).status_code == 200
    assert client.get('/admin/leave/1/download', headers=identity()).status_code == 403


def test_missing_document_returns_clear_404(monkeypatch):
    app = make_app(monkeypatch, find_timesheet=lambda _id: None)
    assert app.test_client().get('/admin/timesheets/1/view', headers=identity()).status_code == 404


def test_employee_cannot_assess_others_leave(monkeypatch):
    client = make_app(monkeypatch).test_client()
    own_id = db.get_user_by_username('employee')['id']
    assert client.get(f'/leave/assessment?user_id={own_id}', headers=identity()).status_code == 200
    assert client.get('/leave/assessment?user_id=999', headers=identity()).status_code == 403
    assert client.get('/leave/assessment?user_id=invalid', headers=identity()).status_code == 403
    assert client.get('/leave/assessment?user_id=999', headers=identity('admin@example.test')).status_code == 200


def test_probes_stay_public_but_business_data_is_not_cached(monkeypatch):
    app = make_app(monkeypatch)
    assert app.test_client().get('/health/live').status_code == 200
    response = app.test_client().get('/', headers=identity())
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    assert 'Secure' in response.headers['Set-Cookie']
    assert 'HttpOnly' in response.headers['Set-Cookie']


@pytest.mark.parametrize('variable,value', [('SECRET_KEY', 'short'), ('SSO_PROXY_SECRET', 'short'), ('SSO_PROXY_SECRET', KEY), ('SSO_EMAIL_MAPPING_JSON', '{broken'), ('SSO_EMAIL_MAPPING_JSON', '{"employee": "employee"}'), ('SSO_EMAIL_MAPPING_JSON', '[]')])
def test_invalid_production_configuration_stops_startup(monkeypatch, variable, value):
    make_app(monkeypatch)
    monkeypatch.setenv(variable, value)
    with pytest.raises(RuntimeError):
        configure_access(Flask('invalid'), db.get_user_by_username)


def test_unknown_mode_cannot_silently_enable_demo(monkeypatch):
    monkeypatch.setenv('APP_MODE', 'prodution')
    with pytest.raises(RuntimeError):
        configure_access(Flask('invalid'), db.get_user_by_username)


def test_demo_mode_keeps_existing_passwordless_behavior(monkeypatch):
    monkeypatch.setenv('APP_MODE', 'demo')
    app = Flask('demo')
    configure_access(app, db.get_user_by_username)
    app.add_url_rule('/admin', view_func=lambda: 'demo')
    assert app.test_client().get('/admin').status_code == 200


def test_identity_change_rotates_existing_session(monkeypatch):
    client = make_app(monkeypatch).test_client()
    client.get('/', headers=identity())
    with client.session_transaction() as existing:
        existing['_csrf_token'] = 'previous-person-token'
    client.get('/', headers=identity('admin@example.test'))
    with client.session_transaction() as changed:
        assert '_csrf_token' not in changed
        assert changed['production_user_id'] == db.get_user_by_username('admin')['id']
