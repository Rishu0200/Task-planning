import logging
import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .agent import Agent
from .config import settings
from .schemas import AgentRequest, AgentResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("agent.api")

app = FastAPI(
    title="Autonomous Document-Generation Agent",
    description=(
        "Accepts a natural language request, autonomously plans the required tasks, "
        "executes them, and returns a polished Word (.docx) document."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    return {
        "service": "autonomous-docx-agent",
        "status": "ok",
        "llm_provider": settings.LLM_PROVIDER,
        "endpoints": {
            "POST /agent": "Submit a natural language request",
            "GET /download/{filename}": "Download a generated .docx",
            "GET /health": "Health check",
        },
    }


@app.get("/health")
def health():
    return {"status": "healthy", "llm_provider": settings.LLM_PROVIDER}


@app.post("/agent", response_model=AgentResponse)
def run_agent(payload: AgentRequest):
    request_text = payload.request.strip()
    if not request_text:
        raise HTTPException(status_code=400, detail="'request' must not be empty")

    logger.info("Received request: %s", request_text)
    agent = Agent()
    try:
        result = agent.run(request_text)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Agent run failed")
        raise HTTPException(status_code=500, detail=f"Agent failed to complete the task: {exc}") from exc

    return AgentResponse(
        status=result["status"],
        document_type=result["document_type"],
        title=result["title"],
        plan=result["plan"],
        summary=result["summary"],
        llm_provider_used=result["llm_provider_used"],
        filename=result["filename"],
        download_url=f"/download/{result['filename']}",
    )


@app.get("/download/{filename}")
def download(filename: str):
    # prevent path traversal
    safe_name = os.path.basename(filename)
    path = os.path.join(settings.OUTPUT_DIR, safe_name)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=safe_name,
    )
