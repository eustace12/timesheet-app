# Release and deployment

## Existing public demo

- URL: https://urchin-people-demo.vercel.app/
- Vercel project: `urchin-people-demo`, scope `stas-projects-fb7c2246`.
- Isolated Supabase reference: `rdoamaeixdzkddvnlvto`.
- `APP_MODE=demo`; all examples are fictional and shared.
- The original `timesheet-app` project and `timesheet-md.vercel.app` are separate and are not deployment targets for this release.

## Database order

For a **new company database**, apply:

1. `supabase/schema.sql`
2. `supabase/hr_workspace.sql`
3. `supabase/migrations/20261102160704_workflow_integrity.sql`
4. `supabase/migrations/20261102164908_office_plans.sql`
5. `supabase/migrations/20261102173305_recorded_hours_snapshot.sql`
6. `supabase/migrations/20261102173413_account_sessions.sql`

The workflow migration adds the typed monthly absence snapshot, atomic workflow RPCs and the immutable activity journal. It is additive and preserves existing records. Apply it before new application code: there is deliberately no fallback to unsafe non-atomic writes. RPC execution and personnel tables are server-side only; public/anonymous roles receive no access.

Existing records do not acquire a fabricated audit history. New operations append events. The current saved document remains the available download; this release does not reconstruct historical PDF revisions.

For the existing isolated demo only, `scripts/apply_demo_release.py` verifies the exact project reference and migration SHA before applying the reviewed file. It accepts `--check` for read-only inspection. Use runtime injection through `vercel env run`; do not pull environment secrets or write them into local files. This script deliberately refuses a company database.

The office migration adds optional office plans and a server-only atomic write RPC. Apply it before publishing Office routes; readiness also checks this table. It does not import the reference spreadsheet or modify accounting records. For the existing isolated demo, use `scripts/apply_demo_release.py --office` through the same runtime injection; `--check` remains read-only.

## Closed account mode: local preparation and live activation boundary

The current published preview remains `APP_MODE=demo`; this stabilization does
not change live settings/data/access or deploy anything. Read-only inspection on
2026-11-02 found no existing Supabase Auth accounts, only four known fictional
People profiles. A profile is not a password account. Provider email auth exists;
public provider signup is currently enabled. The app itself exposes no signup
route and requires an explicit verified UUID allowlist regardless of that setting.

Prepared runtime configuration for a later approved activation:

- `APP_MODE=production`, `AUTH_MODE=account`.
- Existing `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` for server persistence.
- `SUPABASE_PUBLISHABLE_KEY` for a separate Auth client; never reuse service_role.
- Existing strong `SECRET_KEY`, also used to derive the session-token encryption
  key. Browser cookies contain an opaque session id, not provider tokens.
- `AUTH_ACCOUNT_MAPPING_JSON`: nonempty mapping of verified Supabase account UUID
  to the exact existing People username. UUID mapping, not email matching or
  user-editable Auth metadata, grants access; roles come freshly from People.
- `SSO_PROXY_SECRET` is not required in account mode. Proxy/SSO mode is retained.

Sessions expire no later than the provider access token or eight hours. No refresh
token is retained: reauthentication is deliberate. CSRF-protected logout revokes
server state; a copied old cookie cannot re-enter. Expired session rows are purged
on successful account session creation. Drafts remain browser/person/period scoped.

Activation sequence for `https://urchin-people-demo.vercel.app/` (NOT executed):

1. Confirm the intended audience emails (question pending). There are no existing
   Auth UUIDs to allowlist yet. Separately authorize provisioning/invitations and
   linking to the intended employee/admin profiles; do not reuse fictional people
   as authentication accounts or ask for passwords in chat.
2. With separate explicit authorization, disable public signup at the existing
   Supabase provider and establish the approved accounts/profile links. No new
   provider, public registration, paid service or credential rotation is implied.
3. Review/apply required schema migrations to the exact chosen project, including
   the recorded-hours RPC upgrade and server-only account_sessions table. Keep
   documents private and anonymous personnel table access revoked.
4. With separate authorization, configure account mode/nonempty UUID mapping and
   publish the reviewed image/source. Startup fails closed on absent mapping/key.
   Keep the existing signing secret stable to avoid unplanned session rotation.
5. Rehearse unauthenticated page/API/PDF/Excel/legacy/storage denials, approved
   account sign-in, own-document access, employee/admin separation, CSRF, logout,
   cookie replay and expiry/re-login draft recovery on the actual HTTPS hostname.
6. Rollback must keep access closed. Do not restore `APP_MODE=demo` as an automatic
   rollback or expose an older public build; use an approved protected maintenance
   state while diagnosing. Preserve business records and additive schemas.

Existing Kubernetes chart/templates below remain **proxy-mode preparation**, not
an account-mode deployment manifest. SSO, Kubernetes rollout, real provider login,
real-data acceptance and backup/restore are unverified and outside this local pass.

## Company proxy configuration (future rollout)


Use the existing company's secret delivery mechanism, not files in this repository. Required runtime variables:

- `APP_MODE=production`
- `DEMO_MODE=0`
- `SECRET_KEY`: random session signing secret, at least 32 characters.
- `SSO_PROXY_SECRET`: a different random secret, at least 32 characters, shared only between the trusted proxy and the application.
- `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`: company persistence, server-only.
- `SUPABASE_STORAGE_BUCKET`: optional; defaults to private `timesheet-documents`.
- `SSO_EMAIL_MAPPING_JSON`: optional mapping of complete verified company emails to existing usernames. Alternatively use full emails as usernames. Local-part matching and role headers are not trusted.
- `OUTPUT_DIR=/tmp/generated`: scratch space, not persistent business storage.

Provision the initial company administrator and employee records, with actual allowances, through the approved database provisioning process before first use. Do not import fictional demo people or legacy demo permissions.

The proxy must authenticate the company IdP, overwrite `X-Auth-Request-Email` and `X-Urchin-Proxy-Secret`, and be the only source of application traffic. A client-supplied email alone is rejected. See [deploy/README.md](deploy/README.md), including the documented oauth2-proxy configuration fragment and NetworkPolicy.

## Company release artifact

```sh
docker build --platform linux/amd64 -t YOUR_REGISTRY/urchin-people:REVIEWED_RELEASE .
helm lint deploy/chart -f /path/to/company-values.yaml
helm template urchin-people deploy/chart --namespace YOUR_NAMESPACE \
  -f /path/to/company-values.yaml
```

The chart requires an immutable image tag, existing Secret and exact trusted proxy selectors. It renders no public ingress. Resolve the actual company cluster/context, namespace, hostname, registry, SSO deployment and persistence project before applying via the company's GitOps process.

The container uses locked Python dependencies, Gunicorn and uid10001. Kubernetes uses a read-only root filesystem and `/tmp` scratch volume. `/health/live` checks the process; `/health/ready` checks the current schema, database and private document bucket.

## Launch and rollback

Run Python, JavaScript, PostgreSQL integration and dependency checks. Verify the built image starts with valid configuration and refuses missing SSO/persistence configuration. Rehearse employee ownership restrictions, accountant decisions, correction/resubmission and downloads through the actual company SSO hostname.

Before real records, verify company backups and an isolated restore of both database records and document objects. A schema migration test is not a backup/restore test. Define the owner, recovery target and retention for these records.

Keep the prior image tag and chart revision. Roll back application/Helm revisions on sign-in, readiness or workflow regressions; retain the additive schema and business records. Do not delete the journal or database to roll back code.

For the public Vercel demo, inspect the linked project before deploy. Its previous production deployment can be re-aliased if needed; the additive migration is compatible with the older application. Never promote a public demo profile into the company environment.
