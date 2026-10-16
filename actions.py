import json
import uuid

from database import get_connection


PENDING = "AWAITING_HUMAN_APPROVAL"
APPROVING = "APPROVING"
SENT = "SENT"
REJECTED = "REJECTED"
FAILED = "FAILED"


def create_staged_action(
    action_type: str,
    user_id: str | None,
    role: str,
    prompt: str,
    payload: dict,
    sql: str,
) -> str:
    action_id = f"act_{uuid.uuid4().hex[:12]}"
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO staged_actions (
                    id, action_type, created_by_user_id, created_by_role,
                    prompt, payload, sql_query
                )
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
                """,
                (
                    action_id,
                    action_type,
                    user_id,
                    role,
                    prompt,
                    json.dumps(payload, default=str),
                    sql,
                ),
            )
    return action_id


def claim_action(action_id: str, user_id: str, role: str) -> dict:
    if role not in {"owner", "counselor"}:
        raise PermissionError("Only owners and counselors can approve actions.")

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE staged_actions
                SET status = %s,
                    approved_at = NOW(),
                    approved_by_user_id = %s
                WHERE id = %s
                  AND status = %s
                  AND expires_at > NOW()
                RETURNING *
                """,
                (APPROVING, user_id, action_id, PENDING),
            )
            action = cursor.fetchone()

    if action is None:
        raise ValueError(
            "Action was not found, has expired, or has already been processed."
        )
    return dict(action)


def mark_action_sent(action_id: str) -> None:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE staged_actions
                SET status = %s, sent_at = NOW()
                WHERE id = %s AND status = %s
                """,
                (SENT, action_id, APPROVING),
            )


def mark_action_failed(action_id: str, reason: str) -> None:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE staged_actions
                SET status = %s, failure_reason = %s
                WHERE id = %s AND status = %s
                """,
                (FAILED, reason[:2000], action_id, APPROVING),
            )


def reject_action(action_id: str, user_id: str, role: str) -> None:
    if role not in {"owner", "counselor"}:
        raise PermissionError("Only owners and counselors can reject actions.")

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE staged_actions
                SET status = %s, approved_by_user_id = %s, approved_at = NOW()
                WHERE id = %s
                  AND status = %s
                  AND expires_at > NOW()
                """,
                (REJECTED, user_id, action_id, PENDING),
            )
            if cursor.rowcount != 1:
                raise ValueError(
                    "Action was not found, has expired, or has already been processed."
                )
