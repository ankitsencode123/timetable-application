# Timely System Design: 1 Million Registered Users

**Purpose:** explain how Timely could grow from a laptop prototype into a production service designed around 1 million registered accounts. This is a target architecture and capacity estimate, not a claim that the current laptop build has been proven at that scale.

## 1. Product and workload

Timely serves students, teachers, and school administrators. Students mostly read published timetables and calendars. Administrators and teachers authenticate, edit draft schedules, validate constraints, and publish versions. AI chat is an optional, slower path and must not hold up timetable reads or writes.

| Given estimate | Design interpretation |
| --- | --- |
| 1,000,000 registered users | Long-term account count; not all accounts are online together. |
| 300,000–600,000 MAU | 30–60% monthly engagement. |
| 50,000–200,000 DAU | 5–20% daily engagement. |
| 5,000–20,000 peak concurrent users | Online sessions at busy periods. |
| 1–10 million API requests/day | About 12–116 average requests/second over 24 hours. |
| 30–300 million API requests/month | Consistent with the daily range (30 days). |

The estimates do not define a peak request rate: concurrency alone is not requests per second. For an initial capacity exercise, assume a 10× burst over the daily average, or roughly **120–1,160 requests/second**, then load test a **2,000 requests/second read-heavy peak** as a rounded target. Recalculate this after observing real request rates, payload sizes, cache hit rate, and the peak-hour shape. AI requests have separate limits and are excluded from the normal API latency budget.

## 2. Design goals

- Serve public timetable and calendar reads quickly during start-of-day bursts.
- Keep schedule edits correct when multiple administrators act on the same draft.
- Add API capacity by adding stateless instances; protect PostgreSQL from connection storms.
- Keep AI, email, and expensive schedule generation off the synchronous read path.
- Scope data and permissions by school/tenant, with auditable publication history.
- Start with the smallest architecture that can be measured, then add capacity where metrics show pressure.

## 3. Production target architecture

The diagram is a proposed deployment topology. Redis, a queue, object storage/CDN, managed PostgreSQL, and separately scaled workers are additions to the current local FastAPI/PostgreSQL application.

[Open the interactive architecture diagram](./system-design.html)

### Request paths

**Public read:** browser → CDN/WAF → load balancer → API → Redis cache → PostgreSQL on a cache miss. Public published timetable responses are keyed by tenant, timetable version, and filters. Publication changes the version and invalidates or replaces the cached object. Static JS/CSS/images are served directly by the CDN.

**Admin write:** authenticated browser → API → tenant/role check → PostgreSQL transaction. The request includes the draft version (optimistic concurrency) and an idempotency key. The server validates constraints, applies the edit, writes an audit event, and uses the transactional outbox to schedule follow-up work without losing events if a process crashes.

**Slow work:** API writes a job/outbox row and returns a job ID where the user can wait for progress. A queue delivers scheduling, notification, or AI tasks to separately scaled workers. Workers retry transient failures with backoff, deduplicate by job ID, and move repeatedly failing jobs to a dead-letter queue. AI calls have their own rate limits, timeout, budget, and fallback response.

## 4. Main components and scaling

| Component | Responsibility | Scale / safety approach |
| --- | --- | --- |
| CDN + WAF | Static assets, TLS edge, bot/DDoS filtering, optional cache for anonymous public reads | Global edge cache; purge/version on publish; per-IP and per-account limits. |
| Load balancer | Health checks and routing to API instances | Spread traffic across availability zones; drain unhealthy instances. |
| FastAPI API | Auth, timetable reads/writes, validation, APIs | Stateless replicas; horizontal autoscaling on CPU, request latency, and in-flight requests. Bound each instance's DB pool. |
| Redis | Cache hot published schedules, metadata, and short-lived rate-limit/session data | Managed replication and failover; TTL plus explicit versioned invalidation; cache outage falls back to DB with request shedding. |
| PostgreSQL | Source of truth for users, tenants, schedules, versions, and audit records | Multi-AZ primary, backups/PITR, read replicas for read-only queries; connection pooler; indexes and query plans. |
| Queue + worker pool | Schedule generation, email, exports, AI orchestration, cache refresh | Separate worker pools and concurrency limits per task type; retry, deduplicate, dead-letter, monitor queue age. |
| Object storage | Exports, snapshots, attachments, static build artifacts | Private by default; short-lived signed URLs; lifecycle policies. |
| Observability | Logs, traces, metrics, alerting | Correlation IDs across API and jobs; redact tokens/PII; alert on user-visible SLOs and queue/database saturation. |

### Data model and tenant isolation

Use PostgreSQL as the authoritative store. Add a required `tenant_id` (school/organization) to tenant-owned rows and include it in unique constraints, foreign keys, and common query indexes. Authorization derives tenant access from the verified identity; never trust a client-supplied tenant ID alone. Enforce tenant scoping in a shared repository/dependency layer and consider PostgreSQL row-level security as defense in depth. Keep users who belong to multiple schools through an explicit membership table.

Keep draft and published schedule versions immutable once published. A publish operation atomically advances the tenant's active version, records who published it, and emits an outbox event. For edits, compare the expected draft version inside the transaction; return `409 Conflict` for stale updates so the client can refresh or merge. Use database constraints for invariants that can be expressed relationally, and the existing constraint engine for cross-row scheduling rules.

Start with one PostgreSQL cluster and a read replica, not tenant shards. Partition very large append-only audit/event tables by time only after measurements justify it. Introduce tenant sharding only when a single cluster is demonstrably the limiting factor; tenant ID provides a natural routing key.

## 5. Capacity reasoning

At 1–10 million calls/day, average load is approximately 12–116 RPS. The high estimate is 300 million calls per 30-day month. A 10× peak multiplier gives 120–1,160 RPS; use 2,000 RPS as an initial peak test target with a read-heavy mix. These are planning numbers, not benchmark results.

The first resource likely to saturate is not necessarily API CPU. Watch database pool wait time, read replica lag, cache misses, serialization size, and queue age. Cache public read results only when tenant/version/filters are part of the cache key. Use pagination and bounded response sizes for admin lists. Reuse pooled DB connections and size the pool across all API replicas so total connections remain below the PostgreSQL limit. Autoscaling should not create more connections than the database can serve.

Define service objectives as initial targets to validate, for example: 99.9% monthly availability for published timetable reads, p95 under 300 ms for cached reads and under 800 ms for uncached reads at the agreed load, and p95 under 1 s for ordinary edits (excluding explicitly asynchronous scheduling/AI work). Track errors and latency by endpoint, tenant tier, and cache hit/miss. A successful load test must report the exact build, machine/deployment shape, dataset, traffic mix, duration, and percentiles.

## 6. Laptop-only implementation and honest demo

The laptop is the development and demonstration environment. It cannot prove production capacity for one million accounts: it has one physical CPU/memory/storage/network envelope, and a local load generator competes with the app for those same resources. A million rows of synthetic accounts also do not equal one million simultaneous users.

For a laptop prototype, run the React app, FastAPI, PostgreSQL, Redis, and a lightweight worker in Docker Compose. Keep one API process and conservative worker concurrency. Seed synthetic tenants/users/schedules. Run the existing k6 journey at progressively larger VU counts, beginning with a smoke run; record p50/p95/p99 latency, failures, CPU, memory, DB connections, query latency, and cache hit rate. Treat 1,000 virtual users as a useful local demonstration profile, not evidence of 20,000 concurrent users or 2,000 RPS. Do not call the external AI service during capacity tests unless deliberately testing its separate budget and limits.

The current compose file uses SQLite by default, and the current k6 README correctly notes that SQLite writes serialize. For a meaningful backend demo, switch the local profile to PostgreSQL and Redis, keep the traffic generator on a separate machine if possible, and publish the resulting measurements with the topology. If the only available hardware is one laptop, disclose that the measurement is resource-contended.

### What to say in the presentation

> “My current build runs on a laptop, so I’m not claiming it serves one million real users today. I designed the production path for one million registered accounts and a 2,000 requests/second test target. The public read path scales through CDN and Redis caching, the API scales horizontally, PostgreSQL remains the source of truth, and slow work is isolated in queues. I can demonstrate the request mix locally and show the metrics; cloud-scale capacity still needs a distributed load test.”

## 7. Security, reliability, and operations

- Require TLS, secure password hashing, short-lived access tokens, refresh-token rotation/revocation, and MFA for administrators where supported.
- Apply tenant and role checks to every read and write. Rate-limit login, public chat, and expensive schedule generation independently.
- Keep secrets in a managed secret store in production; never commit credentials. Encrypt database backups and storage. Minimize and redact personal data in logs.
- Use automated backups and point-in-time recovery; regularly rehearse restore. Deploy migrations with backward-compatible expand/migrate/contract steps.
- Run health/readiness checks, structured logs, metrics, traces, and alerts. Roll out API changes gradually and retain a rollback path.
- Use idempotency for retryable writes and queue jobs. Keep published timetable reads available during AI or worker outages.

## 8. Delivery roadmap

1. **Laptop baseline:** PostgreSQL + Redis local profile, tenant-aware schema, versioned public cache, query/index review, reproducible k6 scenarios and measurements.
2. **Single-region production:** CDN/WAF, load balancer, multiple API replicas, managed PostgreSQL with backups/read replica, managed Redis, queue and worker pools, dashboards and alerts.
3. **Scale by evidence:** tune hot queries and payloads; raise replica/worker capacity; add read replicas or partition append-only data when metrics justify it; shard by tenant only after a single cluster reaches a measured ceiling.
4. **Resilience:** test zone failure, database restore, cache outage, queue backlog, and dependency timeouts; document recovery objectives and runbooks.

## 9. Assumptions and open measurements

The traffic mix, request sizes, tenant count, timetable size, school-day burst duration, geographic distribution, availability target, and AI share are not supplied. The 10× peak factor and 2,000 RPS target are explicit planning assumptions. Before sizing a cloud bill or promising an SLA, collect those measurements and test the chosen deployment. The design intentionally avoids a premature fixed server count because hardware and managed-service performance vary.
