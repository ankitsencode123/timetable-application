# System Flow & Execution Architecture

This document describes how code flows across files, functions, and components in the timetable application.

---

## 1. Application Entry Points

```mermaid
flowchart TD
    subgraph Frontend["Frontend (React + Vite)"]
        F_Entry["main.tsx"] --> F_App["App.tsx"]
        F_App --> F_Pub["PublicPage.tsx (/)"]
        F_App --> F_Admin["AdminPanel.tsx (/admin/*)"]
        F_App --> F_Teach["TeacherWorkspace.tsx (/teacher/*)"]
    end

    subgraph Backend["Backend (FastAPI)"]
        B_Main["main.py (lifespan)"] --> B_Router["api/router.py"]
        B_Router --> B_Chat["api/chat.py"]
        B_Router --> B_Cal["calendar_system.py"]
        B_Router --> B_Cat["api/catalog.py"]
        B_Router --> B_Act["actions/engine.py"]
    end
```

---

## 2. Order of Execution & Function Call Sequences

### Flow A: Public AI Chat & Date Query Execution

When a student asks a timetable question on the public page (e.g., *"hey AI what classes do i have on this coming wednesay?"*):

```mermaid
sequenceDiagram
    autonumber
    actor User as Student
    participant UI as PublicPage.tsx
    participant API as api.ts (publicChat)
    participant Route as api/chat.py
    participant Svc as chat_service.py
    participant CChat as calendar_chat.py
    participant CalSys as calendar_system.py
    participant DB as SQLite / PostgreSQL

    User->>UI: Types query and clicks Send
    UI->>API: publicChat(text)
    API->>Route: POST /api/public/chat {"message": text}
    Route->>Svc: handle_public_chat(message, db)
    Svc->>CChat: try_handle_calendar_question(message, db)
    CChat->>CChat: answer_calendar_question(db, message, user=None)
    
    rect rgb(240, 245, 255)
        note over CChat: 1. Normalization & Date Scanning
        CChat->>CChat: _norm(message)
        CChat->>CChat: _scan_dates(normalized_text)
        CChat->>CChat: _h_weekday(match) -> kind='weekday', modifier='coming'
        CChat->>CChat: _resolve_spec() -> _upcoming_weekday(today, wd=2, modifier='coming')
    end

    rect rgb(245, 255, 240)
        note over CChat: 2. Intent & Student Scope Check
        CChat->>CChat: _detect_intents() -> ['schedule']
        CChat->>CChat: Check first_person pronoun ("i") + unauthenticated user
        CChat->>CChat: Treat as student: do not reject; add batch filter note
    end

    CChat->>CalSys: _load_ctx(db, target_date, target_date)
    CalSys->>DB: Query CalendarDayOverride & ValidityWindow
    DB-->>CalSys: Active timetable version & overrides
    CalSys->>CalSys: _resolve_day() -> compute effective entries
    CalSys-->>CChat: Effective day schedule
    CChat->>CChat: _section_schedule(views, spans, filters)
    CChat-->>Svc: {"message": formatted_markdown, "response_data": {...}}
    Svc-->>Route: ChatResponse
    Route-->>API: HTTP 200 OK JSON
    API-->>UI: Render message with ReactMarkdown
```

---

### Flow B: HTTP Caching for Public Calendar Endpoints

```mermaid
sequenceDiagram
    autonumber
    actor Browser as Browser / CDN
    participant Router as calendar_system.py (public_router)
    participant Core as calendar_system.py (_resolve_day)
    participant DB as Database

    Browser->>Router: GET /api/calendar/day/2026-10-21
    Router->>Router: _range_check(on_date, on_date)
    Router->>Core: _resolve_day(_load_ctx(db), on_date)
    Core->>DB: Fetch base version + overrides
    DB-->>Core: Records
    Core-->>Router: Effective day schedule
    Router->>Router: _cache(response) sets Cache-Control: public, max-age=15
    Router-->>Browser: HTTP 200 OK + Header: Cache-Control: public, max-age=15
```

---

### Flow C: Catalog Subject Management Flow

```mermaid
sequenceDiagram
    autonumber
    actor Admin as Admin User
    participant CM as CatalogManager.tsx (SubjectsTab)
    participant API as api.ts
    participant CatRoute as api/catalog.py (create_subject)
    participant C as scheduler/constraints.py
    participant DB as Database

    Admin->>CM: Fills Code, Name, Program, Semester, Type (No weekly hours)
    Admin->>CM: Clicks "+ Add Subject"
    CM->>API: req('/catalog/subjects', method='POST', body={...form, weekly_hours: 2})
    API->>CatRoute: POST /api/catalog/subjects
    CatRoute->>DB: Check if code exists in program
    CatRoute->>DB: Insert Subject(..., weekly_hours=2)
    DB-->>CatRoute: Created Subject
    CatRoute->>C: reload_catalog(db) -> Refresh in-memory constraints
    CatRoute-->>API: HTTP 201 Created (SubjectOut)
    API-->>CM: Subject added to table list (without weekly hours)
```

---

### Flow D: Reschedule Conflict Resolution & Suggestion UI Flow

```mermaid
sequenceDiagram
    autonumber
    actor User as Teacher / Admin
    participant Modal as RescheduleModal.tsx
    participant ActAPI as api.ts (executeActions)
    participant Engine as actions/engine.py
    participant Sugg as actions/suggestions.py
    participant DB as Database

    User->>Modal: Selects day and time -> clicks "Confirm Reschedule"
    Modal->>ActAPI: executeActions([MOVE_CLASS action])
    ActAPI->>Engine: POST /api/actions/execute
    Engine->>Engine: validate_schedule(proposed_schedule)
    Engine->>Sugg: Conflict detected (e.g. room double-booked) -> suggest_alternatives()
    Sugg->>Sugg: Generate conflict-free options & room changes
    Sugg-->>Engine: {"rich_suggestions": [...], "error": "Room double-booked"}
    Engine-->>ActAPI: HTTP 200 {success: false, results: [{error, suggestions}]}
    ActAPI-->>Modal: Update state (error + sorted suggestions)
    
    rect rgb(255, 245, 245)
        note over Modal: Render suggestions without overflow
        Modal->>Modal: formatSuggestion(rs) -> headline, tags, description
        Modal->>Modal: Flex container with minWidth: 0, text wrap, flexShrink: 0 button
    end
    
    User->>Modal: Clicks "Apply" on suggestion
    Modal->>ActAPI: executeActions([suggestion.action])
    ActAPI->>Engine: Apply conflict-free action
    Engine->>DB: Commit timetable version
    Engine-->>ActAPI: HTTP 200 {success: true}
    ActAPI-->>Modal: onSuccess() -> close modal & refresh grid
```

---

## 3. Control Flow Across Files and Modules

| Layer | Files | Responsibilities |
|---|---|---|
| **Frontend UI** | `CatalogManager.tsx` | Manages subjects, teachers, programs without weekly hours clutter. |
| | `RescheduleModal.tsx` | Slot picker, error banner, structured suggestion pills, overflow-proof cards. |
| | `PublicPage.tsx` | Public timetable viewer, responsive chat drawer, calendar drawer. |
| **API Client** | `frontend/src/api.ts` | Dispatches requests to `/api/catalog/*`, `/api/actions/*`, `/api/public/chat`. |
| **Backend Routing** | `backend/app/api/chat.py` | Handles `/api/public/chat` and `/api/teacher/chat` requests. |
| | `backend/app/calendar_system.py` | Handles `/api/calendar/*` endpoints, applies 15-second `Cache-Control`. |
| | `backend/app/api/catalog.py` | CRUD operations for subjects, teachers, programs. |
| **Core Engines** | `backend/app/calendar_chat.py` | NLP parser, date typo resolution, intent detection, student & teacher filtering. |
| | `backend/app/services/chat_service.py` | High-level dispatcher between calendar chat hook and LLM fallback. |
| | `backend/app/actions/engine.py` | Action validator, conflict detection, transaction rollback. |
| | `backend/app/actions/suggestions.py` | Conflict-free alternative generator and ranker. |
