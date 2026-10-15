# Timely API load test with k6

`timetable.js` defaults to **1,000 concurrent virtual users (VUs)**: 996 student sessions and 4 admin sessions. It exercises the published timetable and metadata, opens the calendar sometimes, and calls the public AI chat in about one in four student iterations. Admins log in and read the draft and version list. Writes are off by default.

A VU represents a session doing requests and then waiting to model a person's think time. It does not mean one request per second. This is a load profile, not a promise that a particular deployment can handle the load.

## 1. Install k6

Install k6 using the [official installation instructions](https://grafana.com/docs/k6/latest/set-up/install-k6/). For example:

```bash
# macOS with Homebrew
brew install k6

# Windows with Chocolatey
choco install k6

# Debian or Ubuntu: use Grafana's official apt repository/instructions
# https://grafana.com/docs/k6/latest/set-up/install-k6/
```

Check the install:

```bash
k6 version
```

## 2. Prepare the API and test data

Run the test against a staging deployment or a production-like local setup. Use a database with a published timetable and a working admin account. The endpoint `/api/public/timetable` returns 404 when there is no published timetable.

For a local development run, start the backend from the repository root:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate       # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The app initializes its schema and seeds an admin and starter timetable on first startup if the database is empty. Configure `DATABASE_URL`, `ADMIN_EMAIL`, and `ADMIN_PASSWORD` before startup if you need specific credentials. With the default local configuration, the seeded admin credentials are `admin@example.com` / `changeme`; change these for any shared environment.

The repository's Docker Compose setup uses SQLite by default. It is convenient for a smoke test, but SQLite serializes writes and is not representative of PostgreSQL production capacity. For meaningful capacity results, run against a staging database and API sized like the intended deployment. Generate load from a separate machine so k6 does not take CPU away from the API.

Check that the API is up and has a published timetable:

```bash
curl -f http://127.0.0.1:8000/api/health
curl -f http://127.0.0.1:8000/api/public/timetable
```

## 3. Run a small smoke test first

From the repository root, set the API URL and admin credentials. `BASE_URL` is the backend origin, without `/api`:

```bash
export BASE_URL="http://127.0.0.1:8000"
export ADMIN_EMAIL="admin@example.com"
export ADMIN_PASSWORD="changeme"

STUDENT_VUS=5 ADMIN_VUS=1 RAMP_DURATION=15s HOLD_DURATION=1m RAMP_DOWN_DURATION=15s \
  k6 run loadtest/timetable.js
```

On Windows PowerShell:

```powershell
$env:BASE_URL = "http://127.0.0.1:8000"
$env:ADMIN_EMAIL = "admin@example.com"
$env:ADMIN_PASSWORD = "changeme"
$env:STUDENT_VUS = "5"
$env:ADMIN_VUS = "1"
$env:RAMP_DURATION = "15s"
$env:HOLD_DURATION = "1m"
$env:RAMP_DOWN_DURATION = "15s"
k6 run loadtest/timetable.js
```

The smoke test includes login, reads, and a small amount of AI chat. If your test environment should not call the LLM, set `CHAT_EVERY=100000` for a nearly chat-free run.

## 4. Run at 1,000 concurrent VUs

Once the smoke test is healthy, run the default profile. It ramps to 1,000 sessions over 2 minutes, holds for 5 minutes, then ramps down over 1 minute:

```bash
k6 run loadtest/timetable.js
```

Use these explicit settings to make the 1,000 total VUs clear:

```bash
STUDENT_VUS=996 ADMIN_VUS=4 RAMP_DURATION=2m HOLD_DURATION=5m RAMP_DOWN_DURATION=1m \
  k6 run loadtest/timetable.js
```

To run 1,000 student sessions and no admin sessions (no credentials needed):

```bash
STUDENT_VUS=1000 ADMIN_VUS=0 RAMP_DURATION=2m HOLD_DURATION=5m RAMP_DOWN_DURATION=1m \
  k6 run loadtest/timetable.js
```

For a short ramp spike, use `RAMP_DURATION=10s`. Coordinate with whoever operates the target before doing this against a shared environment.

## 5. Read the results

k6 prints request counts, failed checks, error rate, and latency percentiles. The script has starter thresholds: under 1% failed HTTP requests, read p95 under 1 second, admin read p95 under 1.5 seconds, and chat p95 under 15 seconds. A threshold failure means the run did not meet those starter targets; tune them to the actual service objectives. Correlate results with API CPU/memory and worker count, PostgreSQL CPU/connections/locks, database pool waits, and LLM latency, rate limits, and cost.

To save a machine-readable summary:

```bash
k6 run --summary-export=loadtest/summary.json loadtest/timetable.js
```

Avoid committing summaries containing environment details unless intended.

## Optional: staging-only admin write test

Writes create timetable versions and may notify teachers. Use a disposable staging database only. The script refuses writes unless both safety flags are set. Supply valid action JSON for each admin VU; the sample action in the old guide is only illustrative and must match a class and available room in your own staging data.

```bash
export K6_ADMIN_WRITE=true
export K6_CONFIRM_STAGING=true
export K6_ADMIN_ACTIONS_JSON_1='[{"action":"CHANGE_ROOM","target":{"program":"B.Tech","semester":"5th","day":"Monday","start_time":"10:00","end_time":"11:00","subject_code":"cn","teacher":"SK","room":"R#205"},"new_room":"R#208"}]'
export K6_ADMIN_ACTIONS_JSON_2="$K6_ADMIN_ACTIONS_JSON_1"
export K6_ADMIN_ACTIONS_JSON_3="$K6_ADMIN_ACTIONS_JSON_1"
export K6_ADMIN_ACTIONS_JSON_4="$K6_ADMIN_ACTIONS_JSON_1"
k6 run loadtest/timetable.js
```

Use distinct, valid edits to measure successful write throughput. Repeating an edit can exercise stale/overlap conflict handling. HTTP 409 conflicts are counted as expected for this gated write scenario. The write scenario is deliberately not required for the 1,000-user read-load run.

## Configuration

- `BASE_URL`: backend origin; default `http://127.0.0.1:8000`.
- `STUDENT_VUS`: student session peak; default `996`.
- `ADMIN_VUS`: admin session peak from 0 to 4; default `4`.
- `RAMP_DURATION`, `HOLD_DURATION`, `RAMP_DOWN_DURATION`: default `2m`, `5m`, `1m`.
- `CHAT_EVERY`: denominator for chat frequency; default `4` (about 25% of student iterations).
- `ADMIN_EMAIL`, `ADMIN_PASSWORD`: shared admin login for read scenario.
- `ADMIN_EMAIL_1` / `ADMIN_PASSWORD_1` through `_4`: optional per-admin credentials.
