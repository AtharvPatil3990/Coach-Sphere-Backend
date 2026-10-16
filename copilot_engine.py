import os
import json
import sqlglot
from sqlglot.expressions import Select
from pydantic import BaseModel, Field
from typing import Optional, List
from google import genai
from google.genai import types
from database import run_query

# 1. Role-Based Schemas: Fee tables are excluded for trainers
SCHEMA_DESCRIPTIONS = {
    "users": "users(id, full_name, email, phone, role)",
    "batches": "batches(id, name, trainer_id)",
    "enrollments": "enrollments(id, batch_id, student_id)",
    "attendance_logs": "attendance_logs(id, batch_id, student_id, session_date, status ['present','absent','late'])",
    "fee_installments": "fee_installments(id, enrollment_id, installment_num, amount, due_date, status ['paid','pending','overdue'])"
}

ROLE_PERMISSIONS = {
    "owner": ["users", "batches", "enrollments", "attendance_logs", "fee_installments"],
    "counselor": ["users", "batches", "enrollments", "attendance_logs", "fee_installments"],
    "accounts": ["users", "batches", "enrollments", "fee_installments"],
    "trainer": ["users", "batches", "enrollments", "attendance_logs"] # Fee tables masked
}

def get_allowed_schema(role: str) -> str:
    allowed_tables = ROLE_PERMISSIONS.get(role, ROLE_PERMISSIONS["trainer"])
    return "\n".join([f"- {SCHEMA_DESCRIPTIONS[t]}" for t in allowed_tables])

# 2. Pydantic Response Schema for Structured LLM Output
class CopilotResponseSchema(BaseModel):
    summary: str = Field(description="Clear explanation of the findings in plain English.")
    sql_query: str = Field(description="Safe, read-only SQL query answering the question.")
    requires_action: bool = Field(description="True if user asked to draft a message, alert, or task.")
    action_type: Optional[str] = Field(default=None, description="e.g., WHATSAPP_REMINDER or null")
    action_template: Optional[str] = Field(default=None, description="Message template with placeholders like {name}")

class CopilotEngine:
    def __init__(self):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY environment variable is not set.")
        self.client = genai.Client(api_key=api_key)

    def validate_sql(self, sql: str, role: str) -> str:
        """Enforces that SQL is SELECT-only and does not touch forbidden tables."""
        parsed = sqlglot.parse_one(sql, read="sqlite")
        
        # Enforce SELECT statement only
        if not isinstance(parsed, Select):
            raise PermissionError("Security Guardrail: Only SELECT queries are permitted.")
        
        # Check forbidden keywords
        blocked = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE"]
        if any(w in sql.upper() for w in blocked):
            raise PermissionError("Security Guardrail: Mutative SQL keywords detected.")

        # Ensure trainer cannot query fee_installments
        allowed_tables = ROLE_PERMISSIONS.get(role, [])
        for table in parsed.find_all(sqlglot.exp.Table):
            if table.name.lower() not in allowed_tables:
                raise PermissionError(f"Access Denied: Role '{role}' cannot query '{table.name}'.")

        return sql

    def process_prompt(self, user_prompt: str, user_role: str):
        allowed_schema_text = get_allowed_schema(user_role)

        system_instruction = f"""
You are the Operations Copilot for an educational institute.
You translate plain questions into standard SQLite SELECT queries.

Active User Role: {user_role}
Accessible Database Tables:
{allowed_schema_text}

CRITICAL RULES:
1. ONLY query tables listed in the Accessible Database Tables above.
2. If the user asks for fee information and their role does not have fee tables, explain that fee data is restricted. Set sql_query to "SELECT 1 WHERE 1=0".
3. Write clean SQLite compatible SQL.
4. If the user asks to draft a message, reminder, or WhatsApp, set requires_action=True, specify action_type='WHATSAPP_REMINDER', and provide an action_template.
"""

        response = self.client.models.generate_content(
            model="gemini-2.5-flash",
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json",
                response_schema=CopilotResponseSchema,
                temperature=0.0
            )
        )

        structured_output = CopilotResponseSchema.model_validate_json(response.text)
        
        # Validate query
        validated_sql = self.validate_sql(structured_output.sql_query, user_role)
        
        # Execute verified query
        db_results = run_query(validated_sql)

        # Stage Action if requested
        action_preview = None
        if structured_output.requires_action and db_results:
            sample_name = db_results[0].get("full_name", "Student")
            action_preview = {
                "action_type": structured_output.action_type,
                "recipients_count": len(db_results),
                "draft_sample": structured_output.action_template.replace("{name}", sample_name) if structured_output.action_template else "Pending reminder",
                "target_records": db_results,
                "status": "AWAITING_HUMAN_APPROVAL"
            }

        return {
            "summary": structured_output.summary,
            "executed_sql": validated_sql,
            "data": db_results,
            "action_preview": action_preview
        }