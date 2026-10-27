# Architecture & Implementation Decisions

## Date: 2026-10-15

### 1. Removal of Weekly Hours from Subjects Catalog
- **Decision**: Removed the "Weekly Hours" input field from the "+ New Subject" form in `frontend/src/components/catalog/CatalogManager.tsx` and removed the `· {s.weekly_hours}h/wk` suffix from the subject list display. Defaulted `weekly_hours` to `2` during POST request payload creation.
- **Reasoning**: The manual input field was redundant for catalog creation and cluttered the subject entry row. The backend database schema and API schema (`SubjectIn`) already provide a default value of 2, preserving backwards compatibility and constraint engine integrity while simplifying the user interface.

### 2. Reduction of HTTP/HTTPS Cache Time to 15 Seconds
- **Decision**: Lowered `PUBLIC_CACHE_SECONDS` from 30 to 15 in both `backend/app/calendar_system.py` and root `calendar_system.py`.
- **Reasoning**: Ensures that public timetable endpoints (`/api/calendar/day/{date}` and `/api/calendar/range`) issue `Cache-Control: public, max-age=15` headers matching the 15-second configuration in the cache layer (`TT_HTTP_MAX_AGE = 15`). This minimizes stale data visibility for students and edge viewers after administrative updates.

### 3. Date Parsing Typo Tolerance & Modifier Handling in AI Chat
- **Decision**: Expanded weekday lexicons (`_WD`, `_WD_FULL`) in `backend/app/calendar_chat.py` to capture common spelling errors (e.g. `wednesay`, `wensday`, `wednessday`, `thursay`, `tueday`, `firday`, `satruday`). Updated date pattern regexes to recognize `"this coming"`, `"coming"`, `"upcoming"`, and optional trailing `"day"` tokens. Enhanced `_upcoming_weekday` arithmetic to ensure that upcoming modifiers always resolve to the correct next occurrence (and advance by 7 days if asked on the same day).
- **Reasoning**: Natural human phrasing in conversational chat frequently includes typos like "wednesay" or compound phrases like "this coming wednesday day". Without explicit lexicon coverage, the date parser failed to recognize any date entity and fell back to the current day (Thursday).

### 4. Public Chat First-Person ("I" / "me") Query Handling for Students
- **Decision**: Modified `answer_calendar_question` in `backend/app/calendar_chat.py` so that unauthenticated public chat users using first-person pronouns ("I", "my") without an explicit program or semester filter are no longer blocked with an error requesting teacher login. Instead, the schedule for the requested date/range is returned directly, accompanied by an informative note explaining how to filter for their specific batch (e.g. "B.Tech 3rd sem"). If the student explicitly specifies "all classes", the full schedule is delivered cleanly.
- **Reasoning**: Public chat is designed for students, who do not have accounts or login portals in the application. Telling a student "I can't tell who I/my is without logging in ... or log in as a teacher" was misleading and confusing. Treating public chat users as students and returning the requested schedule with filtering guidance fulfills their intent immediately.

### 5. Reschedule Modal Conflict-Free Suggestions UI & Overflow Fix
- **Decision**: Redesigned the suggestions display in `frontend/src/components/timetable/RescheduleModal.tsx`. Structured the raw string into clean semantic components: action type tag (Room change vs Reschedule), conflict-free status badge, clear headline, metadata pills (slot, room), and wrapped description. Set `flex: 1` and `minWidth: 0` on content wrappers with `wordBreak: 'break-word'` / `overflowWrap: 'anywhere'`. Fixed the modal wrapper to `maxWidth: 540`, removed the restrictive `modal-sm` constraint, and applied `overflowX: 'hidden'` with `boxSizing: 'border-box'`.
- **Reasoning**: The previous flex layout caused long text titles to expand uncontrollably, pushing the "Apply" button off-screen and clipping it horizontally. The restructured card layout guarantees that the "Apply" button is always fully visible and clickable without horizontal overflow or modal clipping.

## Date: 2026-10-16

### 1. Relocation of Notification Service to Standard Service Directory
- **Decision**: Moved `Notification_Service.py` from the project root directory into `backend/app/services/notification_service.py`.
- **Reasoning**: Aligns with the backend architectural layout where business logic and domain services (such as `email_service.py`, `publication_service.py`, and `audit_service.py`) reside, preventing root directory clutter and enabling clean imports across the application.

### 2. Explicit Environment Variable Loading via `python-dotenv`
- **Decision**: Added explicit `load_dotenv` calls in `backend/app/services/notification_service.py` targeting `backend/.env`.
- **Reasoning**: The core application utilizes `pydantic-settings`, which reads environment files into model instances without necessarily setting process-level `os.environ` keys. Because `notification_service.py` inspects `os.getenv()`, explicit loading ensures that critical configuration settings (`BREVO_API_KEY`, `SENDER_EMAIL`, `NOTIFY_*`) are always populated regardless of whether execution is via FastAPI, background scripts, or standalone CLI workers.

### 3. Background Worker Lifecycle Integration into FastAPI Lifespan
- **Decision**: Integrated notification schema creation (`ensure_notification_schema`), worker thread start (`start_worker`), and graceful shutdown (`stop_worker`) within the FastAPI `lifespan` handler in `backend/app/main.py`.
- **Reasoning**: Ensures database tables (`notification_events`, `notification_deliveries`, `notification_attempts`, `notification_state`) and indexes are idempotently verified at startup, starts the consumer worker concurrently with the API server, and prevents orphaned threads on application shutdown or reload.

### 4. Adoption of Durable Outbox Publishing (`publish_engine_result`)
- **Decision**: Replaced synchronous/in-memory `BackgroundTasks.add_task(notify_teachers_of_changes, ...)` in `backend/app/api/actions.py` and `backend/app/actions/advanced_chat.py` with `notif.publish_engine_result(result, actor=user)`.
- **Reasoning**: Directly dispatching emails via `BackgroundTasks` is non-durable and risks lost notifications during server restarts or transient network/API failures. The outbox pattern commits the event to persistent storage within ~1 ms during the request path, delegating fan-out and dispatching to worker threads with at-least-once delivery, exponential backoff, circuit breaking, and reconciliation.

### 5. Mounting Dedicated Notification Router
- **Decision**: Registered `notification_service.router` under the `/api/notifications` route prefix in `backend/app/api/router.py`.
- **Reasoning**: Exposes operational administration routes (`/stats`, `/deliveries`, `/events`, `/retry`, `/test`), external webhook receivers (`/webhooks/brevo`), and external scheduler cron triggers (`/tick`) under a standard API namespace protected by admin authorization.

### 6. Test Email Redirection Safeguards
- **Decision**: Configured `NOTIFY_REDIRECT_ALL_TO="ankitsen53806626@gmail.com:Ankit,ankitcursor478@gmail.com:Admin"` and `NOTIFY_ADMIN_EMAILS="ankitcursor478@gmail.com:Admin"` in `backend/.env`.
- **Reasoning**: Enforces strict testing safeguards ensuring that any notification triggered during development or test execution is automatically intercepted and delivered only to the authorized testing inboxes with tagged subjects (`[TEST → ...]`), preventing unintended emails to real faculty accounts.

## Date: 2026-10-20

### 1. Multi-Tier Hybrid Cache Architecture (L1 Exact + L2a/L2b/L2c Semantic)
- **Decision**: Integrated `backend/app/services/semantic_cache.py` alongside `backend/app/services/cache_layer.py` rather than replacing `cache_layer.py`. Placed `cache_layer.CHAT_CACHE` as an L1 ultra-fast exact/normalized lookup tier in front of the L2 `SemanticStore` (L2a exact canonical slot key, L2b hard-slot bucket + residual guard + hashed 3–5 char n-gram + word unigram/bigram cosine, and L2c optional dense embedding cosine).
- **Reasoning**: Combining both systems preserves `cache_layer.py`'s timetable snapshot caching (`GET /api/public/timetable`), ETag/304 handling, singleflight coalescing, and O(1) normalized string cache while adding paraphrase-tolerant semantic matching, slot-constrained bucket safety, and intent-aware volatile TTLs for `POST /api/public/chat`.

### 2. Shared Timetable Snapshot & Zero Duplicate DB Reads (`DataProvider` over `SnapshotStore`)
- **Decision**: Refactored `DataProvider` in `backend/app/services/semantic_cache.py` to derive its `DataState` (dynamic entity lexicon, enriched schedule rows, and content-derived epoch hash) directly from `cache_layer.STORE.get()` (`Snapshot`) in memory, instead of running independent SQL queries or registering duplicate SQLAlchemy `after_commit` listeners.
- **Reasoning**: Eliminates redundant database queries and ensures a single source of truth for timetable state across both `/api/public/timetable` and `/api/public/chat`. Whenever `cache_layer.STORE` refreshes or is invalidated, `DataProvider` automatically detects the changed `(built_at, etag)` pair and rebuilds the semantic lexicon and epoch in memory.

### 3. Cross-Tier Cache Synchronization Hooks & Unified Rate Limiting
- **Decision**: Added `register_cache_hooks` (`on_invalidate`, `on_purge`, `stats_name`, `stats_fn`) and optional `reserve` capacity support on `AsyncSlidingWindow.acquire` in `backend/app/services/cache_layer.py`. Connected `SemanticChatService` and `DataProvider` to these hooks and shared `cache_layer.CHAT._rpm` (`GROQ_RPM`) across both engines.
- **Reasoning**: Guarantees that any DB commit, `invalidate_timetable_data()` call, or admin `POST /api/admin/cache/purge` immediately invalidates both L1 (`CHAT_CACHE` / `STORE`) and L2 (`DataProvider` / `SemanticStore`), exposes combined L1 + L2 telemetry under `GET /api/admin/cache/stats`, and enforces a single process-wide Groq RPM budget with reserved capacity (`SEM_GROQ_RESERVE_FOR_NEW`) for novel queries.

### 4. Case-Preserving L1 Key & L2 Reference Validation
- **Decision**: Constructed the L1 key (`_l1_key`) by combining `cache_layer.normalize_question(text)` with case-preserved uppercase tokens from the raw query, the active timetable `epoch`, and the IST date. Stored lightweight L2 entry references (`("sem_ref", entry_id, status, score)`) in `cache_layer.CHAT_CACHE` and verified every L1 hit against `SemanticStore.get_exact(ref_id, now)` before serving.
- **Reasoning**: `cache_layer.normalize_question` casefolds all text and strips stopwords like `"it"`, `"in"`, `"on"`, and `"am"`. In a timetable context, uppercase tokens such as `"IT"` (Information Technology) or faculty initials carry distinct domain meaning and must not collide with lowercase stopwords. Validating L1 references against L2 ensures that epoch bumps, short volatile TTLs (`now`/`next`/`free` intents), admin entry deletions, and user downvotes (`POST /api/public/chat/feedback`) immediately invalidate L1 hits.

### 5. Chained Deterministic Fallback & Legacy Engine Fail-Safe
- **Decision**: Chained `SemanticChatService._deterministic_answer` (slot-filtered schedule formatting) with `cache_layer._fallback_answer` when Groq is unavailable, rate-limited, or circuit-broken, and added an automatic exception fallback to `cache_layer.CHAT.answer` (`SEM_LEGACY_FALLBACK=1`) plus a `CHAT_ENGINE=legacy` kill switch.
- **Reasoning**: Ensures high availability and graceful degradation. Even if an unexpected error occurs in the semantic pipeline or external LLM provider, students still receive an accurate, deterministic schedule response backed by the existing cache layer.

## Date: 2026-10-27

### 1. Fix Replace Class Payload Structure & Schema Aliasing
- **Decision**: 
  1. Updated `frontend/src/components/actions/ActionForm.tsx` so `REPLACE_CLASS` correctly constructs and submits `base.new_spec` instead of `base.spec`. Enhanced `TargetSelector.onSelect` to prefill `subject_code`, `subject_name`, and `teacher` from the target class, and added fallbacks to target slot properties so partial edits retain existing values.
  2. Added `@model_validator(mode='before')` to `ReplaceClassAction` in `backend/app/actions/types.py` to transparently map `'spec'` to `'new_spec'`. Added alias validators on `ClassTarget` and `ClassSpec` to seamlessly normalize `start` -> `start_time`, `end` -> `end_time`, and `type` -> `entry_type`.
- **Reasoning**: The modal threw a Pydantic 422 error (`1 validation error for ReplaceClassAction new_spec Field required`) because the frontend previously assigned `base.spec` (the key required by `ADD_CLASS`) instead of `base.new_spec`. Fixing the frontend key and adding defensive alias mappings in Pydantic guarantees full compatibility across both button forms and NLP chat parser outputs.

### 2. Optimistic Preloading for Admin Dashboard Teachers View (`GET /api/users`)
- **Decision**:
  1. Extended `Bundle` in `backend/app/services/preload.py` to preload the `User` accounts list (`users_blob`) in server RAM alongside timetable versions. Added `User` table change detection to `_probe(db)` (`func.count(User.id)`, `func.max(User.id)`, `func.max(User.last_login)`).
  2. Added shadow route support for `users_path="/api/users"` in `install_preload` (mounted in `backend/app/main.py`), guarded by `require_admin`. When any email is typed on `LoginPage.tsx`, the debounced `POST /api/preload/warm` request wakes the instance, warms the DB pool and crypto stack, and preloads the user table into RAM.
  3. Integrated `invalidate_preload("user_mutation")` calls into all user mutation endpoints in `backend/app/api/users.py` (`create_user`, `update_user`, `enable_user`, `disable_user`, `reset_password`).
  4. Enhanced `frontend/src/pages/AdminPanel.tsx` with instant SWR/sessionStorage caching to eliminate blank spinner flickers when navigating to the Teachers tab.
- **Reasoning**: Cold starts on Render free instances and Postgres connection setups previously delayed the admin dashboard. Warming when typing the email pre-computes the users JSON in RAM before the admin even clicks login. Sensitive data remains secure because unauthenticated warm requests never return user data; only authenticated requests with an Admin JWT can access the preloaded shadow route.


### 3. Type-Ahead Suggestions in the Calendar Override Modal
- **Decision**: Added a reusable `frontend/src/components/common/SuggestInput.tsx` combobox and used it for every text field in the calendar override modal's ADD and MODIFY forms (Program, Semester, Subject Code, Subject Name, Teacher, Room). Suggestions come from the existing catalog store (`useWorkspaceStore.loadCatalog`) and the rooms/teachers already seen that day. Subjects are filtered by the chosen program and semester. Picking a subject code fills in the subject name (and the type, program and semester when the catalog has them), and picking a name fills in the code. The list is rendered through a React portal with fixed positioning, and it flips upward when there isn't room below.
- **Reasoning**: Plain inputs made admins remember exact short names and codes, which led to typos the backend then rejected. A free-text combobox (rather than a strict `<select>`) still lets admins type values that aren't in the catalog. The portal was needed because `.modal` has `overflow-y: auto`, which would clip an absolutely positioned dropdown, especially for the Teacher/Room fields in the bottom row.

### 4. Free/Busy Markers for Teacher and Room Dropdowns
- **Decision**: Added a pure `computeAvailability()` in `CalendarManager.tsx`. It is memoised on the time window (start/end), the effective day schedule (`dayData.entries`: base timetable plus overrides, with cancelled classes already removed) and the teachers' busy-slot declarations. Busy slots are fetched once from `GET /busy-slots` when the modal opens. The result is a map from each busy teacher/room to the reason (e.g. `B.Tech 3rd · CN 12:00–14:00` or `marked busy (Leave)`). The Teacher and Room dropdowns show green/red dots with free/busy counts, sort free options first, and flag teachers whose catalog subjects include the selected subject ("teaches AA"). For MODIFY, the class being edited is left out of the check, and blank new times fall back to the class's original times.
- **Reasoning**: The answer is computed client-side from data the modal already has, so when the admin opens the dropdown it is ready immediately, with no request and no spinner. Busy options can still be selected because the backend stays the authority (hard constraints plus the existing "Force" checkbox). The markers are guidance, not a second validation layer that could disagree with the server.
