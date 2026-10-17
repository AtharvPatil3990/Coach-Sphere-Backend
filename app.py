import os
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from auth import (
    CurrentUser,
    get_current_user,
    invite_member,
    sign_in,
    sign_up_admin,
)
from actions import (
    claim_action,
    mark_action_failed,
    mark_action_sent,
    reject_action,
)
from copilot_engine import CopilotEngine
from database import get_connection, initialize_action_store
from messaging import send_messages


load_dotenv()

engine: CopilotEngine | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global engine
    initialize_action_store()
    engine = CopilotEngine()
    yield


app = FastAPI(
    title="Institute Operations Copilot API",
    lifespan=lifespan,
)


cors_origins = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS",
        "http://localhost:3000,http://localhost:5173",
    ).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


class ChatRequest(BaseModel):
    message: str


class ActionDecisionRequest(BaseModel):
    pass


class AdminSignupRequest(BaseModel):
    email: str
    password: str
    full_name: str
    phone: str
    institute_name: str
    institute_address: str | None = None
    institute_phone: str | None = None
    institute_email: str | None = None


class MemberInvitationRequest(BaseModel):
    email: str
    full_name: str
    phone: str
    expertise: str | None = None


class SigninRequest(BaseModel):
    email: str
    password: str


class BranchCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    address: str | None = None
    phone: str | None = None


class CourseCreateRequest(BaseModel):
    course_code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    duration_weeks: int = Field(gt=0)
    base_fee: float = Field(ge=0)


class BatchCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    course_id: str
    trainer_id: str | None = None
    mode: str
    room_number: str | None = None
    meeting_url: str | None = None
    max_classroom_seats: int = Field(default=30, gt=0)
    max_online_seats: int = Field(default=100, gt=0)
    start_date: date
    end_date: date | None = None
    schedule_time: time
    status: str = "ACTIVE"


class ScheduleCreateRequest(BaseModel):
    branch_id: str
    batch_id: str
    trainer_id: str | None = None
    day_of_week: int = Field(ge=0, le=6)
    start_time: time
    end_time: time
    mode: str
    room_number: str | None = None
    meeting_url: str | None = None
    effective_from: date
    effective_until: date | None = None


class SessionGenerateRequest(BaseModel):
    from_date: date
    to_date: date
    branch_id: str | None = None


class SessionSubstitutionRequest(BaseModel):
    substitute_trainer_id: str
    reason: str = Field(min_length=1, max_length=500)


class TrainerAbsenceRequest(BaseModel):
    branch_id: str
    trainer_id: str
    substitute_trainer_id: str
    absent_from: date
    absent_until: date
    reason: str = Field(min_length=1, max_length=500)


class TimetableQuery(BaseModel):
    from_date: date
    to_date: date


class HolidayCreateRequest(BaseModel):
    branch_id: str | None = None
    holiday_date: date
    name: str = Field(min_length=1, max_length=150)


class SessionRescheduleRequest(BaseModel):
    session_date: date
    start_time: time
    end_time: time
    room_number: str | None = None
    meeting_url: str | None = None
    reason: str = Field(min_length=1, max_length=500)


def require_admin(user: CurrentUser) -> None:
    if user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="Only institute administrators can perform this operation.",
        )


@app.post("/api/auth/signup/admin")
async def signup_admin(request: AdminSignupRequest):
    return sign_up_admin(
        email=request.email,
        password=request.password,
        full_name=request.full_name,
        phone=request.phone,
        institute_name=request.institute_name,
        institute_address=request.institute_address,
        institute_phone=request.institute_phone,
        institute_email=request.institute_email,
    )


@app.post("/api/auth/signin")
async def signin(request: SigninRequest):
    return sign_in(request.email, request.password)


@app.post("/api/admin/branches")
async def create_branch(
    request: BranchCreateRequest,
    user: CurrentUser = Depends(get_current_user),
):
    require_admin(user)
    try:
        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO public.branches (
                        institute_id, name, address, phone
                    )
                    VALUES (%s::uuid, %s, %s, %s)
                    RETURNING id::text, institute_id::text, name,
                              address, phone, is_active, created_at
                    """,
                    (
                        user.institute_id,
                        request.name.strip(),
                        request.address,
                        request.phone,
                    ),
                )
                branch = cursor.fetchone()
        return {"branch": dict(branch)}
    except Exception as error:
        raise HTTPException(
            status_code=400,
            detail="Unable to create the branch. The name may already exist.",
        ) from error


@app.post("/api/admin/courses")
async def create_course(
    request: CourseCreateRequest,
    user: CurrentUser = Depends(get_current_user),
):
    require_admin(user)
    try:
        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO public.courses (
                        institute_id, course_code, name,
                        duration_weeks, base_fee
                    )
                    VALUES (%s::uuid, %s, %s, %s, %s)
                    RETURNING id::text, institute_id::text, course_code,
                              name, duration_weeks, base_fee,
                              created_at, updated_at
                    """,
                    (
                        user.institute_id,
                        request.course_code.strip(),
                        request.name.strip(),
                        request.duration_weeks,
                        request.base_fee,
                    ),
                )
                course = cursor.fetchone()
        return {"course": dict(course)}
    except Exception as error:
        raise HTTPException(
            status_code=400,
            detail="Unable to create the course. The code may already exist.",
        ) from error


@app.post("/api/admin/batches")
async def create_batch(
    request: BatchCreateRequest,
    user: CurrentUser = Depends(get_current_user),
):
    require_admin(user)
    if request.end_date and request.end_date < request.start_date:
        raise HTTPException(
            status_code=422,
            detail="end_date cannot be earlier than start_date.",
        )
    if request.status not in {"ACTIVE", "INACTIVE", "COMPLETED", "CANCELLED"}:
        raise HTTPException(status_code=422, detail="Invalid batch status.")
    if request.mode not in {"OFFLINE", "ONLINE", "HYBRID"}:
        raise HTTPException(status_code=422, detail="Invalid batch mode.")

    try:
        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id
                    FROM public.courses
                    WHERE id = %s::uuid
                      AND institute_id = %s::uuid
                    """,
                    (request.course_id, user.institute_id),
                )
                if cursor.fetchone() is None:
                    raise HTTPException(
                        status_code=404,
                        detail="Course was not found in your institute.",
                    )

                if request.trainer_id:
                    cursor.execute(
                        """
                        SELECT id
                        FROM public.trainers
                        WHERE id = %s::uuid
                          AND institute_id = %s::uuid
                          AND is_active = TRUE
                        """,
                        (request.trainer_id, user.institute_id),
                    )
                    if cursor.fetchone() is None:
                        raise HTTPException(
                            status_code=404,
                            detail="Active trainer was not found in your institute.",
                        )

                cursor.execute(
                    """
                    INSERT INTO public.batches (
                        institute_id, name, course_id, trainer_id, mode,
                        room_number, meeting_url, max_classroom_seats,
                        max_online_seats, start_date, end_date,
                        schedule_time, status
                    )
                    VALUES (
                        %s::uuid, %s, %s::uuid, %s::uuid, %s::batch_mode,
                        %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    RETURNING id::text, institute_id::text, name,
                              course_id::text, trainer_id::text, mode::text,
                              room_number, meeting_url, max_classroom_seats,
                              max_online_seats, start_date, end_date,
                              schedule_time, status, created_at, updated_at
                    """,
                    (
                        user.institute_id,
                        request.name.strip(),
                        request.course_id,
                        request.trainer_id,
                        request.mode,
                        request.room_number,
                        request.meeting_url,
                        request.max_classroom_seats,
                        request.max_online_seats,
                        request.start_date,
                        request.end_date,
                        request.schedule_time,
                        request.status,
                    ),
                )
                batch = cursor.fetchone()
        return {"batch": dict(batch)}
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(
            status_code=400,
            detail="Unable to create the batch. Check the supplied values.",
        ) from error


@app.post("/api/admin/schedules")
async def create_schedule(
    request: ScheduleCreateRequest,
    user: CurrentUser = Depends(get_current_user),
):
    require_admin(user)
    if request.end_time <= request.start_time:
        raise HTTPException(status_code=422, detail="end_time must be later than start_time.")
    if request.effective_until and request.effective_until < request.effective_from:
        raise HTTPException(status_code=422, detail="effective_until cannot be earlier than effective_from.")
    if request.mode not in {"CLASSROOM", "ONLINE"}:
        raise HTTPException(status_code=422, detail="mode must be CLASSROOM or ONLINE.")
    if request.mode == "CLASSROOM" and not request.room_number:
        raise HTTPException(status_code=422, detail="room_number is required for classroom schedules.")
    if request.mode == "ONLINE" and not request.meeting_url:
        raise HTTPException(status_code=422, detail="meeting_url is required for online schedules.")

    try:
        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id FROM public.batches
                    WHERE id = %s::uuid AND institute_id = %s::uuid
                    """,
                    (request.batch_id, user.institute_id),
                )
                if cursor.fetchone() is None:
                    raise HTTPException(status_code=404, detail="Batch was not found in your institute.")

                if request.branch_id:
                    cursor.execute(
                        """
                        SELECT id FROM public.branches
                        WHERE id = %s::uuid AND institute_id = %s::uuid
                          AND is_active = TRUE
                        """,
                        (request.branch_id, user.institute_id),
                    )
                    if cursor.fetchone() is None:
                        raise HTTPException(status_code=404, detail="Active branch was not found in your institute.")

                if request.trainer_id:
                    cursor.execute(
                        """
                        SELECT id FROM public.trainers
                        WHERE id = %s::uuid AND institute_id = %s::uuid
                          AND is_active = TRUE
                        """,
                        (request.trainer_id, user.institute_id),
                    )
                    if cursor.fetchone() is None:
                        raise HTTPException(status_code=404, detail="Active trainer was not found in your institute.")

                cursor.execute(
                    """
                    SELECT 1
                    FROM public.class_schedules AS s
                    WHERE s.institute_id = %s::uuid
                      AND s.day_of_week = %s
                      AND s.is_active = TRUE
                      AND (%s IS NULL OR s.effective_from <= %s)
                      AND (s.effective_until IS NULL OR s.effective_until >= %s)
                      AND s.start_time < %s
                      AND s.end_time > %s
                      AND (
                          s.batch_id = %s::uuid
                          OR (%s::uuid IS NOT NULL AND s.trainer_id = %s::uuid)
                          OR (
                              %s = 'CLASSROOM'
                              AND s.branch_id = %s::uuid
                              AND s.room_number = %s
                          )
                      )
                    LIMIT 1
                    """,
                    (
                        user.institute_id,
                        request.day_of_week,
                        request.effective_until,
                        request.effective_until,
                        request.effective_from,
                        request.start_time,
                        request.end_time,
                        request.batch_id,
                        request.trainer_id,
                        request.trainer_id,
                        request.mode,
                        request.branch_id,
                        request.room_number,
                    ),
                )
                if cursor.fetchone() is not None:
                    raise HTTPException(
                        status_code=409,
                        detail="Schedule overlaps an existing batch, trainer, or classroom schedule.",
                    )

                cursor.execute(
                    """
                    INSERT INTO public.class_schedules (
                        institute_id, branch_id, batch_id, trainer_id,
                        day_of_week, start_time, end_time, mode,
                        room_number, meeting_url, effective_from, effective_until
                    )
                    VALUES (
                        %s::uuid, %s::uuid, %s::uuid, %s::uuid,
                        %s, %s, %s, %s::attendance_mode,
                        %s, %s, %s, %s
                    )
                    RETURNING id::text, institute_id::text, branch_id::text,
                              batch_id::text, trainer_id::text, day_of_week,
                              start_time, end_time, mode::text, room_number,
                              meeting_url, effective_from, effective_until,
                              is_active, created_at
                    """,
                    (
                        user.institute_id,
                        request.branch_id,
                        request.batch_id,
                        request.trainer_id,
                        request.day_of_week,
                        request.start_time,
                        request.end_time,
                        request.mode,
                        request.room_number,
                        request.meeting_url,
                        request.effective_from,
                        request.effective_until,
                    ),
                )
                schedule = cursor.fetchone()
        return {"schedule": dict(schedule)}
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status_code=400, detail="Unable to create the schedule.") from error


@app.post("/api/admin/trainer-absences")
async def record_trainer_absence(
    request: TrainerAbsenceRequest,
    user: CurrentUser = Depends(get_current_user),
):
    require_admin(user)
    if request.absent_until < request.absent_from:
        raise HTTPException(status_code=422, detail="absent_until cannot be earlier than absent_from.")
    if request.trainer_id == request.substitute_trainer_id:
        raise HTTPException(status_code=422, detail="The substitute must be a different trainer.")

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id
                FROM public.branches
                WHERE id = %s::uuid AND institute_id = %s::uuid AND is_active = TRUE
                """,
                (request.branch_id, user.institute_id),
            )
            if cursor.fetchone() is None:
                raise HTTPException(status_code=404, detail="Active branch was not found in your institute.")

            cursor.execute(
                """
                SELECT id
                FROM public.trainers
                WHERE id = %s::uuid AND institute_id = %s::uuid AND is_active = TRUE
                """,
                (request.trainer_id, user.institute_id),
            )
            if cursor.fetchone() is None:
                raise HTTPException(status_code=404, detail="Absent trainer was not found in your institute.")

            cursor.execute(
                """
                SELECT id
                FROM public.trainers
                WHERE id = %s::uuid AND institute_id = %s::uuid AND is_active = TRUE
                """,
                (request.substitute_trainer_id, user.institute_id),
            )
            if cursor.fetchone() is None:
                raise HTTPException(status_code=404, detail="Active substitute trainer was not found.")

            cursor.execute(
                """
                SELECT 1
                FROM public.trainer_absences
                WHERE institute_id = %s::uuid
                  AND branch_id = %s::uuid
                  AND trainer_id = %s::uuid
                  AND absent_from <= %s
                  AND absent_until >= %s
                LIMIT 1
                """,
                (
                    user.institute_id,
                    request.branch_id,
                    request.trainer_id,
                    request.absent_until,
                    request.absent_from,
                ),
            )
            if cursor.fetchone() is not None:
                raise HTTPException(status_code=409, detail="An absence already covers part of this date range.")

            cursor.execute(
                """
                SELECT 1
                FROM public.class_sessions AS cs
                JOIN public.class_schedules AS s ON s.id = cs.schedule_id
                WHERE cs.institute_id = %s::uuid
                  AND s.branch_id = %s::uuid
                  AND cs.trainer_id = %s::uuid
                  AND cs.session_date BETWEEN %s AND %s
                  AND cs.status NOT IN ('CANCELLED', 'COMPLETED')
                  AND EXISTS (
                      SELECT 1
                      FROM public.class_sessions AS occupied
                      WHERE occupied.institute_id = cs.institute_id
                        AND occupied.id <> cs.id
                        AND occupied.session_date = cs.session_date
                        AND occupied.trainer_id = %s::uuid
                        AND occupied.status NOT IN ('CANCELLED', 'COMPLETED')
                        AND occupied.start_time < cs.end_time
                        AND occupied.end_time > cs.start_time
                  )
                LIMIT 1
                """,
                (
                    user.institute_id,
                    request.branch_id,
                    request.trainer_id,
                    request.absent_from,
                    request.absent_until,
                    request.substitute_trainer_id,
                ),
            )
            if cursor.fetchone() is not None:
                raise HTTPException(
                    status_code=409,
                    detail="The substitute trainer has an overlapping class during the absence.",
                )

            cursor.execute(
                """
                INSERT INTO public.trainer_absences (
                    institute_id, branch_id, trainer_id, substitute_trainer_id,
                    absent_from, absent_until, reason, created_by_user_id
                )
                VALUES (%s::uuid, %s::uuid, %s::uuid, %s::uuid, %s, %s, %s, %s::uuid)
                RETURNING id::text, branch_id::text, trainer_id::text,
                          substitute_trainer_id::text, absent_from, absent_until, reason
                """,
                (
                    user.institute_id,
                    request.branch_id,
                    request.trainer_id,
                    request.substitute_trainer_id,
                    request.absent_from,
                    request.absent_until,
                    request.reason,
                    user.auth_user_id,
                ),
            )
            absence = cursor.fetchone()

            cursor.execute(
                """
                UPDATE public.class_sessions AS cs
                SET trainer_id = %s::uuid,
                    substitution_reason = %s,
                    updated_at = NOW()
                FROM public.class_schedules AS s
                WHERE cs.schedule_id = s.id
                  AND cs.institute_id = %s::uuid
                  AND s.branch_id = %s::uuid
                  AND cs.original_trainer_id = %s::uuid
                  AND cs.session_date BETWEEN %s AND %s
                  AND cs.status NOT IN ('CANCELLED', 'COMPLETED')
                """,
                (
                    request.substitute_trainer_id,
                    request.reason,
                    user.institute_id,
                    request.branch_id,
                    request.trainer_id,
                    request.absent_from,
                    request.absent_until,
                ),
            )
    return {"absence": dict(absence)}


@app.get("/api/admin/branches/{branch_id}/timetable")
async def get_branch_timetable(
    branch_id: str,
    from_date: date,
    to_date: date,
    user: CurrentUser = Depends(get_current_user),
):
    require_admin(user)
    if to_date < from_date:
        raise HTTPException(status_code=422, detail="to_date cannot be earlier than from_date.")
    if (to_date - from_date).days > 366:
        raise HTTPException(status_code=422, detail="Request at most one year of timetable data.")

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id::text, name
                FROM public.branches
                WHERE id = %s::uuid AND institute_id = %s::uuid AND is_active = TRUE
                """,
                (branch_id, user.institute_id),
            )
            branch = cursor.fetchone()
            if branch is None:
                raise HTTPException(status_code=404, detail="Active branch was not found in your institute.")

            cursor.execute(
                """
                SELECT cs.id::text AS session_id,
                       cs.session_date,
                       cs.start_time,
                       cs.end_time,
                       cs.mode::text AS mode,
                       cs.room_number,
                       cs.meeting_url,
                       cs.batch_id::text AS batch_id,
                       b.name AS batch_name,
                       cs.original_trainer_id::text AS original_trainer_id,
                       cs.trainer_id::text AS trainer_id,
                       original_trainer.name AS original_trainer_name,
                       assigned_trainer.name AS trainer_name,
                       (cs.original_trainer_id IS DISTINCT FROM cs.trainer_id) AS is_substituted,
                       cs.substitution_reason,
                       cs.status
                FROM public.class_sessions AS cs
                JOIN public.class_schedules AS s ON s.id = cs.schedule_id
                JOIN public.batches AS b ON b.id = cs.batch_id
                LEFT JOIN public.trainers AS original_trainer ON original_trainer.id = cs.original_trainer_id
                LEFT JOIN public.trainers AS assigned_trainer ON assigned_trainer.id = cs.trainer_id
                WHERE cs.institute_id = %s::uuid
                  AND s.branch_id = %s::uuid
                  AND cs.session_date BETWEEN %s AND %s
                ORDER BY cs.session_date, cs.start_time, b.name
                """,
                (user.institute_id, branch_id, from_date, to_date),
            )
            sessions = [dict(row) for row in cursor.fetchall()]

    return {
        "branch": dict(branch),
        "from_date": from_date,
        "to_date": to_date,
        "timetable": sessions,
    }


@app.get("/api/trainer/timetable")
async def get_trainer_timetable(
    from_date: date,
    to_date: date,
    user: CurrentUser = Depends(get_current_user),
):
    if user.role != "trainer" or not user.trainer_id:
        raise HTTPException(status_code=403, detail="Only trainers can access this timetable.")
    if to_date < from_date:
        raise HTTPException(status_code=422, detail="to_date cannot be earlier than from_date.")
    if (to_date - from_date).days > 366:
        raise HTTPException(status_code=422, detail="Request at most one year of timetable data.")

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT cs.id::text AS session_id, cs.session_date, cs.start_time,
                       cs.end_time, cs.mode::text AS mode, cs.room_number,
                       cs.meeting_url, cs.batch_id::text AS batch_id,
                       b.name AS batch_name, s.branch_id::text AS branch_id,
                       br.name AS branch_name, cs.trainer_id::text AS trainer_id,
                       t.name AS trainer_name, cs.status,
                       (cs.original_trainer_id IS DISTINCT FROM cs.trainer_id) AS is_substituted,
                       cs.substitution_reason
                FROM public.class_sessions AS cs
                JOIN public.class_schedules AS s ON s.id = cs.schedule_id
                JOIN public.batches AS b ON b.id = cs.batch_id
                JOIN public.branches AS br ON br.id = s.branch_id
                LEFT JOIN public.trainers AS t ON t.id = cs.trainer_id
                WHERE cs.institute_id = %s::uuid
                  AND cs.trainer_id = %s::uuid
                  AND cs.session_date BETWEEN %s AND %s
                ORDER BY cs.session_date, cs.start_time
                """,
                (user.institute_id, user.trainer_id, from_date, to_date),
            )
            sessions = [dict(row) for row in cursor.fetchall()]
    return {"from_date": from_date, "to_date": to_date, "timetable": sessions}


@app.get("/api/student/timetable")
async def get_student_timetable(
    from_date: date,
    to_date: date,
    user: CurrentUser = Depends(get_current_user),
):
    if user.role != "student" or not user.student_id:
        raise HTTPException(status_code=403, detail="Only students can access this timetable.")
    if to_date < from_date:
        raise HTTPException(status_code=422, detail="to_date cannot be earlier than from_date.")
    if (to_date - from_date).days > 366:
        raise HTTPException(status_code=422, detail="Request at most one year of timetable data.")

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT cs.id::text AS session_id, cs.session_date, cs.start_time,
                       cs.end_time, cs.mode::text AS mode, cs.room_number,
                       cs.meeting_url, cs.batch_id::text AS batch_id,
                       b.name AS batch_name, s.branch_id::text AS branch_id,
                       br.name AS branch_name, cs.trainer_id::text AS trainer_id,
                       t.name AS trainer_name, cs.status,
                       (cs.original_trainer_id IS DISTINCT FROM cs.trainer_id) AS is_substituted,
                       cs.substitution_reason
                FROM public.class_sessions AS cs
                JOIN public.class_schedules AS s ON s.id = cs.schedule_id
                JOIN public.batches AS b ON b.id = cs.batch_id
                JOIN public.branches AS br ON br.id = s.branch_id
                LEFT JOIN public.trainers AS t ON t.id = cs.trainer_id
                JOIN public.students AS st ON st.batch_id = cs.batch_id
                WHERE cs.institute_id = %s::uuid
                  AND st.id = %s::uuid
                  AND st.institute_id = %s::uuid
                  AND st.status = 'ACTIVE'
                  AND cs.session_date BETWEEN %s AND %s
                ORDER BY cs.session_date, cs.start_time
                """,
                (
                    user.institute_id,
                    user.student_id,
                    user.institute_id,
                    from_date,
                    to_date,
                ),
            )
            sessions = [dict(row) for row in cursor.fetchall()]
    return {"from_date": from_date, "to_date": to_date, "timetable": sessions}


@app.post("/api/admin/holidays")
async def create_holiday(
    request: HolidayCreateRequest,
    user: CurrentUser = Depends(get_current_user),
):
    require_admin(user)
    with get_connection() as connection:
        with connection.cursor() as cursor:
            if request.branch_id:
                cursor.execute(
                    """
                    SELECT id FROM public.branches
                    WHERE id = %s::uuid AND institute_id = %s::uuid AND is_active = TRUE
                    """,
                    (request.branch_id, user.institute_id),
                )
                if cursor.fetchone() is None:
                    raise HTTPException(status_code=404, detail="Active branch was not found in your institute.")
            cursor.execute(
                """
                INSERT INTO public.institute_holidays (
                    institute_id, branch_id, holiday_date, name, created_by_user_id
                )
                VALUES (%s::uuid, %s::uuid, %s, %s, %s::uuid)
                ON CONFLICT (institute_id, branch_id, holiday_date)
                DO UPDATE SET name = EXCLUDED.name
                RETURNING id::text, institute_id::text, branch_id::text,
                          holiday_date, name, created_at
                """,
                (
                    user.institute_id,
                    request.branch_id,
                    request.holiday_date,
                    request.name.strip(),
                    user.auth_user_id,
                ),
            )
            holiday = cursor.fetchone()
    return {"holiday": dict(holiday)}


@app.post("/api/admin/class-sessions/generate")
async def generate_class_sessions(
    request: SessionGenerateRequest,
    user: CurrentUser = Depends(get_current_user),
):
    require_admin(user)
    if request.to_date < request.from_date:
        raise HTTPException(status_code=422, detail="to_date cannot be earlier than from_date.")
    if (request.to_date - request.from_date).days > 366:
        raise HTTPException(status_code=422, detail="Generate sessions for at most one year.")

    created = 0
    with get_connection() as connection:
        with connection.cursor() as cursor:
            if request.branch_id:
                cursor.execute(
                    """
                    SELECT id
                    FROM public.branches
                    WHERE id = %s::uuid AND institute_id = %s::uuid AND is_active = TRUE
                    """,
                    (request.branch_id, user.institute_id),
                )
                if cursor.fetchone() is None:
                    raise HTTPException(status_code=404, detail="Active branch was not found in your institute.")

            current = request.from_date
            while current <= request.to_date:
                cursor.execute(
                    """
                    INSERT INTO public.class_sessions (
                        institute_id, schedule_id, batch_id,
                        original_trainer_id, trainer_id, session_date,
                        start_time, end_time, mode, room_number, meeting_url
                    )
                    SELECT s.institute_id, s.id, s.batch_id,
                           s.trainer_id,
                           COALESCE(ta.substitute_trainer_id, s.trainer_id),
                           %s,
                           s.start_time, s.end_time, s.mode,
                           s.room_number, s.meeting_url
                    FROM public.class_schedules AS s
                    LEFT JOIN public.trainer_absences AS ta
                      ON ta.institute_id = s.institute_id
                     AND ta.branch_id = s.branch_id
                     AND ta.trainer_id = s.trainer_id
                     AND ta.absent_from <= %s
                     AND ta.absent_until >= %s
                    JOIN public.batches AS b ON b.id = s.batch_id
                    WHERE s.institute_id = %s::uuid
                      AND (%s::uuid IS NULL OR s.branch_id = %s::uuid)
                      AND s.is_active = TRUE
                      AND s.day_of_week = EXTRACT(DOW FROM %s::date)
                      AND s.effective_from <= %s
                      AND (s.effective_until IS NULL OR s.effective_until >= %s)
                      AND b.start_date <= %s
                      AND (b.end_date IS NULL OR b.end_date >= %s)
                      AND b.status = 'ACTIVE'
                      AND NOT EXISTS (
                          SELECT 1
                          FROM public.institute_holidays AS h
                          WHERE h.institute_id = s.institute_id
                            AND h.holiday_date = %s
                            AND (h.branch_id IS NULL OR h.branch_id = s.branch_id)
                      )
                    ON CONFLICT (schedule_id, session_date, start_time) DO NOTHING
                    """,
                    (
                        current,
                        current,
                        current,
                        user.institute_id,
                        request.branch_id,
                        request.branch_id,
                        current,
                        current,
                        current,
                        current,
                        current,
                        current,
                    ),
                )
                created += cursor.rowcount
                current += timedelta(days=1)
    return {"created_sessions": created}


@app.post("/api/admin/class-sessions/{session_id}/substitute")
async def substitute_trainer(
    session_id: str,
    request: SessionSubstitutionRequest,
    user: CurrentUser = Depends(get_current_user),
):
    require_admin(user)
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, batch_id, session_date, start_time, end_time,
                       room_number, trainer_id, status
                FROM public.class_sessions
                WHERE id = %s::uuid AND institute_id = %s::uuid
                FOR UPDATE
                """,
                (session_id, user.institute_id),
            )
            session = cursor.fetchone()
            if session is None:
                raise HTTPException(status_code=404, detail="Class session was not found.")
            if session["status"] in {"COMPLETED", "CANCELLED"}:
                raise HTTPException(status_code=409, detail="This session cannot be reassigned.")

            cursor.execute(
                """
                SELECT id FROM public.trainers
                WHERE id = %s::uuid AND institute_id = %s::uuid
                  AND is_active = TRUE
                """,
                (request.substitute_trainer_id, user.institute_id),
            )
            if cursor.fetchone() is None:
                raise HTTPException(status_code=404, detail="Active substitute trainer was not found.")

            cursor.execute(
                """
                SELECT 1
                FROM public.class_sessions AS cs
                WHERE cs.institute_id = %s::uuid
                  AND cs.id <> %s::uuid
                  AND cs.session_date = %s
                  AND cs.status NOT IN ('CANCELLED', 'COMPLETED')
                  AND cs.start_time < %s
                  AND cs.end_time > %s
                  AND (
                      cs.trainer_id = %s::uuid
                      OR (
                          cs.room_number IS NOT NULL
                          AND cs.room_number = %s
                          AND cs.batch_id <> %s::uuid
                      )
                  )
                LIMIT 1
                """,
                (
                    user.institute_id,
                    session_id,
                    session["session_date"],
                    session["end_time"],
                    session["start_time"],
                    request.substitute_trainer_id,
                    session["room_number"],
                    session["batch_id"],
                ),
            )
            if cursor.fetchone() is not None:
                raise HTTPException(
                    status_code=409,
                    detail="The substitute trainer or classroom has an overlapping session.",
                )

            cursor.execute(
                """
                UPDATE public.class_sessions
                SET original_trainer_id = COALESCE(original_trainer_id, trainer_id),
                    trainer_id = %s::uuid,
                    substitution_reason = %s,
                    updated_at = NOW()
                WHERE id = %s::uuid AND institute_id = %s::uuid
                RETURNING id::text, batch_id::text, session_date,
                          start_time, end_time, trainer_id::text,
                          original_trainer_id::text, substitution_reason, status
                """,
                (
                    request.substitute_trainer_id,
                    request.reason,
                    session_id,
                    user.institute_id,
                ),
            )
            updated = cursor.fetchone()
    return {"session": dict(updated)}


@app.patch("/api/admin/class-sessions/{session_id}/cancel")
async def cancel_class_session(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    require_admin(user)
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE public.class_sessions
                SET status = 'CANCELLED', updated_at = NOW()
                WHERE id = %s::uuid
                  AND institute_id = %s::uuid
                  AND status NOT IN ('COMPLETED', 'CANCELLED')
                RETURNING id::text, session_date, status
                """,
                (session_id, user.institute_id),
            )
            session = cursor.fetchone()
            if session is None:
                raise HTTPException(status_code=404, detail="Active class session was not found.")
    return {"session": dict(session)}


@app.patch("/api/admin/class-sessions/{session_id}/reschedule")
async def reschedule_class_session(
    session_id: str,
    request: SessionRescheduleRequest,
    user: CurrentUser = Depends(get_current_user),
):
    require_admin(user)
    if request.end_time <= request.start_time:
        raise HTTPException(status_code=422, detail="end_time must be later than start_time.")

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT cs.id, cs.batch_id, cs.trainer_id, cs.mode, cs.room_number,
                       s.branch_id
                FROM public.class_sessions AS cs
                JOIN public.class_schedules AS s ON s.id = cs.schedule_id
                WHERE cs.id = %s::uuid AND cs.institute_id = %s::uuid
                FOR UPDATE
                """,
                (session_id, user.institute_id),
            )
            session = cursor.fetchone()
            if session is None:
                raise HTTPException(status_code=404, detail="Class session was not found.")
            if session["status"] in {"COMPLETED", "CANCELLED"}:
                raise HTTPException(status_code=409, detail="This session cannot be rescheduled.")
            if session["mode"] == "CLASSROOM" and not request.room_number:
                raise HTTPException(status_code=422, detail="room_number is required for classroom sessions.")
            if session["mode"] == "ONLINE" and not request.meeting_url:
                raise HTTPException(status_code=422, detail="meeting_url is required for online sessions.")

            cursor.execute(
                """
                SELECT 1
                FROM public.class_sessions AS other
                JOIN public.class_schedules AS os ON os.id = other.schedule_id
                WHERE other.institute_id = %s::uuid
                  AND other.id <> %s::uuid
                  AND other.session_date = %s
                  AND other.status NOT IN ('CANCELLED', 'COMPLETED')
                  AND other.start_time < %s
                  AND other.end_time > %s
                  AND (
                      other.batch_id = %s::uuid
                      OR other.trainer_id = %s::uuid
                      OR (
                          %s = 'CLASSROOM'
                          AND os.branch_id = %s::uuid
                          AND other.room_number = %s
                      )
                  )
                LIMIT 1
                """,
                (
                    user.institute_id,
                    session_id,
                    request.session_date,
                    request.end_time,
                    request.start_time,
                    session["batch_id"],
                    session["trainer_id"],
                    session["mode"],
                    session["branch_id"],
                    request.room_number,
                ),
            )
            if cursor.fetchone() is not None:
                raise HTTPException(status_code=409, detail="The rescheduled session overlaps another class.")

            cursor.execute(
                """
                UPDATE public.class_sessions
                SET session_date = %s, start_time = %s, end_time = %s,
                    room_number = %s, meeting_url = %s, status = 'RESCHEDULED',
                    substitution_reason = COALESCE(substitution_reason, '') ||
                        CASE WHEN COALESCE(substitution_reason, '') = '' THEN %s ELSE %s END,
                    updated_at = NOW()
                WHERE id = %s::uuid AND institute_id = %s::uuid
                RETURNING id::text, session_date, start_time, end_time,
                          room_number, meeting_url, status, substitution_reason
                """,
                (
                    request.session_date,
                    request.start_time,
                    request.end_time,
                    request.room_number,
                    request.meeting_url,
                    request.reason,
                    " | " + request.reason,
                    session_id,
                    user.institute_id,
                ),
            )
            updated = cursor.fetchone()
    return {"session": dict(updated)}


@app.post("/api/admin/invitations/student")
async def invite_student(
    request: MemberInvitationRequest,
    user: CurrentUser = Depends(get_current_user),
):
    if user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="Only institute administrators can invite students.",
        )
    return invite_member(
        email=request.email,
        full_name=request.full_name,
        phone=request.phone,
        institute_id=user.institute_id,
        role="STUDENT",
    )


@app.post("/api/admin/invitations/tutor")
async def invite_tutor(
    request: MemberInvitationRequest,
    user: CurrentUser = Depends(get_current_user),
):
    if user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="Only institute administrators can invite tutors.",
        )
    return invite_member(
        email=request.email,
        full_name=request.full_name,
        phone=request.phone,
        expertise=request.expertise,
        institute_id=user.institute_id,
        role="TRAINER",
    )


@app.get("/api/auth/me")
async def current_user(
    user: CurrentUser = Depends(get_current_user),
):
    return {
        "auth_user_id": user.auth_user_id,
        "email": user.email,
        "institute_id": user.institute_id,
        "institute_name": user.institute_name,
        "role": user.role,
        "student_id": user.student_id,
        "trainer_id": user.trainer_id,
    }


@app.post("/api/copilot/chat")
async def chat(
    request: ChatRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    if engine is None:
        raise HTTPException(
            status_code=503,
            detail="Copilot engine is not initialized",
        )

    try:
        return engine.process_prompt(
            user_prompt=request.message,
            user_role=current_user.role.lower(),
            user_id=current_user.auth_user_id,
            institute_id=current_user.institute_id,
            student_id=current_user.student_id,
            trainer_id=current_user.trainer_id,
        )
    except HTTPException:
        raise
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Processing error: {error}",
        )


@app.post("/api/actions/{action_id}/approve")
async def approve_action(
    action_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    role = current_user.role.lower()
    action_claimed = False
    try:
        action = claim_action(action_id, current_user.auth_user_id, role)
        action_claimed = True
        messages = action["payload"].get("messages", [])
        if not messages or any(not message.get("phone") for message in messages):
            raise ValueError("Every staged message must have a phone number.")

        result = send_messages(messages)
        mark_action_sent(action_id)
        return {
            "action_id": action_id,
            "status": "SENT",
            "messages": result,
        }
    except HTTPException:
        raise
    except PermissionError as error:
        raise HTTPException(
            status_code=403,
            detail=str(error),
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Processing error: {error}",
        ) from error


@app.post("/api/actions/{action_id}/reject")
async def reject_staged_action(
    action_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    try:
        reject_action(
            action_id=action_id,
            user_id=current_user.auth_user_id,
            role=current_user.role.lower(),
        )
        return {"action_id": action_id, "status": "REJECTED"}
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.get("/health")
def health():
    return {"status": "ok"}