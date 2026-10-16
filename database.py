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