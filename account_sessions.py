"""Revocable server-side sessions; browser cookies contain only an opaque id."""
import base64
import hashlib
import re
import secrets
import time
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
import db


SCHEMA = '''
CREATE TABLE IF NOT EXISTS account_sessions (
    session_hash TEXT PRIMARY KEY,
    provider_user_id TEXT NOT NULL,
    encrypted_token TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS account_sessions_expiry_idx ON account_sessions(expires_at);
'''
_SID = re.compile(r'[A-Za-z0-9_-]{43,128}\Z')


class SessionStoreUnavailable(RuntimeError):
    pass


def init_schema(connection):
    connection.executescript(SCHEMA)


def _cipher(signing_key):
    material = b'urchin-people/account-access-token/v1\0' + str(signing_key).encode('utf-8')
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(material).digest()))


def _hash(sid):
    if not isinstance(sid, str) or not _SID.fullmatch(sid):
        return None
    return hashlib.sha256(sid.encode('ascii')).hexdigest()


def create(provider_user_id, access_token, expires_at, signing_key):
    now = int(time.time())
    expires_at = min(int(expires_at), now + 8 * 60 * 60)
    if expires_at <= now or not access_token or not isinstance(access_token, str):
        raise ValueError('The account session has expired. Sign in again.')
    provider_user_id = str(UUID(str(provider_user_id)))
    sid = secrets.token_urlsafe(32)
    values = {'session_hash': _hash(sid), 'provider_user_id': provider_user_id,
              'encrypted_token': _cipher(signing_key).encrypt(access_token.encode('utf-8')).decode('ascii'),
              'expires_at': expires_at, 'created_at': now}
    try:
        if db._using_supabase():
            db._client().table('account_sessions').delete().lte('expires_at', now).execute()
            db._client().table('account_sessions').insert(values).execute()
        else:
            with db.connect() as connection:
                connection.execute('DELETE FROM account_sessions WHERE expires_at <= ?', (now,))
                connection.execute('INSERT INTO account_sessions (session_hash, provider_user_id, encrypted_token, expires_at, created_at) VALUES (?, ?, ?, ?, ?)', tuple(values.values()))
    except Exception as exc:
        raise SessionStoreUnavailable('Account sessions are temporarily unavailable.') from exc
    return sid


def read(sid, signing_key):
    digest = _hash(sid)
    if digest is None:
        return None
    try:
        if db._using_supabase():
            row = db._remote_one(db._client().table('account_sessions').select('*').eq('session_hash', digest).execute())
        else:
            with db.connect() as connection:
                row = connection.execute('SELECT * FROM account_sessions WHERE session_hash = ?', (digest,)).fetchone()
    except Exception as exc:
        raise SessionStoreUnavailable('Account sessions are temporarily unavailable.') from exc
    if row is None or row['expires_at'] <= int(time.time()):
        return None
    try:
        token = _cipher(signing_key).decrypt(row['encrypted_token'].encode('ascii')).decode('utf-8')
    except (InvalidToken, ValueError, UnicodeError):
        return None
    return {'provider_user_id': row['provider_user_id'], 'access_token': token, 'expires_at': row['expires_at']}


def revoke(sid):
    digest = _hash(sid)
    if digest is None:
        return
    try:
        if db._using_supabase():
            db._client().table('account_sessions').delete().eq('session_hash', digest).execute()
        else:
            with db.connect() as connection:
                connection.execute('DELETE FROM account_sessions WHERE session_hash = ?', (digest,))
    except Exception as exc:
        raise SessionStoreUnavailable('Account sessions are temporarily unavailable.') from exc
