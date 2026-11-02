# Bounded local stabilization — 2026-11-02

## Scope and current state

This pass preserves the dirty codex/zoho-people worktree and the separate original
Timesheet Generator. No commit/push/merge, deployment, live DB/data/access/credential
mutation, real SSO/Kubernetes setup or outside accountant acceptance was performed.
Account login is prepared locally, not enabled on the public Vercel preview.

Read-only Git inspection: origin is https://github.com/eustace12/timesheet-app.git;
remote exposes only main=d48367f85c6be188365fd4af4e8319a10329b873. Both local main
and codex/zoho-people share that old base; HEAD is not the dirty product identity.
Future save target proposed: origin/codex/zoho-people, only after separate approval.
Do not merge main until the original-generator/two-product layout is decided.

## Confirmed defects corrected (regression before fix)

1. Recorded hours could change between document generation and atomic submit,
   saving stale hours while locking newer logs. Integer-minute snapshot validation,
   per-person locking and additive PostgreSQL RPC/trigger migration reject the race.
2. December9999 month bounds and the last absence day overflowed dates. Inclusive
   boundaries and stopping before the final increment preserve the supported input.
3. Historical accepted leave of conflicting types silently overwrote the earlier
   type. The document now stops with an explicit conflict rather than wrong hours.
4. Storage timeout/permission/dependency errors looked like missing documents or
   bad ZIP content. True missing404 is separated from retryable503; failed uploads
   do not create requests. Metadata/leave preflight outages also preserve drafts.
5. A cold holiday-source failure became an empty successful calendar. Calculations
   now require a valid feed/cache, reject obvious partial/malformed calendars,
   recover from valid disk cache and return retryable503 when no safe calendar exists.
   Fundamental fixed-date/shape checks are an integrity baseline, not a legal audit.
6. DOM Event was accidentally used as a month-draft cursor, losing immediate reason/
   format changes after reload. The correct person/month key is now used.
7. Browser Back left PDF/dialog overlays on top of the restored editor. Dialogs
   close in order and preserve editor/review drafts.
8. Opening an already approved leave crashed because no review form exists.
   Read-only details now open/close repeatedly without losing the card.
9. Editing during pending leave/document writes could discard new input or mix
   document snapshots. Pending operations lock the relevant snapshot and reject
   overlapping actions; failure restores editable fields.
10. Test imports could touch development persistence or inherit deployment env;
    calendar memory leaked between tests. Collection, renderer, cache, DB/docs and
    inherited service settings are now isolated; public transport fixtures are offline.
11. Expired account session rows were purged only in SQLite. Supabase now purges
    only expired rows before session insertion; active rows stay intact. Browser
    roles cannot read/write the server session table.

The UI also removes a confirmed unused escapeHtml helper. No global rewrite or
new HR/business features were added. Account mode was explicitly requested.

## Local account protection

Uses the existing Supabase Auth stack with a separate publishable-key client, not
legacy profile password hashes. Verified provider UUID must be explicitly mapped
to an existing People profile; browser headers/metadata do not grant roles. There
is no app signup or profile/role impersonation. Opaque signed-cookie ids reference
server-side hash/encrypted-access-token rows. Logout/expiry/revocation are checked;
no refresh token is retained. Real provider failure is retryable and does not
silently erase sessions. Existing proxy/SSO mode remains guarded and unconfigured.

Read-only live metadata: four known fictional profiles, zero Auth accounts,
private document bucket, no anonymous personnel SELECT grants. Provider email
signup is currently enabled. No live protection/settings were changed. Audience
emails have been requested; no passwords are requested. Activation is a separate
approved step in DEPLOY.md, including provider signup closure, actual accounts/
UUID links, required migrations/config and protected-hostname acceptance.

## Fresh verification

Final raw results and source/image identity are in the accompanying local artifact
checks/, BUILD-INFO.json, SOURCE-SHA256.json, IMAGE-APP-SHA256.json and SHA256SUMS.
Commands used (after final application changes):

- release Python3.12: python -m pytest -q -ra (temporary PostgreSQL16 binaries in PATH).
- npm exec --yes --package=node@24.15.0 -- npm ci --prefix tests/ui
- npm exec --yes --package=node@24.15.0 -- npm test --prefix tests/ui
- ruff0.17.3 check --select E9,F63,F7,F82; node --check for authored JS;
  Python AST and Jinja compile; git diff --check.
- pip check; pip-audit -r requirements.lock; npm audit --audit-level=high.
- Local Linux/amd64 Docker build from clean exact snapshot, per-file image/source
  checks, no-network/nonroot/read-only startup guards and account login page.

Independent cross-review: UI owner reviewed store/SQL fixes; store owner reviewed
account/API/calendar/test/build isolation, including actual PostgreSQL session
permissions and expiry cleanup before blanket test grants. Actionable findings
(partial calendars, invalid upstream labels, leaked test memory, invalid test
payload and expired-session accumulation) were addressed and rechecked. No open
P1/P2 finding remained in the bounded reviewed scope. This is not a security audit
or a claim that all bugs have been found.

## Real local browser acceptance

Actual browser/HTTP against temporary SQLite and synthetic users only:

- Employee: reason+Excel survive immediate reload; corrected month submits and
  becomes locked with its saved XLSX download.
- Administrator: actual PDF pages show saved typed unpaid leave/hours; browser
  Back closes PDF and restores editor. Approved leave opens read-only. Requesting
  changes without a comment explains the problem in-place; a valid comment saves
  the decision and updates the pending/empty state.
- Mobile375px: Office select/remove/Undo, team week and no horizontal overflow;
  leave drawer close/reopen retains dates/note, submit stays on Office.
- Account mode: unauthed page→login, mapped account sign-in, no demo selector;
  expiry during XHR→login→same month with reason/format intact; logout→login.
  Simulated provider outage shows503 with no false Office save, then retry works.
- No unexpected JavaScript exceptions observed; intentional401/503 resource
  errors were exercised. Pending-write and dropped-fetch recovery also have DOM
  regressions; real provider/production network behaviour is not established.

The account browser provider was a synthetic test boundary with temporary local
sessions. Chrome retained Secure cookies on loopback; no production cookie flags
were weakened. Screenshots are local-test evidence, not proof of live activation.

## Remaining operating boundaries

The public preview remains public until the separate audience/activation decision.
Actual managed Auth login, HTTPS hostname behaviour, cloud readiness/schema,
production workloads, backups/restore, company policy/legal calendar completeness,
remote CI and accountant/company acceptance were not run. The existing Helm chart
remains future proxy-mode preparation. No existing real accounts were available
for inclusion. Runtime versions/artifact hashes and the official Linux/amd64 base digest are
pinned; pip --require-hashes enforces runtime inputs. Dependency audits report
known advisories only. ReportLab/openpyxl deprecation warnings remain.
