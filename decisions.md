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

### 5. Transactional Email Notifications for Calendar Events (`ADD`, `CANCEL`, `MODIFY`, `DAY_OFF`, `DELETE`)
- **Decision**:
  1. **Notification Publishing Integration**: Wired `backend/app/calendar_system.py` directly to `backend/app/services/notification_service.py`. Added `publish_calendar_override(...)` to `create_override` (triggered after commit for `ADD`, `CANCEL`, `MODIFY`, and `DAY_OFF`), `publish_calendar_override_deleted(...)` to `delete_override` (triggered after commit when removing an override and restoring base schedule), and `publish_calendar_validity(...)` / `publish_calendar_validity_deleted(...)` to validity window services.
  2. **Faculty Directory Resolution**: Enhanced `_load_directory` in `notification_service.py` to index not only `Teacher.short_name` and `Teacher.full_name`, but also `User.email`, `User.email.split('@')[0]`, and active user fallback records. This guarantees that timetable tokens (e.g. short initials like `"SK"`, full names like `"Dr. Sunirmal Khatua"`, or email handles) reliably resolve to recipient accounts.
  3. **Event Expansion & Template Rendering**:
     - Built `_expand_calendar_event` to extract affected classes per faculty member and render responsive HTML/text emails with status badges, date/day headers, class metadata (time, room, subject, batch), diff logs for rescheduled/reassigned slots, administrative reasons, and actor attribution.
     - Covered all calendar mutation scenarios: `ADD` (class scheduled), `CANCEL` (class cancelled), `MODIFY` (time/room/teacher changes with old vs new teacher notifications), `DAY_OFF` (classes cancelled due to declared holiday), and `DELETE` (revoking cancellations, reverting modifications, or removing added classes).
     - Generated admin summary notifications for `Cfg.ADMIN_RECIPIENTS` detailing all affected entries, notified teachers, and any unresolved faculty tokens.
  4. **Durable Asynchronous Pipeline**: All calendar notifications publish asynchronously via persistent outbox table `notification_events` with deduplication keys, background queue claiming, Brevo API dispatching, circuit breakers, and staging safe redirection via `Cfg.REDIRECT_ALL_TO`.
- **Reasoning**: Previously, calendar overrides committed to the database and logged to the audit table, but never integrated with the notification engine (which only received events from the timetable version action engine). Faculty were not alerted when classes were added, moved, cancelled, or declared off on specific calendar dates. Integrating calendar mutations with the durable outbox ensures immediate, reliable, and auditable email delivery to both affected teachers and administrators.

### 6. Comprehensive Availability Checking in Calendar System (Busy Slots & Theory Limits)
- **Decision**: Overhauled `computeAvailability()` in `frontend/src/components/calendar/CalendarManager.tsx` to evaluate day-level constraints in addition to time-slot overlaps:
  1. **Day-Level Leave & Unavailable Slots**: Checks `busySlots` declarations (both temporary specific dates and recurring weekdays). If a teacher is on leave or unavailable, they are immediately marked as busy on that date with their reason (e.g., `marked busy on this day (Conference)`), even before the admin enters a time slot.
  2. **Hard Constraint H5 (Max 1 Theory Class per Day)**: If the class being added or modified is `Theory`, `computeAvailability()` counts all existing Theory classes taught by each teacher on that date (excluding the class being modified). Any teacher who already has a Theory class is marked busy with `max 1 theory/day reached (already teaching {subject} at {time})`.
  3. **Hard Constraint H5b (Max 1 Practical Class per Day)**: If the class is `Practical`, any teacher who already has a Practical class on that date is marked busy with `max 1 practical/day reached`.
  4. **Time-Slot Overlap Clashes (H2 & H3)**: When a valid `start` and `end` time are entered, it checks for direct class overlaps, tagging busy teachers with `time clash with {class}` and busy rooms with `room occupied by {class}`.
  5. **Pre-Time Guidance**: Even when `start`/`end` times are not yet chosen, day-level constraints are displayed immediately, preventing admins from picking unavailable teachers only to have the backend reject the change upon submission.
- **Reasoning**: Previously, `computeAvailability()` only checked for overlapping time windows. It completely ignored whether a teacher was on leave or had already reached the 1-theory-class-per-day limit. This caused the dropdown to show teachers with a green "free" status even when scheduling another Theory class would trigger a backend hard constraint error (`H5_multiple_theory_same_day`). Evaluating full day constraints upfront guarantees that the UI indicators match the backend validator rules.

### 7. Suggestion Dropdowns for Subject Name and Subject Code in Scheduling Modals
- **Decision**: Replaced plain HTML `<input>` fields for Subject Name and Subject Code in `frontend/src/components/actions/WeeklyScheduleTab.tsx` ("Weekly Routine Planner") and `frontend/src/components/actions/SmartScheduleModal.tsx` ("Single Class Slot") with `SuggestInput`:
  1. Connected both modals to `catalogSubjects` from `useWorkspaceStore`, ensuring catalog data is fetched on demand.
  2. Options are dynamically filtered by the selected `program` and `semester`, falling back to all catalog subjects if none are selected.
  3. Bidirectional Auto-Population:
     - Selecting a Subject Name automatically fills the corresponding Subject Code, and auto-derives the Class Type (`Theory` vs `Practical`) along with `program` and `semester` if not yet specified.
     - Selecting a Subject Code automatically fills the Subject Name, Class Type, and program/semester.
  4. Enhanced `onPick` in `frontend/src/components/calendar/CalendarManager.tsx` so selecting Subject Name similarly auto-derives Subject Code, Class Type, Program, and Semester.
- **Reasoning**: Plain inputs forced users to memorize and manually type exact subject titles and codes, increasing the risk of typographical errors that caused scheduler validation failures. Providing type-ahead comboboxes with automatic derivation streamlines form completion and ensures exact alignment with catalog subjects.

## 2026-10-28

### 8. Mobile Viewport Horizontal Overflow & Input Focus Shift Fix
- **Decision**: Resolved mobile UI breakage where typing into the search box caused the screen to scroll horizontally off-screen and lock up:
  1. **Root Viewport Clamping**: Enforced `width: 100%`, `max-width: 100vw`, and `overflow-x: hidden` across `html`, `body`, `#root`, and the `PublicPage` main container.
  2. **Mobile Font Size (Auto-Zoom Prevention)**: Increased search input font-size to `16px` on mobile screens (`max-width: 768px`). In mobile WebKit/Blink (iOS Safari and Android Chrome), focusing inputs with font sizes below 16px triggers an automatic zoom and horizontal viewport pan toward the caret, which displaced the layout.
  3. **Adaptive 2-Column Mobile Filter Grid**: Changed `.filter-controls-group` from a rigid 3-column grid (`repeat(3, 1fr)`) to an adaptive 2-column grid (`repeat(2, 1fr)`) for phone viewports (`< 480px`), and 3 columns on tablets/landscape (`480px - 768px`). In 3 columns on narrow screens (~360px), native `<select>` dropdown text ("All Programs", "All Semesters") forced intrinsic minimum widths exceeding the viewport, expanding container `scrollWidth`.
  4. **Active Horizontal Scroll Clamping**: Attached a ref to the `PublicPage` container with scroll event listeners on both the element and `window` to lock `scrollLeft` strictly to `0`, preventing any accidental programmatic or touch-driven horizontal offset.
- **Reasoning**: When users searched (e.g. typing "M.Tech"), the combination of input sub-16px focus zoom and overflowing filter dropdowns caused mobile browsers to scroll the ancestor container horizontally. Because `PublicPage` used `overflow: hidden`, the user was unable to scroll back, leaving the left side of the UI permanently clipped. Constraining dimensions, adjusting input font size, and fixing the filter grid guarantees a rock-solid, non-scrolling mobile viewport.

### 9. Streamlined Dropdown-Only Filter System (Removed Text Search & Teacher Inputs)
- **Decision**: Converted `FilterBar` into a clean, dropdown-driven filter interface, removing freeform text inputs:
  1. **Removed Search & Room Text Fields**: Deleted the search input and room input fields from `FilterBar`. Freeform text typing on mobile triggers virtual keyboards and viewport shifts.
  2. **Teacher Dropdown**: Replaced the `<input list="filter-teachers">` text element with a native `<select className="filter-select">` dropdown. Populated with short code and full faculty name (e.g. `SK (Sunirmal Khatua)`, `SC (Sankhayan Choudhury)`) derived from `catalogTeachers` and institutional defaults, guaranteeing immediate, conflict-free selection.
  3. **Auto Catalog Loading**: Ensured `loadCatalog()` triggers in `FilterBar` if catalog programs or teachers are uninitialized.
  4. **Clean Mobile Grid Layout**: Arranged the 4 dropdowns (Program, Semester, Teacher, Day) into a clean 2x2 grid on mobile viewports (`repeat(2, 1fr)`), with the Reset button spanning across columns when filters are active.
- **Reasoning**: Freeform search and datalist inputs caused mobile keyboard popping, caret panning, and typing inaccuracies. Replacing them with authoritative dropdowns provides a faster, zero-friction, touch-friendly interface that prevents any horizontal overflow or browser layout shifts.

### 10. AI Chat Analytical Query Routing & Rule H5/H6 Verification
- **Decision**: Fixed the chatbot so questions analyzing constraints, teacher workloads, and rules (e.g., `"Can you check if is any teacher who takes more than One theory class in a single day?"`) receive a reasoned, analytical answer instead of dumping today's classes:
  1. **Hook Interception Bypass (`calendar_chat.py`)**: Added `_ANALYTICAL_RE` matching queries containing analytical/constraint terms (`check if`, `verify`, `more than`, `less than`, `theory class`, `free day`, `violation`, `conflict`, `who teaches`, etc.) and date-less schedule queries (`not specs and intents == ["schedule"]`) in `answer_calendar_question` when `force=False`. In these cases, the calendar chat hook immediately returns `None` instead of assuming `today` and dumping all classes.
  2. **Enhanced Analytical System Prompt (`chat_service.py`)**: Expanded the LLM system prompt in `handle_public_chat` with domain definitions for Theory vs Practical classes (practical classes marked `-P` or `:P`), Rule H5 (max 1 theory class/day per teacher across all programmes), and Rule H6 (free day for internal teachers).
  3. **Deterministic Factual Fallback (`chat_service.py`)**: Implemented `_check_teachers_more_than_one_theory()` and `_check_internal_teacher_free_days()` to parse published timetable rows and return direct, accurate counts of theory classes per teacher per day and internal teacher free days even when external LLM APIs are unreachable.
- **Reasoning**: `calendar_chat.py` was originally designed as a deterministic day/date schedule lookup ("classes on Oct 4", "holidays next week"). When a user asked "Can you check if is any teacher who takes more than One theory class in a single day?", the keyword "class" caused `_detect_intents` to classify it as a "schedule" intent. Because no date was provided, it defaulted to `Span(today, today)` (Wednesday, 28 October 2026) and dumped 17 classes for Wednesday. Bypassing calendar chat for analytical queries allows the system to directly evaluate the timetable constraints and give the user an accurate answer.

### 11. Mobile Week Day Pills Horizontal Scrolling & Auto-Centering
- **Decision**: Fixed the day pills strip in `PublicPage.tsx` so Friday and Saturday are fully accessible and interactive on mobile devices:
  1. **Touch-Action Pan-X**: Added `touch-action: pan-x;` to `.date-nav-pills-scroller` and `.date-pill-btn` in `frontend/src/index.css`. In mobile WebKit/Blink browsers, touches on `<button>` elements swallow horizontal swipe gestures unless `touch-action: pan-x` is explicitly enabled.
  2. **Active Pill Auto-Centering**: Attached `pillsScrollerRef` to `.date-nav-pills-scroller` and added a `useEffect` that calls `activeEl.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' })` whenever `selectedDate` changes.
  3. **Chevron Arrow Button Scrolling**: Updated `prevDay` and `nextDay` handlers so clicking `<` or `>` both increments the day and smoothly scrolls the pills container (`scrollBy({ left: ±100, behavior: 'smooth' })`).
  4. **Visual Scroll Affordance**: Added a subtle 3px custom scrollbar (`scrollbar-width: thin; scrollbar-color: ...`) on the scroller so users on mobile have immediate visual indication that additional days extend beyond Thursday.
- **Reasoning**: With 7 days in a week (~60px per pill + gaps = ~440px), narrow phone screens (360px–390px) can only fit 3.5 to 4 pills at a time. The hidden scrollbar, lack of `touch-action: pan-x`, and static arrow buttons prevented users from reaching Friday and Saturday. Combining touch-action, chevron container scrolling, and active-pill centering ensures fluid, intuitive day navigation on all screen sizes.

### 12. Strict Hard Constraint Enforcement Across All Timetable Mutation Flows (2026-10-31)
- **Decision**: Eliminated all mechanisms that allowed hard constraint violations (specifically H1 semester clashes, H2 teacher clashes, H3 room clashes, and H12 busy slot conflicts) to be suggested, saved, or published:
  1. **Abolished Blind Emergency Reschedule Fallbacks (`backend/app/actions/suggestions.py`)**:
     - Removed the blind fallback block that forced the first available time slot (which selected Wednesday 14:00–17:00 in Room 303 for teacher PB) and falsely labeled it `"status": "Conflict-free and validated"`.
     - Hardened `_is_physically_valid` and `_is_fully_valid` so any candidate containing an `H1_semester_clash`, `H2_teacher_clash`, `H3_room_clash`, or `H12_teacher_busy` is strictly rejected and never offered as a valid suggestion.
  2. **Strict Hard Constraint Pre-Commit Gating in `ActionEngine` (`backend/app/actions/engine.py`)**:
     - Previously, `ActionEngine` only computed differential errors (`_get_new_errors()`) against the base schedule. If a prior draft contained an overlap, subsequent operations ignored the violation as "not new".
     - Implemented an absolute hard constraint gate: if any candidate schedule at any intermediate action step or at the final execution pass contains any hard constraint violation (`H1_semester_clash`, `H2_teacher_clash`, `H3_room_clash`, `H12_teacher_busy`, time format errors), execution immediately halts with `success=False` and the draft is NOT saved.
     - In `AddTeacherBusyAction`, candidate alternative slots are simulated against hard constraints. If no conflict-free slot exists, the affected class is safely cancelled instead of forced into an overlapping slot.
  3. **Strict Validation in Teacher Busy Rescheduling (`backend/app/api/busy_slots.py`)**:
     - Validated candidate rich suggestions against `HARD_RULES` prior to selecting a replacement slot.
     - Added a final validation check before `create_draft`. If any hard constraint would be introduced, the auto-reschedule raises an `HTTPException` and cancels rather than corrupting the timetable.
  4. **Publication Hard Constraint Guardrail (`backend/app/services/publication_service.py`)**:
     - `publish_version` now performs full `validate_schedule` and `validate_schema` checks on all entries of the target version before updating its status to `PUBLISHED`.
     - Any version with an active hard constraint clash is strictly barred from publication, raising a `ValueError`.
  5. **Program & Field Normalization in Draft Creation (`backend/app/services/timetable_service.py`)**:
     - Enforced `normalize_program()` and cleaned string handling on `program`, `semester`, `start_time`, `end_time`, and `entry_type` when creating timetable entries to prevent casing and punctuation discrepancies (`M.Tech.` vs `M.Tech`).
  6. **Database Timetable Remediation**:
     - Investigated remote Supabase database lineage: Version 46 introduced the clashing `aa` class on Wednesday 14:00–17:00 when teacher PB was marked busy on Monday via the faulty emergency fallback. Version 47 was published with that H1 clash between `PE-1:SC-P` and `aa`. Later drafts ineffectively cancelled `PE-1:SC-P` and added unapproved subjects (`at`, `dvs`).
     - Generated clean Version 52: pruned the duplicate Wednesday `aa` class and restored `PE-1:SC-P` to Wednesday 14:30–17:30 in R#209 (free room for M.Tech 1st), resulting in 0 hard constraint violations across the entire schedule.
- **Reasoning**: Hard constraints represent physical and institutional impossibilities (students attending two classes simultaneously, teachers in two rooms at once, rooms double-booked). Allowing fallback routines to bypass validation or allowing differential checks to ignore existing hard errors created cascading data corruption. Enforcing absolute zero-tolerance gates at suggestion generation, action execution, draft creation, and publication guarantees the integrity of all schedules.


### 13. Elimination of HTTP 503 Errors on Suggestion Application & Version Transition (2026-10-31)
- **Decision**: Resolved intermittent HTTP 503 errors when clicking "Apply Suggestions" / "Apply These Suggestions":
  1. **Preload Bundle Fallback to Database (`backend/app/services/preload.py`)**:
     - The shadow routes for `versions_list_cached`, `version_detail_cached`, `draft_cached`, and `users_cached` previously called `_bundle_or_503()`, raising HTTP 503 if the in-RAM preloaded cache bundle was uninitialized or invalidated.
     - Immediately following action execution (`POST /api/actions/execute`), SQLAlchemy commit hooks trigger `BUNDLES.invalidate("db-commit")`. When client code immediately requested `getVersion(new_version_id)` or `getCurrentDraft()`, the shadow route threw HTTP 503, aborting the application process and presenting an error toast even though the database action had successfully committed.
     - Updated all shadow routes to seamlessly fall back to querying the database directly (`get_version_by_id`, `get_all_versions`, `get_current_draft`, etc.) whenever the memory bundle is unavailable, completely eliminating 503 errors during version transitions.
  2. **Safe PostgreSQL Advisory Lock Release on Pooled Connections (`backend/app/actions/concurrency.py` & root `concurrency.py`)**:
     - In `ScopeLock.hold()`, if unlocking via session advisory lock (`pg_advisory_unlock`) fails or returns false (as can occur on transaction poolers like PgBouncer on Supabase port 6543), `conn.invalidate()` is explicitly invoked to prevent connection-leaks from holding unreleased session advisory locks in the connection pool.
  3. **Client-Side Exponential Backoff Retry & Idempotent Headers (`frontend/src/api.ts`)**:
     - Added an automatic exponential backoff retry mechanism in `request()` for HTTP 503 (Lock Contention / Service Unavailable) and HTTP 429 (Rate Limit) responses, respecting `Retry-After` headers sent by the backend concurrency guard.
     - Added automatic `Idempotency-Key` header generation for `/actions/execute` mutations, guaranteeing that retried proposals can be safely re-evaluated without duplicating operations.
- **Reasoning**: The 503 errors occurred because in-RAM bundles were invalidated on database commits while the client immediately queried the shadow routes for the new version, coupled with transient lock contention from Postgres advisory locks without client retries. Providing direct DB fallbacks, connection pool invalidation, and client backoff retries ensures that suggestion application is 100% reliable and resilient.

### 14. Immediate Store Synchronization for Newly Added Teachers and Subjects in Weekly Planner (2026-10-31)
- **Decision**: Fixed the catalog synchronization lifecycle so newly created or edited teachers and subjects appear immediately in the Weekly Planner (`WeeklyScheduleTab`) and Scheduling Modals without requiring a browser page refresh:
  1. **Workspace Store Invalidation on Catalog Mutations (`frontend/src/components/catalog/CatalogManager.tsx` & `AdminPanel.tsx`)**:
     - Previously, `CatalogManager.tsx` only updated its local component state (`teachers`, `subjects`, `programs`) when teachers or subjects were created, edited, or deleted. It never updated `useWorkspaceStore`.
     - Added `useWorkspaceStore.getState().loadCatalog()` after teacher creation (`add()`), teacher subject updates (`saveEdit()`), subject creation (`add()`), subject deletion (`remove()`), program creation (`add()`), bundle application (`applyBundle()`), and user creation in `AdminPanel.tsx`.
  2. **Direct Workspace Store Derivation in `WeeklyScheduleTab.tsx` and `SmartScheduleModal.tsx`**:
     - Refactored `SmartScheduleModal.tsx` to derive `dynamicPrograms` and `dynamicTeachers` directly from `catalogPrograms` and `catalogTeachers` in `useWorkspaceStore` via `useMemo`, and ensured `loadCatalog()` is triggered on modal mount.
     - Refactored `WeeklyScheduleTab.tsx` to subscribe to `catalogTeachers`, `catalogPrograms`, `catalogSubjects`, and `loadCatalog` directly from `useWorkspaceStore`. Replaced static props and stale state with dynamic derivations `dynamicPrograms` and `dynamicTeachers`, and updated `useEffect` to always fetch fresh catalog entries on component mount.
- **Reasoning**: The Weekly Routine Planner previously relied on hardcoded static teacher lists and local state fetched only once during initial modal mounting, while catalog mutations only modified local state inside `CatalogManager`. Synchronizing mutations directly through the global workspace store and binding the dropdown options reactively ensures that any new teacher or subject created anywhere in the application is immediately visible throughout all scheduling modals.

### 15. Resolution of HTTP 409 Concurrency Version Mismatches on Weekly Planner Application (2026-10-31)
- **Decision**: Resolved the recurring "Could Not Complete HTTP 409" conflict error when applying weekly schedules:
  1. **Accurate Concurrency Error Parsing in `api.ts`**:
     - `ConcurrencyError` responses emitted by FastAPI contain `message`, `error`, `base_version_id`, and `current_version_id` at the top level of the JSON body rather than nested under `detail`.
     - In `frontend/src/api.ts`, changed detail extraction to `body.detail ?? body.message ?? body.error` and surfaced concurrency attributes on the thrown error object (`current_version_id`, `base_version_id`, `error_code`). This prevents opaque `"HTTP 409"` strings from replacing informative backend explanations (such as "The timetable changed since you loaded it. Reload and retry.").
  2. **Explicit Target Version Binding in Weekly Planner (`schedule_smart_weekly.py`)**:
     - Previously, `solve_weekly` and `weekly_smart_schedule` planned against head in the database without recording the `version_id` against which the candidate slots and actions were calculated, and did not accept a `version_id` parameter.
     - Added optional `version_id` to both `WeeklyScheduleRequest` and `WeeklyScheduleResponse`. In `weekly_smart_schedule`, resolved the target version (falling back to head) and set `resp.version_id = v.id`.
     - In `WeeklyScheduleTab.tsx`, passed `version_id: currentVersionId ?? undefined` in the search request, and when executing proposals sent `targetVersionId = result?.version_id ?? currentVersionId`. Because proposals are applied directly against the version they were verified on, operations commit cleanly without triggering stale auto-rebase rejections.
  3. **Automatic Workspace Head Synchronization on 409 Conflicts**:
     - In `WeeklyScheduleTab.tsx`, if a 409 concurrency conflict occurs during proposal application (e.g., another user commits a new draft concurrently), the application catches the 409, queries `getCurrentDraft()` and `getVersion(draft.id)`, and automatically updates `useWorkspaceStore` (`setCurrentVersion` and `setVersions`).
     - Displayed an informative message to the user explaining that the timetable has advanced, and provided a one-click `↻ Re-run Routine Search` button so the user can immediately re-calculate and apply conflict-free slots against the updated head routine.
  4. **Resilient Version Resolution in `TeacherWorkspace.tsx`**:
     - Updated `init()` in `TeacherWorkspace.tsx` so that if `getCurrentDraft()` fails, it automatically falls back to `listVersions()` and loads the newest existing version into `currentVersionId`, preventing clients from being left uninitialized or stranded on Version 1.
- **Reasoning**: The 409 error occurred because the Weekly Routine Planner computed proposals against the database head but submitted mutations pinned to `currentVersionId` (which was out-of-date or defaulted to Version 1). Concurrency guards in `SafeActionEngine` detected that the client's base version lagged head by more than `max_rebase_distance` (25 versions), raising `StaleVersionError` (HTTP 409). Binding weekly proposals explicitly to their evaluated version, resolving latest versions on teacher workspace mount, and adding automatic store re-synchronization on 409 eliminates this conflict loop.
