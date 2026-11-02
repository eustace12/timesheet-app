"""Managed account login with mocked provider and real isolated session storage."""
import json
import time
from types import SimpleNamespace

from flask import Flask, jsonify
import pytest

import account_auth
import account_sessions
import db
import production_access


EMPLOYEE_UUID = '11111111-1111-4111-8111-111111111111'
ADMIN_UUID = '22222222-2222-4222-8222-222222222222'
UNKNOWN_UUID = '33333333-3333-4333-8333-333333333333'
SIGNING_KEY = 'synthetic-account-session-signing-key-for-tests'


class ProviderError(Exception):
    status = 400


class FakeProvider:
    def __init__(self):
        self.auth = self
        self.tokens = {}
        self.calls = []
        self.outage = False

    def sign_in_with_password(self, credentials):
        self.calls.append('sign_in')
        if self.outage:
            raise TimeoutError('synthetic provider timeout')
        if credentials['password'] != 'synthetic-password':
            raise ProviderError('invalid credentials from fictional provider')
        uid = ADMIN_UUID if credentials['email'] == 'admin@example.test' else UNKNOWN_UUID if credentials['email'] == 'unmapped@example.test' else EMPLOYEE_UUID
        token = 'synthetic-access-token-' + str(len(self.tokens))
        user = SimpleNamespace(id=uid, user_metadata={'role': 'admin'}, app_metadata={})
        self.tokens[token] = user
        return SimpleNamespace(user=user, session=SimpleNamespace(access_token=token,
            refresh_token='never-persist-this-synthetic-refresh-token', expires_at=int(time.time()) + 600))

    def get_user(self, token):
        self.calls.append('get_user')
        if self.outage:
            raise TimeoutError('synthetic provider timeout')
        if token not in self.tokens:
            raise ProviderError('synthetic revoked credential')
        return SimpleNamespace(user=self.tokens[token])


@pytest.fixture()
def account_app(monkeypatch):
    monkeypatch.setenv('APP_MODE', 'production')
    monkeypatch.setenv('AUTH_MODE', 'account')
    monkeypatch.setenv('SECRET_KEY', SIGNING_KEY)
    monkeypatch.setenv('SUPABASE_URL', 'https://fictional-auth.example.test')
    monkeypatch.setenv('SUPABASE_PUBLISHABLE_KEY', 'synthetic-public-test-key')
    # Keep persistence local; the provider boundary is replaced below.
    monkeypatch.setattr(db, '_using_supabase', lambda: False)
    monkeypatch.setenv('AUTH_ACCOUNT_MAPPING_JSON', json.dumps({EMPLOYEE_UUID: 'employee', ADMIN_UUID: 'admin'}))
    provider = FakeProvider()
    monkeypatch.setattr(account_auth, '_auth_client', lambda: provider)
    app = Flask(__name__, template_folder='../templates', static_folder='../static')
    app.config['TESTING'] = True
    production_access.configure_access(app, db.get_user_by_username,
        find_timesheet=lambda _id: {'user_id': db.get_user_by_username('employee')['id']})

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        return app.extensions['account_access'].login()

    @app.post('/logout')
    def logout():
        return app.extensions['account_access'].logout()

    @app.get('/')
    @app.get('/admin')
    @app.get('/admin/timesheets/1/view')
    @app.get('/download/fictional.pdf')
    @app.get('/timesheets/period')
    @app.route('/profile', methods=['POST'])
    @app.route('/demo/role', methods=['POST'])
    @app.route('/generate', methods=['POST'])
    @app.route('/preview', methods=['POST'])
    def workspace():
        person = production_access.production_current_user()
        return jsonify({'person_id': person['id'], 'role': person['role']})

    app.synthetic_provider = provider
    return app


def login(client, email='employee@example.test', password='synthetic-password', **extra):
    client.get('/login')
    with client.session_transaction() as current:
        csrf = current['_csrf_token']
    return client.post('/login', data={'email': email, 'password': password, '_csrf_token': csrf, **extra})


def test_every_business_entry_requires_account_even_with_demo_session(account_app):
    client = account_app.test_client()
    with client.session_transaction() as current:
        current['user_id'] = db.get_user_by_username('admin')['id']
        current['demo_role'] = 'accountant'
    for path in ('/', '/admin', '/admin/timesheets/1/view', '/download/fictional.pdf'):
        response = client.get(path)
        assert response.status_code == 302
        assert response.headers['Location'].startswith('/login')
    for path in ('/timesheets/period',):
        assert client.get(path).status_code == 401
    for path in ('/generate', '/preview'):
        assert client.post(path).status_code == 401


def test_account_login_form_has_no_signup_or_password_echo(account_app):
    client = account_app.test_client()
    page = client.get('/login')
    assert page.status_code == 200
    assert b'type="password"' in page.data and b'name="_csrf_token"' in page.data
    assert b'/signup' not in page.data and b'/register' not in page.data
    failed = login(client, password='wrong-synthetic-password')
    assert failed.status_code == 401
    assert b'wrong-synthetic-password' not in failed.data
    assert b'invalid credentials from fictional provider' not in failed.data


def test_login_requires_csrf_before_contacting_provider(account_app):
    client = account_app.test_client()
    response = client.post('/login', data={'email': 'employee@example.test', 'password': 'synthetic-password'})
    assert response.status_code == 400
    assert account_app.synthetic_provider.calls == []


def test_valid_login_keeps_tokens_only_in_encrypted_server_storage(account_app):
    client = account_app.test_client()
    response = login(client)
    assert response.status_code == 302 and response.headers['Location'] == '/'
    cookie = response.headers['Set-Cookie']
    assert 'Secure' in cookie and 'HttpOnly' in cookie and 'SameSite=Lax' in cookie
    with client.session_transaction() as current:
        assert current['account_sid']
        assert all('token' not in key for key in current if key != '_csrf_token')
        sid = current['account_sid']
    with db.connect() as connection:
        row = dict(connection.execute('SELECT * FROM account_sessions').fetchone())
    assert row['session_hash'] != sid
    assert 'synthetic-access-token' not in str(row)
    assert 'never-persist' not in str(row)
    assert 'synthetic-access-token' not in cookie
    assert client.get('/').json['role'] == 'employee'
    assert client.get('/admin').status_code == 403
    assert client.get('/admin/timesheets/1/view').status_code == 200


def test_roles_and_person_are_not_taken_from_auth_metadata_or_demo_headers(account_app):
    client = account_app.test_client()
    login(client)
    response = client.get('/', headers={'X-Auth-Request-Email': 'admin@example.test', 'X-Auth-Request-Role': 'admin'})
    assert response.json['person_id'] == db.get_user_by_username('employee')['id']
    assert response.json['role'] == 'employee'
    assert client.post('/profile', data={'user_id': '1'}).status_code == 404
    assert client.post('/demo/role', data={'role': 'accountant'}).status_code == 404
    assert client.get('/download/fictional.pdf').status_code == 404


def test_accountant_role_comes_from_linked_person(account_app):
    client = account_app.test_client()
    assert login(client, email='admin@example.test').status_code == 302
    assert client.get('/admin').json['role'] == 'admin'


def test_unmapped_provider_user_cannot_assume_matching_demo_email(account_app):
    client = account_app.test_client()
    assert login(client, email='unmapped@example.test').status_code == 403
    assert client.get('/').status_code == 302
    with db.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM account_sessions').fetchone()[0] == 0


def test_provider_is_checked_on_every_authenticated_request(account_app):
    client = account_app.test_client()
    login(client)
    before = account_app.synthetic_provider.calls.count('get_user')
    assert client.get('/').status_code == 200
    assert client.get('/timesheets/period').status_code == 200
    assert account_app.synthetic_provider.calls.count('get_user') == before + 2
    account_app.synthetic_provider.tokens.clear()
    assert client.get('/timesheets/period').status_code == 401


def test_expired_session_requires_login_and_does_not_refresh(account_app):
    client = account_app.test_client()
    login(client)
    with db.connect() as connection:
        connection.execute('UPDATE account_sessions SET expires_at = ?', (int(time.time()) - 1,))
    assert client.get('/timesheets/period').status_code == 401
    assert client.get('/').status_code == 302


def test_logout_requires_csrf_and_revokes_replayed_cookie(account_app):
    client = account_app.test_client()
    login(client)
    old_cookie = client.get_cookie(account_app.config['SESSION_COOKIE_NAME']).value
    assert client.post('/logout').status_code == 400
    assert client.get('/').status_code == 200
    with client.session_transaction() as current:
        token = current['_csrf_token']
    assert client.post('/logout', data={'_csrf_token': token}).status_code == 302
    replay = account_app.test_client()
    replay.set_cookie(account_app.config['SESSION_COOKIE_NAME'], old_cookie)
    assert replay.get('/timesheets/period').status_code == 401
    with db.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM account_sessions').fetchone()[0] == 0


def test_login_redirect_remains_local(account_app):
    client = account_app.test_client()
    assert login(client, next='https://outside.example.test/').headers['Location'] == '/'
    assert login(client, next='//outside.example.test/').headers['Location'] == '/'
    assert login(client, next='/admin').headers['Location'] == '/admin'


def test_provider_outage_is_retryable_and_does_not_erase_session(account_app):
    client = account_app.test_client()
    login(client)
    account_app.synthetic_provider.outage = True
    assert client.get('/timesheets/period').status_code == 503
    account_app.synthetic_provider.outage = False
    assert client.get('/').status_code == 200


def test_short_local_retry_protection_does_not_replace_provider_limits(account_app):
    client = account_app.test_client()
    results = [login(client, password='wrong-synthetic-password').status_code for _ in range(7)]
    assert 429 in results


def test_unconfigured_account_mapping_cannot_boot_open(monkeypatch):
    monkeypatch.setenv('APP_MODE', 'production')
    monkeypatch.setenv('AUTH_MODE', 'account')
    monkeypatch.setenv('SECRET_KEY', SIGNING_KEY)
    monkeypatch.setenv('SUPABASE_URL', 'https://fictional-auth.example.test')
    monkeypatch.setenv('SUPABASE_PUBLISHABLE_KEY', 'synthetic-public-test-key')
    monkeypatch.setenv('AUTH_ACCOUNT_MAPPING_JSON', '{}')
    with pytest.raises(RuntimeError):
        production_access.configure_access(Flask('invalid'), db.get_user_by_username)


def test_real_company_pages_show_logout_and_keep_account_identity(account_app):
    from test_production_integration import build_production_app
    app = build_production_app()
    client = app.test_client()
    assert client.get('/timesheet?year=2026&month=11').status_code == 302
    assert login(client).status_code == 302
    page = client.get('/timesheet?year=2026&month=11')
    assert page.status_code == 200
    assert b'action="/logout"' in page.data and b'Sign out' in page.data
    assert b'id="demo-role-selector"' not in page.data and b'id="selected_person"' not in page.data
    assert client.get('/admin').status_code == 403
    submitted = client.post('/timesheets/submit', data={'year': 2026, 'month': 11, 'format': 'pdf', 'user_id': 1})
    assert submitted.status_code == 200
    row = db.get_timesheet_for_period(db.get_user_by_username('employee')['id'], 2026, 11)
    assert row and client.get(f'/history/timesheets/{row["id"]}/download').data.startswith(b'%PDF')


def test_login_rotation_revokes_previous_sid(account_app):
    client = account_app.test_client()
    assert login(client).status_code == 302
    old_cookie = client.get_cookie(account_app.config['SESSION_COOKIE_NAME']).value
    assert login(client).status_code == 302
    replay = account_app.test_client()
    replay.set_cookie(account_app.config['SESSION_COOKIE_NAME'], old_cookie)
    assert replay.get('/timesheets/period').status_code == 401
    assert client.get('/timesheets/period').status_code == 200


def test_server_session_store_outage_does_not_claim_successful_logout(account_app, monkeypatch):
    client = account_app.test_client()
    login(client)
    with client.session_transaction() as current:
        token = current['_csrf_token']
    original = account_sessions.revoke
    def unavailable(_sid):
        raise account_sessions.SessionStoreUnavailable('synthetic session store outage')
    monkeypatch.setattr(account_sessions, 'revoke', unavailable)
    assert client.post('/logout', data={'_csrf_token': token}).status_code == 503
    monkeypatch.setattr(account_sessions, 'revoke', original)
    assert client.get('/').status_code == 200
    assert client.post('/logout', data={'_csrf_token': token}).status_code == 302


def test_tampered_encrypted_session_token_requires_reauthentication(account_app):
    client = account_app.test_client()
    login(client)
    with db.connect() as connection:
        connection.execute("UPDATE account_sessions SET encrypted_token = 'corrupted-ciphertext'")
    assert client.get('/timesheets/period').status_code == 401


def test_expired_xhr_has_safe_login_link_and_keeps_screen(account_app):
    response = account_app.test_client().get('/?year=2026&month=11', headers={'X-Workspace-Navigation': '1'})
    assert response.status_code == 401
    assert response.json['login_url'].startswith('/login?next=')
    assert '2026' in response.json['login_url'] and 'month' in response.json['login_url']


def test_provider_session_with_sdk_optional_expiry_uses_explicit_lifetime(account_app, monkeypatch):
    original = account_app.synthetic_provider.sign_in_with_password
    def with_lifetime(credentials):
        result = original(credentials)
        result.session.expires_at = None
        result.session.expires_in = 600
        return result
    monkeypatch.setattr(account_app.synthetic_provider, 'sign_in_with_password', with_lifetime)
    client = account_app.test_client()
    assert login(client).status_code == 302
    assert client.get('/').status_code == 200


def test_supabase_login_removes_expired_sessions_and_keeps_active_sessions(monkeypatch):
    now = 2_000_000_000
    records = [
        {'session_hash': 'already-expired', 'expires_at': now - 1},
        {'session_hash': 'expires-now', 'expires_at': now},
        {'session_hash': 'still-active', 'expires_at': now + 1},
    ]
    class Query:
        def __init__(self):
            self.operation = None
            self.cutoff = None
            self.values = None
        def delete(self):
            self.operation = 'delete'
            return self
        def lte(self, column, value):
            assert column == 'expires_at'
            self.cutoff = value
            return self
        def insert(self, values):
            self.operation = 'insert'
            self.values = values
            return self
        def execute(self):
            if self.operation == 'delete':
                assert self.cutoff is not None
                records[:] = [row for row in records if row['expires_at'] > self.cutoff]
            else:
                records.append(self.values)
            return SimpleNamespace(data=[])
    class Client:
        def table(self, table):
            assert table == 'account_sessions'
            return Query()
    monkeypatch.setattr(account_sessions.time, 'time', lambda: now)
    monkeypatch.setattr(db, '_using_supabase', lambda: True)
    monkeypatch.setattr(db, '_client', lambda: Client())
    sid = account_sessions.create(EMPLOYEE_UUID, 'synthetic-access-token', now + 600, SIGNING_KEY)
    assert len(records) == 2
    assert records[0] == {'session_hash': 'still-active', 'expires_at': now + 1}
    assert records[1]['session_hash'] != sid
    assert records[1]['expires_at'] == now + 600
