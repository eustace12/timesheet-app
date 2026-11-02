"""Passwordless company access, behind an explicitly trusted SSO proxy.

Demo profile selection is deliberately separate from authenticated identity.
The proxy must overwrite both identity headers, and the app must not be publicly
reachable except through that proxy. An email header alone never authenticates.
"""
from datetime import timedelta
import json
import os
import re
import secrets

from flask import current_app, g, has_app_context, jsonify, make_response, request, session


PROXY_SECRET_HEADER = 'X-Urchin-Proxy-Secret'
IDENTITY_HEADER = 'X-Auth-Request-Email'
_EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+\Z")
_OWN_DOCUMENT = re.compile(r'/admin/(timesheets|leave)/(\d+)/(?:view|download)\Z')
_PUBLIC_PATHS = {'/health', '/health/live', '/health/ready', '/favicon.ico'}


def is_production():
    mode = current_app.config.get('APP_MODE') if has_app_context() else None
    return (mode or os.environ.get('APP_MODE', 'demo')) == 'production'


def production_current_user():
    return getattr(g, 'authenticated_user', None)


def _email(raw):
    if not isinstance(raw, str) or len(raw) > 254 or not _EMAIL.fullmatch(raw):
        return None
    return raw.lower()


def _email_mapping(raw):
    try:
        mapping = json.loads(raw or '{}')
        if not isinstance(mapping, dict):
            raise ValueError
        canonical = {}
        for email, username in mapping.items():
            normalized = _email(email)
            if not normalized or not isinstance(username, str) or not username.strip() or normalized in canonical:
                raise ValueError
            canonical[normalized] = username.strip().lower()
        return canonical
    except (ValueError, TypeError):
        raise RuntimeError('SSO_EMAIL_MAPPING_JSON must map valid full emails to existing usernames.') from None


def _access_error(status, message):
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes.best == 'application/json' or request.method != 'GET':
        return jsonify({'error': message}), status
    title = {401: 'Company sign-in required', 403: 'Access is restricted', 404: 'Page unavailable', 503: 'Service temporarily unavailable'}[status]
    # Every string interpolated here is a constant controlled by this module.
    response = make_response(f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{title} · Urchin People</title><style>body{{margin:0;background:#f4f7fb;color:#163651;font:16px/1.6 system-ui,sans-serif;display:grid;min-height:100vh;place-items:center}}main{{max-width:34rem;margin:24px;padding:32px;background:white;border-radius:18px;box-shadow:0 8px 40px #1736510c}}h1{{font-size:1.6rem;line-height:1.25}}p{{color:#52677c}}a{{color:#007bba}}</style><main><strong>URCHIN PEOPLE</strong><h1>{title}</h1><p>{message}</p><a href="/">Return to workspace</a></main></html>''', status)
    response.headers['Content-Type'] = 'text/html; charset=utf-8'
    return response


def configure_access(app, find_user_by_username, *, find_timesheet=None, find_leave=None):
    """Install access checks before CSRF; callbacks return DB rows or None.

    ``current_user`` in the application must use production_current_user() in
    production rather than trusting the demo session's user_id. File callbacks
    allow employees to preview only their own existing saved documents.
    """
    mode = os.environ.get('APP_MODE', 'demo')
    if mode not in {'demo', 'production'}:
        raise RuntimeError('APP_MODE must be demo or production.')
    app.config['APP_MODE'] = mode
    auth_mode = os.environ.get('AUTH_MODE', 'proxy')
    if auth_mode not in {'proxy', 'account'}:
        raise RuntimeError('AUTH_MODE must be proxy or account.')
    app.config['AUTH_MODE'] = auth_mode
    account_access = None
    if mode == 'production':
        secret = os.environ.get('SECRET_KEY', '')
        if len(secret) < 32 or secret in {'dev-change-me', 'change-me'}:
            raise RuntimeError('Production requires SECRET_KEY with at least 32 characters.')
        app.secret_key = secret
        app.config.update(SESSION_COOKIE_SECURE=True, SESSION_COOKIE_HTTPONLY=True,
                          SESSION_COOKIE_SAMESITE='Lax', PERMANENT_SESSION_LIFETIME=timedelta(hours=8))
        if auth_mode == 'account':
            from account_auth import AccountAccess
            account_access = AccountAccess(app, find_user_by_username)
            app.extensions['account_access'] = account_access
            proxy_secret, mapping = '', {}
        else:
            proxy_secret = os.environ.get('SSO_PROXY_SECRET', '')
            if len(proxy_secret) < 32 or not proxy_secret.isascii() or not secret.isascii() or secrets.compare_digest(secret, proxy_secret):
                raise RuntimeError('Production requires a separate ASCII SSO_PROXY_SECRET with at least 32 characters.')
            mapping = _email_mapping(os.environ.get('SSO_EMAIL_MAPPING_JSON'))
    else:
        proxy_secret, mapping = '', {}

    @app.before_request
    def company_access():
        if not is_production() or request.path in _PUBLIC_PATHS or request.path.startswith('/static/'):
            return None
        if account_access is not None:
            if request.path in {'/login', '/logout'}:
                return None
            user, error = account_access.authenticate()
            if error is not None:
                return error
        else:
            supplied = request.headers.get(PROXY_SECRET_HEADER, '')
            if not supplied.isascii() or not secrets.compare_digest(supplied, proxy_secret):
                return _access_error(401, 'Open Urchin People through your company SSO workspace to sign in.')
            email = _email(request.headers.get(IDENTITY_HEADER, ''))
            if email is None:
                return _access_error(401, 'Your company sign-in could not be verified. Sign in again through your company workspace.')
            try:
                user = find_user_by_username(mapping.get(email, email))
            except Exception:
                app.logger.error('Company identity lookup is unavailable.')
                return _access_error(503, 'We could not load your company profile. Please try again shortly.')
        if user is None or user['role'] not in {'employee', 'admin'}:
            return _access_error(403, 'Your sign-in is valid, but no active employee profile is linked. Contact your company administrator.')
        g.authenticated_user = dict(user)
        if session.get('production_user_id') != user['id']:
            session.clear()
            session['production_user_id'] = user['id']
        session.permanent = True
        if request.path in {'/profile', '/demo/role'}:
            return _access_error(404, 'Profile switching is only available in the company demo.')
        if request.path.startswith('/download/'):
            return _access_error(404, 'Open saved documents from your own submissions or the accounting inbox.')
        accountant = user['role'] == 'admin'
        document = _OWN_DOCUMENT.fullmatch(request.path)
        if request.path.startswith('/admin') and not accountant:
            if document is None or request.method not in {'GET', 'HEAD'}:
                return _access_error(403, 'Only your company accountant can access this section.')
            lookup = find_timesheet if document[1] == 'timesheets' else find_leave
            if lookup is None:
                return _access_error(403, 'You can only open documents linked to your own profile.')
            try:
                row = lookup(int(document[2]))
            except Exception:
                app.logger.error('Document ownership lookup is unavailable.')
                return _access_error(503, 'We could not open this document. Please try again shortly.')
            if row is None:
                return _access_error(404, 'This document is unavailable.')
            if row['user_id'] != user['id']:
                return _access_error(403, 'You can only open documents linked to your own profile.')
        if request.path == '/leave-balances' and request.method not in {'GET', 'HEAD'} and not accountant:
            return _access_error(403, 'Only your company accountant can change leave allowances.')
        if request.path == '/leave/assessment' and request.args.get('user_id') and not accountant:
            try:
                own = int(request.args['user_id']) == user['id']
            except ValueError:
                own = False
            if not own:
                return _access_error(403, 'You can only check leave requests for your own profile.')
        return None

    @app.context_processor
    def company_access_context():
        user = production_current_user() if is_production() else None
        accountant = bool(user and user['role'] == 'admin')
        values = {'is_production': is_production(), 'app_mode': mode,
                  'is_account_mode': is_production() and account_access is not None,
                  'is_accountant': accountant, 'can_manage_people': accountant}
        if is_production():
            values['demo_role'] = 'accountant' if accountant else 'employee'
        return values

    @app.after_request
    def private_company_response(response):
        if is_production():
            response.headers['X-Content-Type-Options'] = 'nosniff'
            response.headers['X-Frame-Options'] = 'DENY'
            response.headers['Referrer-Policy'] = 'same-origin'
            response.headers['Strict-Transport-Security'] = 'max-age=31536000'
            if not request.path.startswith('/static/'):
                response.headers['Cache-Control'] = 'no-store'
        return response
