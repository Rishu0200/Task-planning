from typing import List, Optional
from pydantic import BaseModel, Field


class AgentRequest(BaseModel):
    request: str = Field(..., description="Natural language request describing the document to produce")


class Task(BaseModel):
    id: int
    name: str
    status: str = "pending"   # pending | in_progress | done | failed
    detail: Optional[str] = None


class AgentResponse(BaseModel):
    status: str
    document_type: str
    title: str
    plan: List[Task]
    summary: str
    llm_provider_used: str
    filename: str
    download_url: str
