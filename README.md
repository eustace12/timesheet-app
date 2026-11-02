# Urchin People

Monthly timesheets, time off, accounting approvals and an immutable activity journal for Urchin Systems. This project is separate from the original database-free generator in the sibling `timesheet-app` checkout.

## Access profiles

- **Closed account mode (prepared locally):** `APP_MODE=production`, `AUTH_MODE=account`. Existing Supabase Auth email/password accounts must be explicitly linked by verified account UUID to a People profile. No app registration, demo impersonation or role switching. Server-side encrypted, revocable sessions; expired access requires signing in again. No existing accounts or passwords are created by this code.
- **Corporate proxy mode (retained, rollout deferred):** `APP_MODE=production`, `AUTH_MODE=proxy` (the existing default). Verified proxy identity and database permissions; no demo impersonation. Real SSO/Kubernetes configuration is a separate future step.
- **Public demo:** <https://urchin-people-demo.vercel.app/>. `APP_MODE=demo`, fictional data, shared access, Employee/Accountant perspectives. This address is not the company deployment and must not contain real employee records.

## Core workflows

- Prepare a month without daily time logging, add absences, preview the actual PDF, export PDF/Excel and submit to accounting.
- Approved annual, unpaid and other leave are enforced on the server and retain their correct types. A submitted month's absence snapshot remains fixed.
- Accounting approves or requests corrections. Submitted and approved documents are locked; approved timesheets require an explicit reopen with a reason.
- Previous months awaiting corrections remain visible on Home. Returned leave can be prepared again directly from Time off.
- Team balances identify missing allowances and reservations. Accountant edits name the affected employee explicitly.
- Concurrent submissions, leave overlaps, balances and stale decisions are checked transactionally in SQLite and PostgreSQL. Decisions and authors are appended to a read-only journal.
- Navigation and contextual drawers preserve drafts by person and month; layouts adapt to mobile screens.

## Optional office plans

At `/office`, employees choose office days with one tap and optionally add arrival/departure times. The shared week shows who plans to come in. Plans require no approval and have no effect on timesheets or leave balances. Time drafts stay separate by employee and date; stale updates are detected. The original office spreadsheet is a reference only; its employee records are not imported.

## Run locally

```sh
python3 -m venv .venv
.venv/bin/pip install --require-hashes -r requirements.lock
.venv/bin/pip install -r requirements-dev.txt
APP_MODE=demo DEMO_MODE=1 .venv/bin/python web_app.py
```

Local development uses SQLite and generated files. Company deployments require persistent Supabase records and a private document bucket; startup refuses insecure configuration. Do not reuse the public demo database for real company data.

## Verify

```sh
python3 -m pytest -q
npm exec --yes --package=node@24.15.0 -- npm ci --prefix tests/ui
npm exec --yes --package=node@24.15.0 -- npm test --prefix tests/ui
pip-audit -r requirements.lock
npm audit --prefix tests/ui --audit-level=high
```

Tests use recorded public holiday responses and disposable persistence, including during application import; inherited deployment database settings are not used. A missing/invalid runtime holiday source with no valid cache stops accounting calculations with a retryable error.

The PostgreSQL integration tests use temporary local databases when `initdb`, `pg_ctl` and `psql` are available. The Linux/amd64 base is digest-pinned and runtime requirements carry artifact hashes, enforced by pip --require-hashes. The company container build and release checks are in `.github/workflows/ci.yml`.

See [DEPLOY.md](DEPLOY.md) and [deploy/README.md](deploy/README.md) for the migration order, company SSO contract, container, chart and rollback procedure. See the account activation plan in DEPLOY.md. This local stabilization has not enabled protection or changed the published demo; live activation, account audience/provisioning and remote Git writes require separate authorization.

## Current scope

The product covers a single company's monthly time and leave workflow. It does not include payroll calculation, automatic email notifications, leave cancellation, accrual/carry-over policy automation, or the rest of Zoho People. Define these policies before extending the workflow.

MIT license.
