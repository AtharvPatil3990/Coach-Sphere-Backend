from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from copilot_engine import CopilotEngine
from database import init_db
import os

app = FastAPI(title="Institute Operations Copilot API")
engine = None

@app.on_event("startup")
def startup_event():
    global engine
    if not os.path.exists("institute_ops.db"):
        init_db()
    engine = CopilotEngine()

class ChatRequest(BaseModel):
    message: str
    user_role: str = "counselor"  # 'owner', 'counselor', 'trainer', 'accounts'

@app.post("/api/copilot/chat")
async def chat(request: ChatRequest):
    try:
        result = engine.process_prompt(
            user_prompt=request.message,
            user_role=request.user_role.lower()
        )
        return result
    except PermissionError as pe:
        raise HTTPException(status_code=403, detail=str(pe))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Processing error: {str(e)}")

@app.get("/health")
def health():
    return {"status": "ok"}