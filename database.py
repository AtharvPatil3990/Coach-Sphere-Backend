import os

import psycopg2
from fastapi import HTTPException
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv


load_dotenv()

DB_URL = os.getenv("SUPABASE_DB_URL")

if not DB_URL:
    raise ValueError("SUPABASE_DB_URL is not configured")


def get_connection():
    return psycopg2.connect(
        DB_URL,
        cursor_factory=RealDictCursor,
    )


def initialize_action_store() -> None:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS public.branches (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    institute_id UUID NOT NULL
                        REFERENCES public.institutes(id) ON DELETE CASCADE,
                    name VARCHAR NOT NULL,
                    address TEXT,
                    phone VARCHAR,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (institute_id, name)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS staged_actions (
                    id TEXT PRIMARY KEY,
                    action_type TEXT NOT NULL,
                    created_by_user_id TEXT,
                    created_by_role TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    payload JSONB NOT NULL,
                    sql_query TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'AWAITING_HUMAN_APPROVAL',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    expires_at TIMESTAMPTZ NOT NULL
                        DEFAULT NOW() + INTERVAL '30 minutes',
                    approved_at TIMESTAMPTZ,
                    approved_by_user_id TEXT,
                    sent_at TIMESTAMPTZ,
                    failure_reason TEXT
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS public.class_schedules (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    institute_id UUID NOT NULL
                        REFERENCES public.institutes(id) ON DELETE CASCADE,
                    branch_id UUID REFERENCES public.branches(id),
                    batch_id UUID NOT NULL
                        REFERENCES public.batches(id) ON DELETE CASCADE,
                    trainer_id UUID REFERENCES public.trainers(id),
                    day_of_week INTEGER NOT NULL CHECK (day_of_week BETWEEN 0 AND 6),
                    start_time TIME NOT NULL,
                    end_time TIME NOT NULL,
                    mode attendance_mode NOT NULL,
                    room_number VARCHAR,
                    meeting_url TEXT,
                    effective_from DATE NOT NULL,
                    effective_until DATE,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    CHECK (end_time > start_time),
                    CHECK (
                        effective_until IS NULL
                        OR effective_until >= effective_from
                    )
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS public.class_sessions (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    institute_id UUID NOT NULL
                        REFERENCES public.institutes(id) ON DELETE CASCADE,
                    schedule_id UUID NOT NULL
                        REFERENCES public.class_schedules(id) ON DELETE CASCADE,
                    batch_id UUID NOT NULL
                        REFERENCES public.batches(id) ON DELETE CASCADE,
                    original_trainer_id UUID REFERENCES public.trainers(id),
                    trainer_id UUID REFERENCES public.trainers(id),
                    session_date DATE NOT NULL,
                    start_time TIME NOT NULL,
                    end_time TIME NOT NULL,
                    mode attendance_mode NOT NULL,
                    room_number VARCHAR,
                    meeting_url TEXT,
                    status VARCHAR NOT NULL DEFAULT 'SCHEDULED'
                        CHECK (status IN (
                            'SCHEDULED', 'IN_PROGRESS', 'COMPLETED',
                            'CANCELLED', 'RESCHEDULED'
                        )),
                    substitution_reason TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (schedule_id, session_date, start_time),
                    CHECK (end_time > start_time)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS public.trainer_absences (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    institute_id UUID NOT NULL
                        REFERENCES public.institutes(id) ON DELETE CASCADE,
                    branch_id UUID NOT NULL
                        REFERENCES public.branches(id) ON DELETE CASCADE,
                    trainer_id UUID NOT NULL
                        REFERENCES public.trainers(id) ON DELETE CASCADE,
                    substitute_trainer_id UUID
                        REFERENCES public.trainers(id),
                    absent_from DATE NOT NULL,
                    absent_until DATE NOT NULL,
                    reason TEXT NOT NULL,
                    created_by_user_id UUID NOT NULL
                        REFERENCES auth.users(id),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    CHECK (absent_until >= absent_from),
                    CHECK (
                        substitute_trainer_id IS NULL
                        OR substitute_trainer_id <> trainer_id
                    )
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS public.institute_holidays (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    institute_id UUID NOT NULL
                        REFERENCES public.institutes(id) ON DELETE CASCADE,
                    branch_id UUID REFERENCES public.branches(id) ON DELETE CASCADE,
                    holiday_date DATE NOT NULL,
                    name VARCHAR NOT NULL,
                    created_by_user_id UUID NOT NULL
                        REFERENCES auth.users(id),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (institute_id, branch_id, holiday_date)
                )
                """
            )
            cursor.execute(
                """
                ALTER TABLE public.attendance
                ADD COLUMN IF NOT EXISTS class_session_id UUID
                    REFERENCES public.class_sessions(id)
                """
            )
            cursor.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS attendance_student_session_unique
                ON public.attendance (student_id, class_session_id)
                WHERE class_session_id IS NOT NULL
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS class_sessions_timetable_idx
                ON public.class_sessions (institute_id, session_date, trainer_id, batch_id)
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS class_schedules_branch_weekday_idx
                ON public.class_schedules (institute_id, branch_id, day_of_week)
                """
            )
            cursor.execute(
                """
                ALTER TABLE public.class_schedules
                ALTER COLUMN branch_id SET NOT NULL
                """
            )


def execute_readonly_sql(query: str) -> list[dict]:
    clean_query = query.strip()
    upper_query = clean_query.upper()

    if not (
        upper_query.startswith("SELECT")
        or upper_query.startswith("WITH")
    ):
        raise HTTPException(
            status_code=403,
            detail="Only read-only SELECT queries are permitted.",
        )

    forbidden_keywords = (
        "INSERT ",
        "UPDATE ",
        "DELETE ",
        "DROP ",
        "ALTER ",
        "TRUNCATE ",
        "GRANT ",
        "REVOKE ",
        "CREATE ",
        "MERGE ",
    )

    if any(keyword in upper_query for keyword in forbidden_keywords):
        raise HTTPException(
            status_code=403,
            detail="Destructive SQL keywords detected.",
        )

    try:
        with get_connection() as connection:
            connection.set_session(
                readonly=True,
                autocommit=True,
            )

            with connection.cursor() as cursor:
                cursor.execute(clean_query)
                return [dict(row) for row in cursor.fetchall()]

    except psycopg2.Error as error:
        detail = error.pgerror or str(error)
        raise HTTPException(
            status_code=400,
            detail=f"Database execution error: {detail}",
        ) from error