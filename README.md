# Timely
Timely is a comprehensive timetable management application featuring a powerful scheduling engine and an AI-driven chat assistant. It is designed to automatically handle conflicts, manage active timetables, and apply scheduling constraints seamlessly.

## Architecture

The application is built using a modern decoupled architecture:

- **Frontend**: A React application built with Vite, TypeScript, and Zustand for state management. It provides the user interface for administrators and teachers to view the interactive schedule, manage slots, and chat with the AI assistant.
- **Backend**: A FastAPI-based REST API that runs the timetable constraint engine, processes smart rescheduling events, and manages application data. 
- **Database**: PostgreSQL (with Alembic for migrations) as the underlying datastore for storing timetable drafts, schedules, teacher resources, and active constraint configurations.

## Key Features

- **Constraint Resolution Engine**: Automatically validates timetable changes against comprehensive constraints (e.g., overlapping classes, semester clashes, teacher free days).
- **Smart Rescheduling**: Re-allocates conflicting classes dynamically with validation to guarantee a conflict-free active routine.
- **AI Chat Assistant**: A context-aware interface integrated with the current timetable state that allows users to ask questions or process multi-intent schedule modifications.
- **Unified Administrative Access**: Simplified role architecture to provide teachers and administrators equal access to necessary management tools.

## Getting Started

### Prerequisites

Ensure you have the following installed:
- Node.js (for the Vite frontend)
- Python 3.9+ (for the FastAPI backend)
- PostgreSQL (or Supabase)

### Backend Setup

1. Navigate to the `backend` directory.
2. Initialize your virtual environment and install dependencies.
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
3. Set up your environment variables by copying `.env.example` to `.env` and adjusting the database URI and CORS settings.
4. Run the database migrations.
   ```bash
   alembic upgrade head
   ```
5. Start the backend server.
   ```bash
   uvicorn app.main:app --reload
   ```

### Frontend Setup

1. Navigate to the `frontend` directory.
2. Install the required Node packages.
   ```bash
   npm install
   ```
3. Start the Vite development server.
   ```bash
   npm run dev
   ```

## Deployment

Timely is optimized for split-stack deployment:
- **Frontend**: Hosted on platforms like Vercel.
- **Backend**: Hosted on Render or similar platforms.

Ensure cross-origin settings are aligned correctly between your backend server and the deployed frontend URL to support the application's robust local storage and CSRF configurations.
