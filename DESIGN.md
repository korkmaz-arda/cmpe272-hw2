# Design Notes

A short account of the decisions behind this service and what they cost.
See [`README.md`](README.md) for usage and [`openapi.yaml`](openapi.yaml) for the contract.

## Shape of the thing

Four route modules over three collaborators: `github_client.py` (the only caller of
`api.github.com`), `db.py` (the only caller of SQLite), and `errors.py` (the only place that
decides a status code). Everything the routes need is built once in the lifespan and parked on
`app.state`; `deps.py` is two functions long. There is no repository layer, no service layer,
and no DI container, because at this size those would add indirection without removing any.

## Error mapping

Every failure — validation, upstream, or internal — leaves through one envelope:

```json
{"error": {"code": "...", "message": "...", "details": {...}}}
```

`map_github_response()` translates upstream failures once:

| GitHub | Us | Why |
|---|---|---|
| 401 | 401 `github_unauthorized` | Our token is bad; the caller should know it is a server-side problem. |
| 403 (rate-limited) | 429 `rate_limited` | See below — GitHub does not always use 429. |
| 403 (otherwise) | 403 `github_forbidden` | A real permission/scope problem. |
| 404 | 404 `not_found` | |
| 422 | 400 `github_validation_failed` | GitHub rejected the *caller's* content, so it is a client error to us. |
| other 4xx | 400 `github_bad_request` | |
| 5xx, timeout, connect error | 503 `github_unavailable` | A dependency is down, not us. |

Raw GitHub bodies are never forwarded. For a 422 we whitelist `resource`/`field`/`code` from
`errors[]`; everything else (including `documentation_url`) is dropped. Our own bugs become a
generic 500 while the traceback goes to the logs.

**Validation is 400, not 422.** FastAPI defaults to 422 for request-validation failures, but the
assignment specifies 400 for a missing title or an invalid state. Rather than sprinkle try/except
through the routes, one `RequestValidationError` handler converts every such failure, and the
constraints live in Pydantic (`StringConstraints(strip_whitespace=True, min_length=1)` catches
whitespace-only titles and comment bodies; `Literal` catches bad states; `Query(le=100)` catches
oversized pages; `extra="forbid"` catches typos). The cost is a deliberate divergence from FastAPI
convention; the benefit is that *every* invalid request looks the same to a caller.

## Pagination

`state`, `labels`, `page`, and `per_page` are forwarded to GitHub unchanged, `per_page` is capped
at GitHub's own maximum of 100, and GitHub's `Link` header is copied onto our response **verbatim**.
`pagination.py` parses that header only to *add* conveniences (`X-Next-Page`, `X-Last-Page`, …).

Two consequences worth naming. First, the `Link` URLs point at `api.github.com`, not at us —
rewriting them would mean owning a pagination scheme, which is a stated non-goal. Second, we do
**not** filter pull requests out of the list, even though GitHub's Issues endpoint returns them:
dropping entries while forwarding GitHub's own `Link` header would produce pages shorter than
`per_page` and a page count that no longer matches the data. Preserving upstream semantics beats
tidying the payload.

The parser anchors on `<...>` rather than splitting on commas, because a URL like
`?labels=bug,wontfix` legitimately contains one. Malformed input yields `{}` instead of raising —
a broken upstream header must never fail a request.

## Rate limits

GitHub signals rate limiting in at least three ways, so detecting only 429 would misclassify most
of them. A response counts as rate-limited when its status is 403 **or** 429 **and** any of:
`x-ratelimit-remaining: 0`, a `retry-after` header, or a body message mentioning a rate limit.
`Retry-After` is taken from the header, else computed from the `x-ratelimit-reset` epoch, else
defaulted to 60 seconds, and returned both as a header and in `details.retry_after`.

The service **never retries automatically**. A wrapper that retries into a rate limit makes the
limit worse, and a caller who can see 429 and `Retry-After` is better placed to decide. All of
this is exercised against mocked responses; no test tries to exhaust a real token.

## Webhook security

The HMAC is computed over `await request.body()` — the exact bytes GitHub sent — before any JSON
parsing, because a parse-and-re-serialize round trip would change the bytes and break the digest.
Comparison uses `hmac.compare_digest`. Verification happens *first*, so an unsigned request for an
unsupported event is a 401, not a 400: we do not reveal what we support to unauthenticated callers.

The secret, the token, the received signature, and the raw body are never logged. Log lines carry
only `request_id`, `delivery_id`, `event`, `action`, `issue_number`, and an outcome.

## Webhook deduplication

`(delivery_id, action)` is the primary key of `webhook_deliveries`, and inserts use
`ON CONFLICT DO NOTHING`. A redelivery therefore writes nothing and still returns 204, which is
exactly what GitHub's "Redeliver" button needs.

Two details make this work:

- **`action` is `NOT NULL`.** SQLite treats NULLs as distinct in a unique index, so a nullable
  action would silently defeat deduplication for `ping` deliveries, which carry no action. Ping is
  stored with the action `"ping"`.
- **`X-GitHub-Delivery` is required.** A delivery we cannot key is a delivery we cannot dedupe, so
  a missing or blank header is a 400 rather than an unkeyed row.

## Retry and poison handling

Interpreted pragmatically — no queue, no worker, no dead-letter topic:

1. **Deterministic rejection.** A signed but malformed or unsupported payload always fails the same
   way (400), so redelivering it fails identically instead of behaving unpredictably.
2. **No crashes.** JSON errors and unexpected shapes are caught; `issue_number` extraction is
   defensive.
3. **No loops.** The service never re-enqueues or self-retries; retry is GitHub's redelivery, and
   the dedupe key keeps it harmless.
4. **Persistence failures are never acknowledged.** If the SQLite write raises, returning 204 would
   tell GitHub the delivery was accepted and silently lose it, and writing a `status='failed'` row
   into the database that just failed is not a plan. We log safely and return **503** so GitHub
   retries; the retry deduplicates normally once storage recovers.

The table also carries `status` and `error` columns for internal diagnostics. `GET /events` returns
only the five documented fields, so internal state cannot leak through a debugging endpoint.

## Why SQLite

The workload is a low-rate append of small rows with a uniqueness constraint, read back
occasionally. SQLite via `aiosqlite` does that with zero operational surface — no server, no
container, no connection pool — and the uniqueness guarantee comes from the schema rather than from
application logic. Postgres or Redis would add a dependency to the run instructions and buy nothing
here; SQLAlchemy would add a layer over four hand-written statements. Connections are opened per
operation, which is fine at webhook rates. The trade-off is horizontal scaling: multiple instances
writing one file is not a design that grows, and if it ever needed to, `db.py` is the only module
that would change.

## Server-side GitHub authentication

The PAT is a credential *this service* holds, not one its callers present. It is loaded from
`GITHUB_TOKEN` into a `SecretStr`, attached as a `Bearer` header on outbound requests only, and
never appears in a response, a log line, a fixture, or this repository.

**The OpenAPI wording.** The assignment asks to "mark security schemes (http bearer) for routes
that require server-side auth to GitHub", but OpenAPI's `security` describes what *clients of our
API* must send — applying it would instruct callers to send our PAT. So the spec declares
`githubServerAuth` as `type: http, scheme: bearer` with a description stating it is a server-side
outbound credential, sets top-level `security: []`, and marks the GitHub-backed operations with
`x-upstream-security: [githubServerAuth]`. The scheme is declared, the routes are marked, and no
caller is ever told to send a token. A contract test asserts no operation carries a `security` block.

Our own API has no caller authentication, which is right for a local assignment service and would
be the first thing to add before exposing it anywhere real.

## Why there is no public `GET /issues/{number}/comments`

The required API defines only `POST` for comments. The assignment's integration step says "create
comment; fetch comments list", which reads as *verify the comment exists* rather than *add a route*.
So `GitHubClient.list_comments()` exists, the integration test calls GitHub's API directly to verify,
and the public surface stays exactly as specified. Adding an unrequested route would have been the
larger liberty.

## Extra credit not taken

Conditional GET with ETags is deliberately not implemented; the required functionality came first.
The architecture stays compatible: `GitHubClient._request()` is the single funnel where an
`If-None-Match` header and a 304 branch would go, and an `etag_cache` table would sit beside the
existing one in `db.py` without touching a single route.
