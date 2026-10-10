import http from 'k6/http';
import { check, fail, sleep } from 'k6';
import { Counter } from 'k6/metrics';

const BASE_URL = (__ENV.BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
const API = `${BASE_URL}/api`;
const STUDENT_VUS = Number(__ENV.STUDENT_VUS || 120);
const ADMIN_VUS = Number(__ENV.ADMIN_VUS || 4);
const RAMP = __ENV.RAMP_DURATION || '2m';
const HOLD = __ENV.HOLD_DURATION || '5m';
const RAMP_DOWN = __ENV.RAMP_DOWN_DURATION || '1m';
const CHAT_EVERY = Number(__ENV.CHAT_EVERY || 4); // about one chat request per four student iterations
const ALLOW_ADMIN_WRITES = __ENV.K6_ADMIN_WRITE === 'true';
const adminWriteConflicts = new Counter('admin_write_conflicts');

if (!Number.isInteger(STUDENT_VUS) || STUDENT_VUS < 1) fail('STUDENT_VUS must be a positive integer');
if (!Number.isInteger(ADMIN_VUS) || ADMIN_VUS < 0 || ADMIN_VUS > 4) fail('ADMIN_VUS must be between 0 and 4');
if (!Number.isInteger(CHAT_EVERY) || CHAT_EVERY < 1) fail('CHAT_EVERY must be a positive integer');
if (ALLOW_ADMIN_WRITES && __ENV.K6_CONFIRM_STAGING !== 'true') {
  fail('Admin writes require K6_CONFIRM_STAGING=true. Use a disposable staging database.');
}

const scenarios = {
  students: {
    executor: 'ramping-vus',
    exec: 'studentJourney',
    startVUs: 0,
    stages: [
      { duration: RAMP, target: STUDENT_VUS },
      { duration: HOLD, target: STUDENT_VUS },
      { duration: RAMP_DOWN, target: 0 },
    ],
    gracefulRampDown: '30s',
  },
};

if (ADMIN_VUS > 0) {
  scenarios.admins = {
    executor: 'ramping-vus',
    exec: 'adminJourney',
    startVUs: 0,
    stages: [
      { duration: RAMP, target: ADMIN_VUS },
      { duration: HOLD, target: ADMIN_VUS },
      { duration: RAMP_DOWN, target: 0 },
    ],
    gracefulRampDown: '30s',
  };
}

export const options = {
  scenarios,
  thresholds: {
    http_req_failed: ['rate<0.01'],
    'http_req_duration{endpoint:student_read}': ['p(95)<1000', 'p(99)<2000'],
    'http_req_duration{endpoint:student_chat}': ['p(95)<15000'],
    'http_req_duration{endpoint:admin_read}': ['p(95)<1500', 'p(99)<3000'],
    'http_req_duration{endpoint:admin_write}': ['p(95)<5000'],
  },
  userAgent: 'Timely-k6-load-test/1.0',
};

const jsonHeaders = { headers: { 'Content-Type': 'application/json' } };
const chatPrompts = [
  'What classes are scheduled today?',
  'Show me the timetable for Monday.',
  'Which room is my next class in?',
  'What classes does SK teach this week?',
];

export function setup() {
  const response = http.get(`${API}/health`, { tags: { name: 'health_check' } });
  if (response.status !== 200) {
    fail(`Health check failed (${response.status}) at ${API}/health. Check BASE_URL and start the backend.`);
  }
  return { startedAt: new Date().toISOString() };
}

export function studentJourney() {
  const timetable = http.get(`${API}/public/timetable`, {
    tags: { name: 'student_timetable', endpoint: 'student_read' },
  });
  check(timetable, {
    'published timetable returns 200': (r) => r.status === 200,
    'published timetable is a JSON array': (r) => Array.isArray(parseJson(r)),
  });

  // Metadata is a common companion request when the timetable page opens.
  const meta = http.get(`${API}/public/meta`, {
    tags: { name: 'student_timetable_meta', endpoint: 'student_read' },
  });
  check(meta, { 'timetable metadata returns 200': (r) => r.status === 200 });

  // About 25% of student iterations ask the public AI assistant.
  if (Math.random() < 1 / CHAT_EVERY) {
    const prompt = chatPrompts[Math.floor(Math.random() * chatPrompts.length)];
    const response = http.post(`${API}/public/chat`, JSON.stringify({ message: prompt }), {
      ...jsonHeaders,
      timeout: '45s',
      tags: { name: 'student_chat', endpoint: 'student_chat' },
    });
    check(response, { 'student chat returns 200': (r) => r.status === 200 });
  } else if (Math.random() < 0.35) {
    // Date-based calendar view, using the current month and today's date.
    const now = new Date();
    const month = http.get(`${API}/calendar/month/${now.getFullYear()}/${now.getMonth() + 1}`, {
      tags: { name: 'student_calendar_month', endpoint: 'student_read' },
    });
    check(month, { 'calendar month returns 200': (r) => r.status === 200 });
  }

  sleep(2 + Math.random() * 4); // student think time, 2–6 seconds
}

let cachedAdminToken;

function getAdminToken() {
  if (cachedAdminToken) return cachedAdminToken;
  // VU IDs can include the student scenario's VUs; modulo maps admin VUs to 1–4.
  const n = ((__VU - 1) % 4) + 1;
  const email = __ENV[`ADMIN_EMAIL_${n}`] || __ENV.ADMIN_EMAIL;
  const password = __ENV[`ADMIN_PASSWORD_${n}`] || __ENV.ADMIN_PASSWORD;
  if (!email || !password) {
    fail(`Set ADMIN_EMAIL and ADMIN_PASSWORD (or ADMIN_EMAIL_${n} / ADMIN_PASSWORD_${n}) for admin VU ${n}.`);
  }
  const response = http.post(`${API}/auth/login`, JSON.stringify({ email, password }), {
    ...jsonHeaders,
    tags: { name: 'admin_login', endpoint: 'admin_read' },
  });
  const body = parseJson(response);
  if (response.status !== 200 || !body || !body.access_token) {
    fail(`Admin VU ${n} could not authenticate (HTTP ${response.status}). Check admin credentials.`);
  }
  cachedAdminToken = body.access_token;
  return cachedAdminToken;
}

export function adminJourney() {
  const token = getAdminToken();
  const params = {
    headers: { Authorization: `Bearer ${token}` },
    tags: { endpoint: 'admin_read' },
  };

  const draft = http.get(`${API}/timetable/draft`, { ...params, tags: { ...params.tags, name: 'admin_draft' } });
  check(draft, { 'admin draft returns 200': (r) => r.status === 200 });
  const versions = http.get(`${API}/versions`, { ...params, tags: { ...params.tags, name: 'admin_versions' } });
  check(versions, { 'admin versions returns 200': (r) => r.status === 200 });

  if (ALLOW_ADMIN_WRITES) {
    const userNo = ((__VU - 1) % 4) + 1;
    const encoded = __ENV[`K6_ADMIN_ACTIONS_JSON_${userNo}`] || __ENV.K6_ADMIN_ACTIONS_JSON;
    if (!encoded) fail(`Set K6_ADMIN_ACTIONS_JSON_${userNo} (JSON array of action objects) before enabling admin writes.`);
    let actions;
    try {
      actions = JSON.parse(encoded);
    } catch (_) {
      fail(`K6_ADMIN_ACTIONS_JSON_${userNo} is not valid JSON.`);
    }
    if (!Array.isArray(actions) || actions.length === 0) fail('Admin action JSON must be a non-empty array.');

    const draftBody = parseJson(draft);
    const payload = JSON.stringify({ version_id: draftBody && draftBody.id, actions, partial_ok: true });
    const write = http.post(`${API}/actions/execute`, payload, {
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
        'Idempotency-Key': `k6-${__VU}-${__ITER}-${Date.now()}`,
      },
      timeout: '30s',
      responseCallback: http.expectedStatuses(200, 409), // 409 is a valid stale/overlap conflict under simultaneous edits
      tags: { name: 'admin_timetable_edit', endpoint: 'admin_write' },
    });
    check(write, {
      'admin edit succeeds or reports a concurrency conflict': (r) => {
        if (r.status === 409) return true;
        const body = parseJson(r);
        return r.status === 200 && body && (body.success || body.partial_applied);
      },
    });
    if (write.status === 409) adminWriteConflicts.add(1);
  }

  sleep(2 + Math.random() * 5); // admin review/edit think time, 2–7 seconds
}

function parseJson(response) {
  try {
    return response.json();
  } catch (_) {
    return null;
  }
}
