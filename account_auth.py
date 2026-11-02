"""Existing Supabase email/password accounts, explicitly linked to People.

No signup, password provisioning or legacy profile-password fallback lives here.
Each provider operation uses an isolated client; the privileged DB client never
receives a user's Auth session. Refresh tokens are deliberately not persisted.
"""
from collections import OrderedDict
from contextlib import contextmanager
import json
import os
import secrets
import threading
import time
from urllib.parse import unquote, urlencode
from uuid import UUID

from flask import current_app, jsonify, redirect, render_template, request, session

import account_sessions


class InvalidAccount(Exception):
    pass


class AuthUnavailable(Exception):
    pass


class ProviderLimited(Exception):
    pass


def _auth_client():
    from supabase import ClientOptions, create_client
    return create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_PUBLISHABLE_KEY'],
        options=ClientOptions(auto_refresh_token=False, persist_session=False))


@contextmanager
def _provider():
    client = _auth_client()
    try:
        yield client.auth
    finally:
        close = getattr(client.auth, 'close', None)
        if callable(close):
            close()


def _provider_call(method, *args):
    try:
        with _provider() as auth:
            return getattr(auth, method)(*args)
    except Exception as exc:
        status = str(getattr(exc, 'status', ''))
        if status in {'400', '401', '403', '422'}:
            raise InvalidAccount from None
        if status == '429':
            raise ProviderLimited from None
        raise AuthUnavailable from None


def _safe_next(value):
    value = value if isinstance(value, str) else '/'
    decoded = unquote(value)
    if not decoded.startswith('/') or decoded.startswith('//') or '\\' in decoded or any(ord(char) < 32 for char in decoded):
        return '/'
    return value


def _csrf():
    if '_csrf_token' not in session:
        session['_csrf_token'] = secrets.token_urlsafe(32)
    return session['_csrf_token']


def _valid_csrf():
    expected = session.get('_csrf_token', '')
    supplied = request.form.get('_csrf_token') or request.headers.get('X-CSRF-Token', '')
    return bool(expected and supplied and supplied.isascii() and secrets.compare_digest(expected, supplied))


class AccountAccess:
    def __init__(self, app, find_user_by_username):
        if not os.environ.get('SUPABASE_URL', '').startswith('https://') or not os.environ.get('SUPABASE_PUBLISHABLE_KEY'):
            raise RuntimeError('Account login requires SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY.')
        if os.environ.get('SUPABASE_PUBLISHABLE_KEY') == os.environ.get('SUPABASE_SERVICE_ROLE_KEY'):
            raise RuntimeError('Account login requires a publishable key, separate from the server service key.')
        try:
            raw = json.loads(os.environ.get('AUTH_ACCOUNT_MAPPING_JSON', '{}'))
            if not isinstance(raw, dict) or not raw:
                raise ValueError
            self.mapping = {}
            for provider_id, username in raw.items():
                uid = str(UUID(provider_id))
                if uid in self.mapping or not isinstance(username, str) or not username.strip():
                    raise ValueError
                self.mapping[uid] = username.strip().lower()
        except (ValueError, TypeError, AttributeError):
            raise RuntimeError('AUTH_ACCOUNT_MAPPING_JSON must explicitly map account UUIDs to existing People usernames.') from None
        self.lookup = find_user_by_username
        self.signing_key = app.secret_key
        self.failures = OrderedDict()
        self.lock = threading.Lock()

    def _person(self, provider_id):
        try:
            uid = str(UUID(str(provider_id)))
        except (ValueError, TypeError, AttributeError):
            raise InvalidAccount from None
        username = self.mapping.get(uid)
        if username is None:
            return None
        person = self.lookup(username)
        return dict(person) if person is not None and person['role'] in {'employee', 'admin'} else None

    def _rate_key(self, email):
        return (request.remote_addr or 'unknown', email)

    def _limited(self, key):
        now = time.monotonic()
        with self.lock:
            recent = [value for value in self.failures.get(key, []) if now - value < 300]
            if recent:
                self.failures[key] = recent
            else:
                self.failures.pop(key, None)
            return len(recent) >= 5

    def _failed(self, key):
        with self.lock:
            self.failures.setdefault(key, []).append(time.monotonic())
            self.failures.move_to_end(key)
            if len(self.failures) > 2048:
                self.failures.popitem(last=False)

    def _form(self, error=None, status=200, email='', next_url='/'):
        return render_template('account_login.html', error=error, email=email,
                               login_csrf=_csrf(), next_url=next_url), status

    def login(self):
        next_url = _safe_next(request.values.get('next', '/'))
        if request.method == 'GET':
            return self._form(next_url=next_url)
        if not _valid_csrf():
            return self._form('Your sign-in form expired. Please try again.', 400, next_url=next_url)
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        if not email or '@' not in email or len(email) > 254 or not password or len(password) > 4096:
            return self._form('Enter your account email and password.', 400, email=email[:254], next_url=next_url)
        key = self._rate_key(email)
        if self._limited(key):
            response, status = self._form('Too many sign-in attempts. Wait five minutes and try again.', 429, email, next_url)
            return response, status, {'Retry-After': '300'}
        try:
            result = _provider_call('sign_in_with_password', {'email': email, 'password': password})
            account = result.user
            provider_session = result.session
            if account is None or provider_session is None or getattr(account, 'is_anonymous', False):
                raise InvalidAccount
            person = self._person(account.id)
            if person is None:
                self._failed(key)
                return self._form('This account is not linked to a company profile. Contact your administrator.', 403, email, next_url)
            # A new login rotates the browser id and revokes any prior app session.
            if session.get('account_sid'):
                account_sessions.revoke(session['account_sid'])
            expires_at = getattr(provider_session, 'expires_at', None)
            if expires_at is None:
                expires_at = int(time.time()) + int(provider_session.expires_in)
            sid = account_sessions.create(account.id, provider_session.access_token,
                                          expires_at, self.signing_key)
        except InvalidAccount:
            self._failed(key)
            return self._form('Email or password is incorrect. Please try again.', 401, email, next_url)
        except ProviderLimited:
            return self._form('Too many sign-in attempts. Please try again shortly.', 429, email, next_url)
        except Exception:
            current_app.logger.error('Account sign-in is temporarily unavailable.')
            return self._form('Sign-in is temporarily unavailable. Please try again shortly.', 503, email, next_url)
        with self.lock:
            self.failures.pop(key, None)
        session.clear()
        session['account_sid'] = sid
        session['production_user_id'] = person['id']
        session.permanent = True
        _csrf()
        return redirect(next_url)

    def _unauthenticated(self):
        next_url = _safe_next(request.full_path.rstrip('?') if request.method in {'GET', 'HEAD'} else '/')
        login_url = '/login?' + urlencode({'next': next_url})
        api = request.method not in {'GET', 'HEAD'} or request.path in {'/timesheets/period', '/office/data', '/leave/assessment'} or request.path.startswith('/holidays/')
        ajax = request.headers.get('X-Workspace-Navigation') or request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes.best == 'application/json'
        if api or ajax:
            return jsonify({'error': 'Sign in to continue. Your draft is kept.', 'login_url': login_url}), 401
        return redirect(login_url)

    def authenticate(self):
        try:
            stored = account_sessions.read(session.get('account_sid'), self.signing_key)
            if stored is None:
                return None, self._unauthenticated()
            result = _provider_call('get_user', stored['access_token'])
            account = result.user
            if account is None or str(account.id) != stored['provider_user_id'] or getattr(account, 'is_anonymous', False):
                raise InvalidAccount
            person = self._person(account.id)
            if person is None:
                from production_access import _access_error
                return None, _access_error(403, 'Your account is no longer linked to an active company profile. Contact your administrator.')
            return person, None
        except InvalidAccount:
            try:
                account_sessions.revoke(session.get('account_sid'))
            except account_sessions.SessionStoreUnavailable:
                pass
            session.clear()
            return None, self._unauthenticated()
        except Exception:
            current_app.logger.error('Account verification is temporarily unavailable.')
            return None, (jsonify({'error': 'We could not verify your account. Your draft is kept; please try again.'}), 503)

    def logout(self):
        if not _valid_csrf():
            return jsonify({'error': 'Refresh the page before signing out.'}), 400
        try:
            account_sessions.revoke(session.get('account_sid'))
        except account_sessions.SessionStoreUnavailable:
            return jsonify({'error': 'We could not sign you out. Please try again shortly.'}), 503
        session.clear()
        return redirect('/login')
