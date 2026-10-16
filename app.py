from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from actions import (
    claim_action,
    mark_action_failed,
    mark_action_sent,
    reject_action,
)
from copilot_engine import CopilotEngine
from database import initialize_action_store
from messaging import send_messages


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


class ChatRequest(BaseModel):
    message: str
    user_role: str = "counselor"
    user_id: str | None = None


class ActionDecisionRequest(BaseModel):
    user_id: str
    user_role: str


@app.post("/api/copilot/chat")
async def chat(request: ChatRequest):
    if engine is None:
        raise HTTPException(
            status_code=503,
            detail="Copilot engine is not initialized",
        )

    try:
        return engine.process_prompt(
            user_prompt=request.message,
            user_role=request.user_role.lower(),
            user_id=request.user_id,
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
async def approve_action(action_id: str, request: ActionDecisionRequest):
    role = request.user_role.lower()
    action_claimed = False
    try:
        action = claim_action(action_id, request.user_id, role)
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
async def reject_staged_action(action_id: str, request: ActionDecisionRequest):
    try:
        reject_action(
            action_id=action_id,
            user_id=request.user_id,
            role=request.user_role.lower(),
        )
        return {"action_id": action_id, "status": "REJECTED"}
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.get("/health")
def health():
    return {"status": "ok"}