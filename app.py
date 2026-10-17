import os
from contextlib import asynccontextmanager
from datetime import date, time

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