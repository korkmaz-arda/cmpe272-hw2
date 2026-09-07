# GitHub Issues API Wrapper

A small FastAPI service that wraps the **GitHub REST API for Issues** in one configured
repository. It exposes our own CRUD HTTP API (where *closing* an issue is the Delete
operation), supports issue comments, and securely receives, deduplicates, and stores
GitHub webhooks.

Built for CMPE 272, Assignment 2.

---

## Contents

- [What it does](#what-it-does)
- [Architecture](#architecture)
- [Requirements](#requirements)
- [Install](#install)
- [Configuration](#configuration)
- [GitHub token permissions](#github-token-permissions)
- [Run it locally](#run-it-locally)
- [Run it with Docker](#run-it-with-docker)
- [API reference and examples](#api-reference-and-examples)
- [Errors](#errors)
- [Webhook setup with a public tunnel](#webhook-setup-with-a-public-tunnel)
- [Testing](#testing)
- [Lint and formatting](#lint-and-formatting)
- [Continuous integration](#continuous-integration)
- [Project layout](#project-layout)

---

## What it does

| Capability | Detail |
|---|---|
| Issue CRUD | Create, read, list, update. **Closing an issue is Delete** — there is deliberately no `DELETE /issues/{number}`. |
| Comments | Create a comment on an issue. |
| Webhooks | `issues`, `issue_comment`, and `ping`, verified with HMAC-SHA256 over the raw body. |
| Persistence | SQLite, deduplicated on `(delivery_id, action)`, so GitHub redeliveries are safe no-ops. |
| Contract | A checked-in OpenAPI 3.1 file: [`openapi.yaml`](openapi.yaml). |
| Observability | JSON logs with a per-request correlation id, and `GET /healthz`. |

Design decisions and trade-offs are written up in [`DESIGN.md`](DESIGN.md).

---

## Architecture

```
                    ┌──────────────────────────────────────────────┐
  client ──────────▶│  FastAPI app                                 │
  (curl / HTTPie)   │                                              │
                    │  request-id middleware → JSON logs           │
                    │        │                                     │
                    │        ├── routes/issues.py ──┐              │
                    │        ├── routes/health.py   │              │
                    │        ├── routes/events.py   │              │
                    │        └── routes/webhooks.py │              │
                    │              │                │              │
                    │   webhook_security.py         │              │
                    │   (HMAC over raw bytes)       ▼              │
                    │              │        github_client.py       │──── https ───▶ api.github.com
                    │              ▼        (the only httpx caller)│
                    │           db.py                              │
                    │        (aiosqlite)   errors.py maps every    │
                    │              │       failure to one envelope │
                    └──────────────┼───────────────────────────────┘
  GitHub ──────────────────────────┘
  (webhook delivery)          webhooks.db
```

One module, one job:

| Module | Responsibility |
|---|---|
| `app/main.py` | App factory, lifespan, request-id middleware, exception handlers. |
| `app/config.py` | Environment configuration via `pydantic-settings`. |
| `app/models.py` | Request/response models and the GitHub → our-shape mappers. |
| `app/errors.py` | The single error envelope and the GitHub → HTTP mapping. |
| `app/github_client.py` | The only place that calls `api.github.com`. |
| `app/webhook_security.py` | Constant-time HMAC-SHA256 verification. |
| `app/pagination.py` | `Link`-header parsing and page-number helpers. |
| `app/db.py` | SQLite schema, idempotent insert, recent deliveries. |
| `app/routes/` | The HTTP surface: issues, webhook, events, health. |

---

## Requirements

- **Python 3.12**
- A GitHub repository you own, for testing
- A GitHub fine-grained personal access token
- Optionally: Docker, and a tunnel tool (ngrok / Cloudflare Tunnel / smee) for live webhooks

---

## Install

### With conda

```bash
conda create -n cmpe272-hw2 python=3.12 -y
conda activate cmpe272-hw2
pip install -e ".[dev]"
```

### With venv

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Runtime dependencies: `fastapi`, `uvicorn[standard]`, `httpx`, `pydantic-settings`, `aiosqlite`.
Development adds `pytest`, `pytest-asyncio`, `pytest-cov`, `pytest-httpx`, `ruff`, `PyYAML`.

There is no GitHub SDK — the service calls the REST API directly with `httpx.AsyncClient`.

---

## Configuration

```bash
cp .env.example .env
# then edit .env
```

`.env` is **git-ignored and must never be committed**. `.env.example` contains names and
placeholders only.

### Required

The service refuses to start if any of these is missing — including `PORT`, which has no
application-level default.

| Variable | Description | Example |
|---|---|---|
| `GITHUB_TOKEN` | Fine-grained PAT, scoped to the one repository below. | `github_pat_…` |
| `GITHUB_OWNER` | Repository owner (user or organization). | `your-username` |
| `GITHUB_REPO` | Repository name (just the name). | `hw2-test-repo` |
| `WEBHOOK_SECRET` | Shared secret configured on the GitHub webhook. | a random 32-byte hex string |
| `PORT` | TCP port to listen on. | `8000` |

Generate a webhook secret with:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### Optional

| Variable | Default | Description |
|---|---|---|
| `DB_PATH` | `./data/webhooks.db` | Where the SQLite webhook database lives. |
| `LOG_LEVEL` | `INFO` | Root log level. |

Secrets are held as `SecretStr`, so they are redacted from reprs, and they are never logged.

---

## GitHub token permissions

Use a **fine-grained** personal access token, not a classic one:

1. GitHub → **Settings → Developer settings → Personal access tokens → Fine-grained tokens**
2. **Generate new token**
3. **Repository access → Only select repositories** → pick the single test repository
4. **Repository permissions:**
   - **Issues: Read and write** ← the only permission this service needs
   - **Metadata: Read-only** (GitHub adds this automatically and it cannot be removed)
5. Copy the token into `GITHUB_TOKEN` in your `.env`

The token is a *server-side* credential. Callers of this API never send it, and it is never
returned in a response.

---

## Run it locally

```bash
make run
# or, without make:
uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --reload
```

Then:

```bash
curl -s http://localhost:8000/healthz
# {"status":"ok"}
```

Interactive docs generated from the running app are at `http://localhost:8000/docs`.
The authoritative contract is the checked-in [`openapi.yaml`](openapi.yaml).

---

## Run it with Docker

```bash
docker build -t cmpe272-hw2 .

docker run --rm -p 8000:8000 \
  --env-file .env \
  -e DB_PATH=/data/webhooks.db \
  -v hw2-data:/data \
  cmpe272-hw2
```

Or `make docker-build && make docker-run`.

The container binds `0.0.0.0:${PORT}` and takes all configuration from environment variables.

**SQLite persistence in Docker.** The image sets `DB_PATH=/data/webhooks.db` and declares
`/data` as a volume. Mounting a named volume (`-v hw2-data:/data`) is what makes stored
webhook deliveries survive a container restart — **without it, the database is destroyed
with the container and `/events` will look empty after a restart.** To inspect the data
from the host instead, bind-mount a directory: `-v "$PWD/data:/data"`.

---

## API reference and examples

Base URL: `http://localhost:${PORT}`

Both `curl` and [HTTPie](https://httpie.io/) forms are given. No client authentication is
required.

### `POST /issues` — create an issue

`title` is required and may not be empty or whitespace-only. `body` and `labels` are optional.

```bash
curl -i -X POST http://localhost:8000/issues \
  -H 'Content-Type: application/json' \
  -d '{"title": "Something is broken", "body": "Steps to reproduce...", "labels": ["bug"]}'
```

```bash
http POST :8000/issues title="Something is broken" body="Steps to reproduce..." labels:='["bug"]'
```

**201 Created**, with a `Location` header:

```http
HTTP/1.1 201 Created
Location: /issues/42
```
```json
{
  "number": 42,
  "html_url": "https://github.com/you/your-repo/issues/42",
  "state": "open",
  "title": "Something is broken",
  "body": "Steps to reproduce...",
  "labels": ["bug"],
  "created_at": "2026-09-01T10:00:00Z",
  "updated_at": "2026-09-01T10:00:00Z"
}
```

A missing or blank title is **400**:

```bash
curl -i -X POST http://localhost:8000/issues -H 'Content-Type: application/json' -d '{"title": "   "}'
```
```json
{"error": {"code": "invalid_request", "message": "The request payload or parameters are invalid.",
           "details": {"fields": [{"loc": "body.title", "msg": "String should have at least 1 character",
                                   "type": "string_too_short"}]}}}
```

### `GET /issues` — list issues

Query parameters: `state` (`open` | `closed` | `all`, default `open`), `labels`
(comma-separated), `page` (≥ 1), `per_page` (1–100).

```bash
curl -i 'http://localhost:8000/issues?state=all&labels=bug&page=1&per_page=50'
```

```bash
http GET :8000/issues state==all labels==bug page==1 per_page==50
```

**200 OK** with an array of issues. GitHub's `Link` header is forwarded verbatim, plus
convenience headers derived from it:

```http
Link: <https://api.github.com/repositories/1/issues?page=2>; rel="next", <...?page=5>; rel="last"
X-Page: 1
X-Per-Page: 50
X-Next-Page: 2
X-Last-Page: 5
```

`per_page` above 100 is **400**. Note that, exactly like GitHub's own Issues endpoint, a page
may include pull requests; entries are not filtered, so page sizes stay consistent with the
`Link` header being forwarded.

### `GET /issues/{number}` — get one issue

```bash
curl -s http://localhost:8000/issues/42
http GET :8000/issues/42
```

**200** when found, **404** when not, **400** when the number is not a positive integer.

### `PATCH /issues/{number}` — rename, edit, close, reopen

```bash
# rename
curl -s -X PATCH http://localhost:8000/issues/42 \
  -H 'Content-Type: application/json' -d '{"title": "A clearer title"}'

# edit the body
curl -s -X PATCH http://localhost:8000/issues/42 \
  -H 'Content-Type: application/json' -d '{"body": "Updated reproduction steps."}'

# close  — this is the Delete operation
curl -s -X PATCH http://localhost:8000/issues/42 \
  -H 'Content-Type: application/json' -d '{"state": "closed"}'

# reopen
curl -s -X PATCH http://localhost:8000/issues/42 \
  -H 'Content-Type: application/json' -d '{"state": "open"}'
```

```bash
http PATCH :8000/issues/42 state=closed
```

**200** on success. Only the fields you send are forwarded, so an omitted field is left
untouched. An invalid `state`, an unknown field, or an empty object is **400**; a missing
issue is **404**.

### `POST /issues/{number}/comments` — comment on an issue

```bash
curl -i -X POST http://localhost:8000/issues/42/comments \
  -H 'Content-Type: application/json' -d '{"body": "Thanks for the report."}'
```

```bash
http POST :8000/issues/42/comments body="Thanks for the report."
```

**201 Created**:

```json
{
  "id": 900001,
  "body": "Thanks for the report.",
  "user": "your-username",
  "created_at": "2026-09-01T11:00:00Z",
  "html_url": "https://github.com/you/your-repo/issues/42#issuecomment-900001"
}
```

An empty or whitespace-only body is **400**.

> There is intentionally no public `GET /issues/{number}/comments`. The assignment's API
> defines only `POST` for comments; the integration test verifies a created comment by
> querying GitHub directly. See [`DESIGN.md`](DESIGN.md).

### `POST /webhook` — receive a GitHub delivery

Called by GitHub, not by you. Requires `X-Hub-Signature-256`, `X-GitHub-Event`, and
`X-GitHub-Delivery`. Returns **204 No Content** on success (including for a redelivery),
**401** for a bad signature, **400** for an unsupported event/action or a missing delivery id.

You can simulate a delivery locally:

```bash
BODY='{"action":"opened","issue":{"number":42,"title":"Something is broken"}}'
SIG="sha256=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$WEBHOOK_SECRET" | awk '{print $2}')"

curl -i -X POST http://localhost:8000/webhook \
  -H 'Content-Type: application/json' \
  -H 'X-GitHub-Event: issues' \
  -H 'X-GitHub-Delivery: 11111111-2222-3333-4444-555555555555' \
  -H "X-Hub-Signature-256: $SIG" \
  -d "$BODY"
```

Send it twice: both return `204`, and `/events` still shows a single row.

### `GET /events` — recently processed deliveries

```bash
curl -s 'http://localhost:8000/events?limit=10'
http GET :8000/events limit==10
```

```json
[
  {"id": "11111111-2222-3333-4444-555555555555", "event": "issues", "action": "opened",
   "issue_number": 42, "timestamp": "2026-09-06T12:00:00.000+00:00"}
]
```

`limit` defaults to 20 and must be between 1 and 200.

### `GET /healthz`

```bash
curl -s http://localhost:8000/healthz
# {"status":"ok"}
```

---

## Errors

Every failure uses one envelope:

```json
{"error": {"code": "machine_readable_code", "message": "Human readable explanation"}}
```

with optional `details`:

```json
{"error": {"code": "rate_limited", "message": "GitHub API rate limit exceeded.",
           "details": {"retry_after": 60}}}
```

| Situation | Status | `code` |
|---|---|---|
| Invalid request payload or parameters | 400 | `invalid_request` |
| GitHub rejected the request (validation) | 400 | `github_validation_failed` |
| Bad webhook signature | 401 | `invalid_signature` |
| GitHub authentication failed | 401 | `github_unauthorized` |
| GitHub denied access | 403 | `github_forbidden` |
| Issue or route not found | 404 | `not_found` |
| GitHub rate limit hit | 429 | `rate_limited` (+ `Retry-After`) |
| Bug in this service | 500 | `internal_error` |
| GitHub unavailable / timed out | 503 | `github_unavailable` |
| Webhook could not be stored | 503 | `storage_unavailable` |

Request-validation failures are reported as **400**, not FastAPI's default 422. Raw GitHub
response bodies are never forwarded.

---

## Webhook setup with a public tunnel

GitHub must be able to reach your machine, so expose the local port through a tunnel.

**1. Start the service**

```bash
make run     # listening on $PORT
```

**2. Start a tunnel** (pick one)

```bash
ngrok http 8000
# or
cloudflared tunnel --url http://localhost:8000
```

**3. Copy the public URL**, e.g. `https://a1b2c3d4.ngrok-free.app`.

**4. Configure the webhook in GitHub**

In your test repository: **Settings → Webhooks → Add webhook**

| Field | Value |
|---|---|
| Payload URL | `https://<public-host>/webhook` |
| Content type | `application/json` |
| Secret | the **same** value as `WEBHOOK_SECRET` in your `.env` |
| SSL verification | Enabled |
| Events | **Let me select individual events** → check **Issues** and **Issue comments** |
| Active | ✔ |

GitHub immediately sends a `ping`; it should show a **204** response.

**5. Trigger a real event** — open an issue in the repository (or comment on one). You can
also do it through this service:

```bash
http POST :8000/issues title="Webhook smoke test"
```

**6. Check that it was received and stored**

```bash
curl -s http://localhost:8000/events | python -m json.tool
```

You should see the `ping` row and an `issues` / `opened` row.

**7. Verify idempotency with a redelivery**

In GitHub: **Settings → Webhooks → (your webhook) → Recent Deliveries**, pick a delivery and
click **Redeliver**. It returns **204** again, and `/events` still shows the same number of
rows — the `(delivery_id, action)` key deduplicates it.

**Troubleshooting**

- **401 in Recent Deliveries** → the secret in GitHub does not match `WEBHOOK_SECRET`.
- **400** → the event is not one of `issues` / `issue_comment` / `ping`; uncheck the others.
- **Nothing arrives** → the tunnel URL changed (free ngrok URLs change on restart); update
  the Payload URL.

---

## Testing

### Unit tests (mocked GitHub, no network)

```bash
make test
# or
pytest
```

Integration tests are excluded from this run by default, so a plain `pytest` can never create
issues in a real repository.

### Coverage

Coverage runs automatically and fails below **80%**:

```bash
pytest                    # prints a terminal report
make cov                  # writes htmlcov/index.html
```

### Integration tests (real GitHub)

These use the real repository from your `.env`. They create one issue, exercise the full
flow, verify the comment by querying GitHub directly, and close the issue afterwards. They
**skip automatically** when credentials are absent.

```bash
make test-integration
# or
pytest -m integration --no-cov -v
```

The flow is: create → get → rename → edit body → close → reopen → comment → verify the
comment via GitHub's own API.

### Run everything

```bash
pytest -m "" --no-cov     # unit + integration (needs credentials)
```

---

## Lint and formatting

```bash
make lint     # ruff check + ruff format --check
make fmt      # auto-format and auto-fix
```

---

## Continuous integration

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every push and pull request:

1. **test** — install dependencies, `ruff check`, `ruff format --check`, unit tests with the
   coverage gate, and upload the coverage report.
2. **docker** — build the image and smoke-test `/healthz` in a running container.

CI uses placeholder credentials only. Live integration tests are **not** part of the default
pipeline; the workflow documents how to enable them with repository secrets.

---

## Project layout

```
cmpe272-hw2/
├── app/
│   ├── main.py             # app factory, middleware, exception handlers
│   ├── config.py           # environment configuration
│   ├── models.py           # request/response models + GitHub mappers
│   ├── errors.py           # error envelope + GitHub -> HTTP mapping
│   ├── github_client.py    # the only caller of api.github.com
│   ├── webhook_security.py # HMAC-SHA256 verification
│   ├── pagination.py       # Link-header parsing
│   ├── logging_config.py   # JSON logs + request-id contextvar
│   ├── db.py               # aiosqlite persistence
│   ├── deps.py             # two FastAPI dependencies
│   └── routes/             # issues, webhooks, events, health
├── tests/
│   ├── unit/               # mocked; the default test run
│   ├── integration/        # real GitHub; marked `integration`
│   └── fixtures/           # sample GitHub payloads
├── openapi.yaml            # OpenAPI 3.1 contract (source of truth)
├── DESIGN.md               # design notes and trade-offs
├── Dockerfile
├── Makefile
├── pyproject.toml
├── .env.example
└── .github/workflows/ci.yml
```
