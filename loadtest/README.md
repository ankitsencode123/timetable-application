# Timely load test (k6)

This test models 120 students and up to four admins active together. Students load the published timetable and metadata, sometimes open the calendar, and ask the public AI chatbot in about one out of every four iterations. Admins authenticate, read the current draft/version list, and can optionally submit real timetable edits.

## Run a read-only load test

1. Start the backend and point it at a database with a published timetable and usable admin account(s). Install [k6](https://grafana.com/docs/k6/latest/set-up/install-k6/).
2. Start with a small smoke run (for example, 5 students and 1 admin) before testing at full load.
3. Set the API URL and admin credentials in your shell. Do not commit credentials or place production secrets in this file.

```bash
export BASE_URL="https://your-staging-api.example.com"
export ADMIN_EMAIL="loadtest-admin@example.com"
export ADMIN_PASSWORD="your-staging-admin-password"

k6 run loadtest/timetable.js
```

By default, the test ramps up for 2 minutes, holds 120 student VUs and 4 admin VUs for 5 minutes, then ramps down for 1 minute. A VU waits between iterations to model people reading and thinking, so 124 VUs are concurrent sessions rather than a claim of 124 requests per second. The two scenarios ramp together.

For a sharper arrival spike, run a second pass with `RAMP_DURATION=10s`:

```bash
RAMP_DURATION=10s HOLD_DURATION=5m RAMP_DOWN_DURATION=1m k6 run loadtest/timetable.js
```

This brings all 124 VUs online over ten seconds, then keeps them active together during the hold period.

Example smaller run:

```bash
STUDENT_VUS=5 ADMIN_VUS=1 RAMP_DURATION=15s HOLD_DURATION=1m RAMP_DOWN_DURATION=15s \
  k6 run -e BASE_URL="$BASE_URL" -e ADMIN_EMAIL="$ADMIN_EMAIL" \
  -e ADMIN_PASSWORD="$ADMIN_PASSWORD" loadtest/timetable.js
```

The default run is read-only apart from login. It needs a published timetable for the public student checks and a draft for the admin check. It includes starter thresholds for read, chat, and admin-edit latency; treat these as initial service objectives and tune them to your deployment and user expectations. The AI threshold is looser because the request waits on the external LLM provider.

## Exercise simultaneous admin edits (staging only)

Admin edits create timetable versions and may trigger teacher notifications. Keep this disabled for production. First make a disposable staging copy of the database, then set `K6_ADMIN_WRITE=true` and `K6_CONFIRM_STAGING=true`. Supply one JSON array of valid action objects for each admin VU. Use distinct, non-conflicting edits if measuring successful edit throughput; use overlapping edits if deliberately measuring the conflict path. The admin writes use the current draft version and unique idempotency keys. HTTP 409 stale/overlap responses are counted as expected concurrency conflicts.

```bash
export K6_ADMIN_WRITE=true
export K6_CONFIRM_STAGING=true
export K6_ADMIN_ACTIONS_JSON_1='[{"action":"CHANGE_ROOM","target":{"program":"B.Tech","semester":"5th","day":"Monday","start_time":"10:00","end_time":"11:00","subject_code":"cn","teacher":"SK","room":"R#205"},"new_room":"R#208"}]'
export K6_ADMIN_ACTIONS_JSON_2="$K6_ADMIN_ACTIONS_JSON_1"
export K6_ADMIN_ACTIONS_JSON_3="$K6_ADMIN_ACTIONS_JSON_1"
export K6_ADMIN_ACTIONS_JSON_4="$K6_ADMIN_ACTIONS_JSON_1"
k6 run loadtest/timetable.js
```

The example target values are illustrative: change them to identify a class that exists in your staging timetable, and choose a valid free room. The example repeats the same edit to provoke overlapping-edit handling; for successful independent edits, provide different existing classes and destinations for each admin. Actions must match the schema in `backend/app/schemas/actions.py` and `backend/app/actions/types.py`. Set `ADMIN_VUS=3` if only three admin credentials/action payloads are configured. For distinct admin accounts, set `ADMIN_EMAIL_1` / `ADMIN_PASSWORD_1` through the number of admin VUs; if those are unset, all VUs use the shared `ADMIN_EMAIL` / `ADMIN_PASSWORD` account.

Useful knobs:

- `STUDENT_VUS`, `ADMIN_VUS`: peak concurrent users; defaults are 120 and 4.
- `RAMP_DURATION`, `HOLD_DURATION`, `RAMP_DOWN_DURATION`: load shape; defaults are 2m, 5m, and 1m.
- `CHAT_EVERY`: approximate chat frequency denominator; default 4 means about 25% of student iterations call the chatbot.
- `BASE_URL`: origin of the FastAPI backend, without `/api`.

The first run should be a smoke test. Then use the full user counts, inspect k6's latency percentiles and failure rate, and correlate them with API worker saturation, database pool wait time, PostgreSQL CPU/locks, and LLM provider latency/rate limits. Run the test from a separate machine or load generator so k6 does not compete with the service for CPU.

## System design priorities for this app

1. **Use PostgreSQL for concurrency testing and production.** The repository's Compose file defaults to SQLite, which serializes writers and does not represent a multi-worker production database. The backend engine also uses SQLAlchemy's default pool settings; size the PostgreSQL connection limit, SQLAlchemy pool, and API worker count together so `workers × pool_size` stays within the database's connection budget. Add pool timeout/wait and database utilization metrics.
2. **Scale API workers horizontally only after validating shared-state coordination.** The timetable edit path in this working tree already has a per-scope lock, optimistic version checks, stale-edit handling, and idempotency support. Keep those protections enabled across all workers and run the gated write scenario on staging to validate them with the real database. Avoid process-local locks as the only correctness mechanism.
3. **Protect the synchronous LLM path.** `/api/public/chat` makes an external Groq call inside a synchronous FastAPI endpoint and rebuilds timetable context from the database for each request. Put explicit timeouts, bounded concurrency, rate limits/quotas, retries with backoff for transient provider failures, and a circuit breaker around the provider. Return a clear temporary-unavailable response when saturated. Consider a worker queue only for tasks users can accept asynchronously; for interactive chat, bounded direct calls and graceful degradation are usually clearer.
4. **Cache read-heavy published timetable data.** Student timetable/meta reads are identical for many users. Cache the published version or serialized read response with a key containing the published version ID, invalidate/update on publish, and use HTTP `ETag`/`Cache-Control` so a CDN or reverse proxy can serve public reads. Keep personalized/admin draft data out of public caches.
5. **Add capacity and correctness observability.** Track request rate/latency/error rate by route, DB pool wait and query time, 5xx/409 counts, LLM timeouts/429s/latency/cost, worker CPU/memory, and queue depth if introduced. Alert on sustained error/latency objectives. Test against a production-like dataset and repeat at 120 students + 4 admins, then increase load to identify headroom.
6. **Keep edit semantics explicit.** Preserve a client version/ETag precondition for edits, idempotency keys on retries, transaction boundaries, and audit history. Make clients refresh and present a conflict when overlapping edits return 409; never retry a mutation blindly without reusing its idempotency key.

The k6 thresholds are a starting point, not evidence that the current hosting plan can sustain this load; measure on the actual deployment shape and database tier.
