import os
from dataclasses import dataclass
import logging
import re
import uuid

from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from supabase import Client, create_client

from database import get_connection


logger = logging.getLogger(__name__)


load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL:
    raise ValueError("SUPABASE_URL is not configured")

if not SUPABASE_ANON_KEY:
    raise ValueError("SUPABASE_ANON_KEY is not configured")


supabase: Client = create_client(
    SUPABASE_URL,
    SUPABASE_ANON_KEY,
)
supabase_admin: Client | None = (
    create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    if SUPABASE_SERVICE_ROLE_KEY
    else None
)

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    auth_user_id: str
    email: str | None
    institute_id: str
    role: str
    student_id: str | None
    trainer_id: str | None


def _session_response(session) -> dict:
    if session is None:
        return {
            "access_token": None,
            "refresh_token": None,
            "expires_at": None,
            "user": None,
        }

    user = getattr(session, "user", None)
    return {
        "access_token": session.access_token,
        "refresh_token": session.refresh_token,
        "expires_at": getattr(session, "expires_at", None),
        "user": {
            "id": str(user.id),
            "email": user.email,
        }
        if user is not None
        else None,
    }


def _require_service_role_client() -> Client:
    if supabase_admin is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SUPABASE_SERVICE_ROLE_KEY is not configured.",
        )
    return supabase_admin


def _auth_signup_error(error: Exception) -> HTTPException:
    error_message = str(error)
    normalized_message = error_message.lower()
    logger.warning("Supabase admin signup failed: %s", error_message)

    if "rate limit" in normalized_message:
        return HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Supabase email rate limit exceeded. Wait before retrying.",
        )

    if "already registered" in normalized_message or "already exists" in normalized_message:
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    if "password" in normalized_message:
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The password does not meet Supabase requirements.",
        )

    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Unable to create the admin account.",
    )


def _invitation_error(error: Exception) -> HTTPException:
    error_message = str(error)
    normalized_message = error_message.lower()
    logger.warning("Supabase member invitation failed: %s", error_message)

    if "rate limit" in normalized_message:
        return HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Supabase email rate limit exceeded. Wait before retrying.",
        )

    if "already registered" in normalized_message or "already exists" in normalized_message:
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Unable to send the invitation email.",
    )


def _new_institute_code(institute_name: str) -> str:
    name_code = re.sub(r"[^A-Z0-9]+", "-", institute_name.upper()).strip("-")
    name_code = name_code[:38] or "INSTITUTE"
    return f"{name_code}-{uuid.uuid4().hex[:8].upper()}"


def sign_up_admin(
    *,
    email: str,
    password: str,
    full_name: str,
    phone: str,
    institute_name: str,
    institute_address: str | None,
    institute_phone: str | None,
    institute_email: str | None,
) -> dict:
    admin_client = _require_service_role_client()
    institute_code = _new_institute_code(institute_name)

    try:
        response = admin_client.auth.admin.create_user(
            {
                "email": email,
                "password": password,
                "email_confirm": True,
                "user_metadata": {
                    "full_name": full_name,
                    "phone": phone,
                    "role": "admin",
                },
            }
        )
    except Exception as error:
        raise _auth_signup_error(error) from error

    user = response.user
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Supabase did not return the admin user.",
        )

    try:
        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO public.institutes (
                        name, code, address, phone, email, admin_user_id
                    )
                    VALUES (%s, %s, %s, %s, %s, %s::uuid)
                    RETURNING id::text
                    """,
                    (
                        institute_name,
                        institute_code,
                        institute_address,
                        institute_phone,
                        institute_email,
                        str(user.id),
                    ),
                )
                institute_id = cursor.fetchone()["id"]
                cursor.execute(
                    """
                    INSERT INTO public.user_profiles (
                        auth_user_id, full_name, phone
                    )
                    VALUES (%s::uuid, %s, %s)
                    """,
                    (str(user.id), full_name, phone),
                )
                cursor.execute(
                    """
                    INSERT INTO public.institute_members (
                        auth_user_id, institute_id, role, is_active
                    )
                    VALUES (%s::uuid, %s::uuid, 'ADMIN', TRUE)
                    """,
                    (str(user.id), institute_id),
                )
    except Exception as error:
        try:
            admin_client.auth.admin.delete_user(str(user.id))
        except Exception as cleanup_error:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    "Admin account setup failed and automatic cleanup also "
                    "failed. Remove the Auth user manually."
                ),
            ) from cleanup_error
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The account was created, but the institute could not be created.",
        ) from error

    return {
        "message": "Institute and admin account created. You can sign in now.",
        "institute_id": institute_id,
        "user": {
            "id": str(user.id),
            "email": user.email,
        },
    }


def invite_member(
    *,
    email: str,
    full_name: str,
    phone: str,
    institute_id: str,
    role: str,
    expertise: str | None = None,
) -> dict:
    admin_client = _require_service_role_client()
    try:
        response = admin_client.auth.admin.invite_user_by_email(
            email,
            options={
                "data": {
                    "full_name": full_name,
                    "phone": phone,
                    "role": role.lower(),
                    "institute_id": institute_id,
                }
            },
        )
    except Exception as error:
        raise _invitation_error(error) from error

    user = response.user
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Supabase did not return an invited user.",
        )

    try:
        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO public.user_profiles (
                        auth_user_id, full_name, phone
                    )
                    VALUES (%s::uuid, %s, %s)
                    ON CONFLICT (auth_user_id) DO UPDATE
                    SET full_name = EXCLUDED.full_name,
                        phone = EXCLUDED.phone
                    """,
                    (str(user.id), full_name, phone),
                )
                if role == "STUDENT":
                    cursor.execute(
                        """
                        INSERT INTO public.students (
                            id, institute_id, name, email, phone
                        )
                        VALUES (%s::uuid, %s::uuid, %s, %s, %s)
                        ON CONFLICT (id) DO NOTHING
                        """,
                        (str(user.id), institute_id, full_name, email, phone),
                    )
                    student_id = str(user.id)
                    trainer_id = None
                else:
                    cursor.execute(
                        """
                        INSERT INTO public.trainers (
                            id, institute_id, name, email, phone, expertise
                        )
                        VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s)
                        ON CONFLICT (id) DO NOTHING
                        """,
                        (
                            str(user.id),
                            institute_id,
                            full_name,
                            email,
                            phone,
                            expertise,
                        ),
                    )
                    student_id = None
                    trainer_id = str(user.id)
                cursor.execute(
                    """
                    INSERT INTO public.institute_members (
                        auth_user_id, institute_id, role,
                        student_id, trainer_id, is_active
                    )
                    VALUES (
                        %s::uuid, %s::uuid, %s, %s::uuid, %s::uuid, TRUE
                    )
                    ON CONFLICT (auth_user_id, institute_id) DO NOTHING
                    """,
                    (
                        str(user.id),
                        institute_id,
                        role,
                        student_id,
                        trainer_id,
                    ),
                )
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The invitation was sent, but the member profile could not "
                "be created."
            ),
        ) from error

    return {
        "message": f"{role.title()} invitation sent by email.",
        "user_id": str(user.id),
        "email": user.email,
        "role": role.lower(),
    }


def sign_in(email: str, password: str) -> dict:
    try:
        response = supabase.auth.sign_in_with_password(
            {
                "email": email,
                "password": password,
            }
        )
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from error

    if response.session is None or response.user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email confirmation may be required before signing in.",
        )

    membership = load_user_membership(str(response.user.id))
    return {
        "message": "Signed in successfully.",
        "role": membership.role,
        "institute_id": membership.institute_id,
        **_session_response(response.session),
    }


def get_supabase_user(access_token: str):
    try:
        response = supabase.auth.get_user(access_token)
        user = response.user

        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid access token",
                headers={"WWW-Authenticate": "Bearer"},
            )

        return user

    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unable to validate Supabase access token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from error


def load_user_membership(auth_user_id: str) -> CurrentUser:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    auth_user_id::text,
                    institute_id::text,
                    LOWER(role::text) AS role,
                    student_id::text,
                    trainer_id::text
                FROM public.institute_members
                WHERE auth_user_id = %s::uuid
                  AND is_active = TRUE
                ORDER BY created_at
                LIMIT 1
                """,
                (auth_user_id,),
            )

            membership = cursor.fetchone()

    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not assigned to an active institute",
        )

    return CurrentUser(
        auth_user_id=membership["auth_user_id"],
        email=None,
        institute_id=membership["institute_id"],
        role=membership["role"],
        student_id=membership["student_id"],
        trainer_id=membership["trainer_id"],
    )


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(
        bearer_scheme
    ),
) -> CurrentUser:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer access token is required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer authentication is required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    supabase_user = get_supabase_user(credentials.credentials)

    return load_user_membership(str(supabase_user.id))