# DevFlow production deployment

This document describes the current PostgreSQL, shared-authentication, and Render architecture. It is preparation only: do not deploy, change hosted settings, or touch production data without separate authorization.

## Runtime architecture

DevFlow runs as a Render Python Web Service behind Render's managed TLS ingress. Gunicorn serves Flask over the service's internal HTTP connection. Flask uses managed Render PostgreSQL in the same region through its internal URL. Application data is not stored on the web-service filesystem, so a Render persistent disk is neither required nor appropriate for the current runtime.

Required release configuration:

| Setting | Value |
| --- | --- |
| Runtime | Python pinned by `.python-version` |
| Build command | `pip install -r requirements.txt` |
| Pre-deploy command | `python migrate.py` |
| Start command | `python start_production.py` |
| Database | Managed Render PostgreSQL, same region, internal URL |
| `DATABASE_URL` | Private environment value; PostgreSQL URL only |
| `DEVFLOW_ACCESS_PASSWORD_HASH` | Private Werkzeug scrypt hash |
| `DEVFLOW_SESSION_SECRET` | Private random value, at least 32 characters |

Do not set the obsolete `DEVFLOW_DATABASE`. Do not put URLs, password hashes, plaintext passwords, or session secrets in Git, commands captured in reports, or logs. `migrate.py` is the only schema-change path; application import and startup never migrate or seed the database. Pre-deploy commands are unavailable on some Render plans, so plan eligibility is a release prerequisite.

`start_production.py` requires Linux, validates `PORT`, database configuration, and access configuration before execing Gunicorn. It rejects command-line arguments, clears `GUNICORN_CMD_ARGS`, `FORWARDED_ALLOW_IPS`, and `WEB_CONCURRENCY`, forces production access behavior, and uses the checked-in Gunicorn config. That config fixes one sync worker and one thread, with reload and application preload disabled. Keep one worker until process-local API and login limits are replaced with a shared store.

## Render HTTPS proxy trust and HSTS

Render terminates HTTPS at its managed load balancer and forwards the request to the web service over internal HTTP. Render's Python runtime documents `FORWARDED_ALLOW_IPS=*`, but Render does not publish a stable ingress-proxy CIDR that can be placed in Gunicorn's IP allowlist. Therefore the checked-in policy is:

- when `RENDER=true`, set Gunicorn `forwarded_allow_ips = "*"` and recognize only the exact `X-Forwarded-Proto: https` secure-scheme signal;
- everywhere else, set `forwarded_allow_ips = ""`, so direct and local clients cannot change `request.is_secure` with forwarded headers;
- do not add Flask `ProxyFix`, do not use `X-Forwarded-For` for authorization or the current shared rate limits, and do not accept launcher overrides.

This is a platform-bound trust decision, not a claim that wildcard proxy trust is safe on a generally reachable Gunicorn server. It is safe only while Render is the exclusive public ingress, strips or overwrites client-supplied forwarding headers, and the service's private network is restricted to trusted services in the same workspace/environment. If any untrusted service can connect directly over the Render private network, isolate it before release or use a proxy boundary with a stable allowlist. Do not copy the Render setting to local or self-hosted runs.

The application emits `Strict-Transport-Security: max-age=31536000` only when Flask sees the request as secure. With empty proxy trust, Render HTTPS would look like HTTP to Flask and HSTS would be missing. With the Render-only policy, genuine HTTPS requests receive HSTS while local HTTP and spoofed direct requests do not. The policy intentionally omits `includeSubDomains` and `preload`; adding either would affect other hostnames and requires a separate domain-wide review. HSTS is enforced by browsers only after they receive it over a valid HTTPS response; it does not itself redirect a user's first HTTP request or replace Render's TLS and HTTP-to-HTTPS edge behavior.

Before release, verify on the actual service that an HTTPS response contains the expected HSTS header and that Render overwrites a client-supplied `X-Forwarded-Proto: http`. Do not weaken the policy if that check fails; stop and investigate the platform path.

Relevant upstream documentation:

- [Render default environment variables](https://render.com/docs/environment-variables)
- [Render web services](https://render.com/docs/web-services)
- [Render private network](https://render.com/docs/private-network)
- [Gunicorn forwarded-header settings](https://docs.gunicorn.org/en/stable/settings.html#forwarded-allow-ips)

## PostgreSQL operations

Use the managed database's internal URL and keep the web service and database in the same region. Restrict the database's external inbound rules; normal application traffic does not require the external URL. Preserve any TLS parameters supplied by Render.

Run `python migrate.py` as the pre-deploy command. It uses a transaction-scoped advisory lock, verifies migration checksums/history, and applies pending numbered files atomically. Never edit an applied migration; add a new numbered migration. Concurrent application startup against an unmigrated schema fails rather than creating tables implicitly.

Configure and rehearse Render PostgreSQL backups and a restore into a separate non-production database before release. Record retention and recovery objectives. The legacy ignored `devflow.db` is not a runtime file, but it may contain data; preserve it and any backups until a separately reviewed SQLite-to-PostgreSQL import and reconciliation is complete. Raw SQLite copies are not PostgreSQL backups and must not be deployed with the web service.

## Authentication and public-demo limits

Production fails closed unless both access values are present. The password hash must be a valid Werkzeug scrypt hash; the session secret must contain at least 32 characters. Authenticated sessions are host-only, Secure, HttpOnly, SameSite=Lax, and limited to eight hours. Login has a pre-authentication CSRF token; authenticated mutations require the rotated session CSRF token. Login and task responses are marked `no-store`.

All authenticated visitors share one task collection. API and failed-login limits are process-local and intentionally paired with a single worker. They reset at restart and are basic safeguards, not distributed abuse prevention. Do not claim per-user privacy, authorization, or DDoS protection.

## Release verification

Use only a disposable loopback PostgreSQL database ending in `_test`. Never point tests at `DATABASE_URL` or a hosted database. The strict Linux procedure and exact tool versions are maintained in [POSTGRESQL_MIGRATION.md](POSTGRESQL_MIGRATION.md#exact-linux-python-31410-verification-commands). The required gate includes:

```sh
python -m pytest -q -p no:cacheprovider
node --test tests/frontend.test.mjs
python tests/verify_production.py --require-pinned-runtime
```

Require zero skipped PostgreSQL tests. The real-server harness checks the fixed launcher, migrations, authentication, CSRF, CRUD, PostgreSQL persistence and connection cleanup, security headers, limits, local TLS proxy behavior, and that untrusted local forwarded scheme headers remain ineffective. Its explicit loopback-trust case is test-only and must not become the normal local policy.

Final release verification completed on 2026-11-11 with Python 3.14.10 and a disposable loopback PostgreSQL 18.6 cluster: **183 Python tests passed with zero skipped**, **76 JavaScript tests passed with zero failed**, the strict production verification passed, and the Chromium browser verification passed.

After a separately authorized Render deployment, verify:

1. the deployed Python version matches `.python-version` and no conflicting `PYTHON_VERSION` override exists;
2. migrations completed once and the application starts with one sync worker;
3. unauthenticated API access is rejected and login/logout, CSRF, CRUD, static assets, and literal rendering work;
4. HTTPS responses contain exactly `Strict-Transport-Security: max-age=31536000`;
5. Render sanitizes client forwarding headers and direct private-network access is limited to trusted services;
6. the application uses the internal PostgreSQL URL and survives a web-service restart without data loss;
7. backup and restore monitoring is operational.

## Current release blockers

- Rehearse managed PostgreSQL backup and restore.
- If legacy SQLite data must be retained, implement and verify the separately scoped export/import and identity-sequence reconciliation before cutover.
- Confirm the Render plan supports the pre-deploy migration command.
- Perform the post-deploy proxy/HSTS checks on the actual Render ingress; local tests cannot prove hosted header sanitization or private-network isolation.
