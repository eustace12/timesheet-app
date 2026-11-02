# Current bounded stabilization — 2026-11-02

Preserve all existing dirty worktree changes and the original generator. Local
implementation/checks only. No commit/push/merge, publishing/deploy, live DB/access/
credential mutation, real SSO/Kubernetes setup or external accountant acceptance.

1. **Account login first — API/auth owner: production_developer.** Existing Supabase
   Auth email/password, explicit verified UUID → People profile allowlist, no app
   registration or demo role/profile bypass. Server-side encrypted access tokens,
   opaque revocable sessions, CSRF login/logout and expiry. Existing proxy guards
   preserved. Done when unauthenticated pages/API/documents and cookie replay are
   rejected by fresh regression tests; live activation plan/audience question recorded.
2. **Git boundary — root.** Read-only remotes, worktrees and remote heads; identify
   all local-only changes. Done when snapshot identity and proposed future remote
   topic branch are clear, without external writes.
3. **Business/data — workflow_tester.** Own db.py/hr_store.py/office_store.py/
   timesheet_absences.py, SQL and store tests. Reproduce and fix confirmed stale
   hours-at-submit race, upper date overflow and conflicting historical leave types;
   check balance/review concurrency and Office independence on both SQLite and
   temporary PostgreSQL. Do not edit API/UI files or existing deployed migrations.
4. **Routes/storage — production_developer.** Own web_app/hr_web/office_web,
   production_access, account_auth/account_sessions, document_storage/runtime_health,
   account_login template and route/auth tests. Reproduce retryable dependency errors,
   enforce rights/validation without weakening guards. New SQL session migration only.
5. **UI recovery — product_critic.** Own static JS/CSS, templates except account_login,
   DOM tests. Fix confirmed immediate draft-key loss, readonly approved-leave drawer
   crash and Back leaving PDF/dialog overlays. Check pending writes and safe sign-in
   recovery. Root alone owns tests/conftest and UI render fixtures to avoid collisions.
6. **Calendar/test/build isolation — root.** Cold upstream/calendar-cache failure
   must not silently calculate holidays as working days. Offline recorded public
   holiday fixtures; tests must not touch development DB/docs or inherited deploy
   services. Pin supported test runtime and exclude local tooling/state from images.
7. **Acceptance and independent final review — root + cross-review workers.** Real
   local browser employee/admin cycles on disposable data, desktop/mobile; drafts,
   Back, Undo, repeat clicks and retry/error states. Review others' final changes;
   fix findings, then full Python/UI/PostgreSQL, syntax/dependency checks and local
   Linux/amd64 build. Done when bounded scenarios pass, review findings resolved,
   final source/image manifests and fresh evidence identify the exact dirty snapshot.

The current live preview remains publicly readable (HTTP200 for workspace/admin/
Office/month API). Read-only metadata: four known fictional profiles, no existing
Supabase Auth accounts; private document bucket and no anonymous personnel SELECT.
Provider email signup currently enabled; application UUID allowlist denies unlinked
accounts. Before live enablement: confirm audience (question already pending),
separately authorize provisioning/linking and disabling public provider signup,
apply reviewed migrations/configuration and verify protected published surfaces.
No live action is authorized in this stabilization pass.

Remote currently has only main=d48367f85c6be188365fd4af4e8319a10329b873. Proposed
future save: origin/codex/zoho-people, subject to explicit approval. HEAD alone does
not identify the dirty product; don't merge main until dual-product layout is settled.

---

## Historical completed plan (superseded; not current authorization)

# Company release hardening

Prepare a company deployment build while preserving the public fictional-data demo and the separate database-free generator.

1. Independent product critique, workflow tests and production review. Record reproducible findings, not speculative scores.
2. Fix workflow correctness: approved leave enforced server-side with correct types and a saved snapshot; balances, duplicate submissions and stale decisions protected atomically.
3. Fix recovery UX: Other dates, overdue months, revised leave requests, and a team balance view. Preserve per-person drafts and mobile layouts.
4. Add a separate production profile behind the company ingress/oauth2-proxy. Require verified proxy identity, database roles, durable persistence and secure configuration; disable demo impersonation.
5. Package a non-root container and repository-owned deployment templates, health/readiness checks and a release test gate. Do not apply to a company cluster without resolving its exact target and existing ingress contract.
6. Run regression, authorization and browser checks; review all new changes. Publish demo-compatible fixes only after its new database migration is verified. Report actual deployment state separately from release artifact readiness.

Acceptance: employee cannot access another employee's documents or admin mutations; accounting decisions cannot silently overwrite a changed submission; approved absences and generated hours match; existing demo remains usable; release instructions identify migration, secrets, SSO boundary, backup and rollback prerequisites.

