import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

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
from database import initialize_action_store
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