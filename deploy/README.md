# Future company proxy deployment

The image defaults to `APP_MODE=production`, `AUTH_MODE=proxy`. This chart is
preparation for that existing trusted SSO proxy mode; it cannot use the demo
identity selector. The application also has a separately prepared account mode
(`AUTH_MODE=account`) documented in ../DEPLOY.md. This chart does not configure
that mode, and neither real SSO nor Kubernetes rollout is part of stabilization. The public Vercel demo is a separate deployment with
`APP_MODE=demo` and fictional records only.

## Required target values

Choose the image registry and immutable release tag, company hostname/TLS,
namespace, exact trusted SSO proxy namespace/pod labels, and the intended
Supabase project. The chart deliberately refuses rendering without the image,
existing Secret name and trusted proxy selectors. It creates no external ingress
or credentials and does not alter the company's SSO deployment.

Use the company's existing secret delivery mechanism to provide a Secret with
`SECRET_KEY`, `SSO_PROXY_SECRET`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`.
The two signing/proxy secrets must be different, randomly generated values of
at least 32 characters. No credential values belong in values files or images.

`identityMapping` maps complete verified SSO emails to existing People usernames.
An existing username that already equals the complete email needs no mapping.
Unmapped email local parts are never used for identity matching. Roles come only
from the People database: `admin` can manage accounting and employees;
`employee` can access their own records and redacted team availability.

## Proxy trust boundary

Use `oauth2-proxy-headers.yaml` as a fragment in the **existing** proxy alpha
configuration; it targets the documented v7.14+ header source structure. Mount
the existing Secret's `SSO_PROXY_SECRET` key into that proxy as the indicated
file. Both headers must be overwritten after sign-in. Never forward the proxy
secret to browsers, auth-request response headers, access logs or redirect URLs.

Route the company HTTPS ingress to oauth2-proxy and its authenticated upstream
to the chart's ClusterIP Service. Do not route ingress directly to the app. The
NetworkPolicy accepts requests only from the selected proxy pods, and the app
also checks the secret on every business request. Health probes carry no data
and remain public. A client-supplied email without the secret returns HTTP401.

The proxy must validate the company IdP and its email/tenant claims. Confirm its
provider configuration before rollout. Header injection is documented in the
[official OAuth2 Proxy alpha configuration](https://oauth2-proxy.github.io/oauth2-proxy/7.14.x/configuration/alpha-config/).

## Build and verify without applying

```sh
docker build --platform linux/amd64 -t YOUR_REGISTRY/urchin-people:REVIEWED_RELEASE .
helm lint deploy/chart -f /path/to/company-values.yaml
helm template urchin-people deploy/chart --namespace YOUR_NAMESPACE \
  -f /path/to/company-values.yaml > /tmp/urchin-people-rendered.yaml
```

The image runs Gunicorn as uid10001, uses a read-only filesystem in Kubernetes
and writes only generation scratch files under `/tmp`. Business records and
documents are durable in the configured Supabase project. Two replicas do not
share scratch files. `/health/live` is a process check; `/health/ready` checks
required persistence before receiving traffic.

Before real employee data: apply the reviewed database schema migrations to the
selected project, provision real employee profiles/allowances, verify SSO as an
employee and accountant, and perform a backup/restore drill including document
objects. Existing demo rows and allowances are not imported into company data.

## Rollout and rollback

Apply only through the company's repository-owned release/GitOps process after
reviewing the exact target and rendered chart. Keep the previous image tag and
chart revision. Roll back the release if sign-in, readiness, document access or
the employee/accountant rehearsal fails. Keep the database and document bucket:
rolling back application code does not mean deleting business data. Restore from
the verified backup only if data recovery is actually required.
