-- Server-owned, revocable account sessions. No user passwords or refresh tokens.
-- Cookie ids are random; only their SHA-256 digest is persisted. Access tokens
-- are encrypted with a key derived from the application signing secret.
create table if not exists public.account_sessions (
    session_hash text primary key check (session_hash ~ '^[0-9a-f]{64}$'),
    provider_user_id uuid not null,
    encrypted_token text not null,
    expires_at bigint not null,
    created_at bigint not null,
    check (expires_at > created_at)
);
create index if not exists account_sessions_expiry_idx on public.account_sessions(expires_at);
alter table public.account_sessions enable row level security;
revoke all on table public.account_sessions from public, anon, authenticated;
grant select, insert, delete on table public.account_sessions to service_role;
