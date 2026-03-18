import os
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()
from session_store import store, Session

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")
log = logging.getLogger("devmate")


class IndexRequest(BaseModel):
    github_url: str
    github_token: str | None = None

class ChatRequest(BaseModel):
    session_id: str
    message: str
    history: list[dict] = []

class GenerateRequest(BaseModel):
    session_id: str
    feature_request: str
    extra_context: str | None = None

class DebugRequest(BaseModel):
    session_id: str
    error: str
    stack_trace: str | None = None
    extra_context: str | None = None

class DocsFetchRequest(BaseModel):
    session_id: str
    library: str
    question: str
    version: str | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("DevMate v2 starting up")
    if not os.getenv("DO_GRADIENT_API_KEY"):
        log.warning("DO_GRADIENT_API_KEY is not set — AI features will fail")
    else:
        log.info("DO_GRADIENT_API_KEY detected ✓")
    if os.getenv("GITHUB_TOKEN"):
        log.info("GITHUB_TOKEN detected (higher rate limits) ✓")
    yield
    log.info("DevMate v2 shutting down")


app = FastAPI(title="DevMate v2", version="2.0.0", lifespan=lifespan)


async def _run_indexing_pipeline(session_id: str, github_token: str | None):
    from github_client import parse_github_url, fetch_repo_files, extract_dependencies, extract_code_patterns
    from gradient_kb import create_knowledge_base, upload_files_to_kb, trigger_indexing, wait_for_indexing
    from gradient_agent import create_agent

    session = store.get(session_id)
    if session is None:
        return

    api_key = os.getenv("DO_GRADIENT_API_KEY", "")
    token = github_token or os.getenv("GITHUB_TOKEN")

    try:
        session.status = "indexing"
        session.status_message = "Fetching repository files from GitHub..."
        store.update(session)

        owner, repo = parse_github_url(session.github_url)
        files = await fetch_repo_files(owner, repo, token)

        session.file_count = len(files)
        session.file_paths = [f["path"] for f in files]
        session.status_message = f"Fetched {len(files)} files. Analysing dependencies..."
        store.update(session)

        session.dependencies = extract_dependencies(files)
        session.code_patterns = extract_code_patterns(files)
        session.status_message = "Creating Knowledge Base..."
        store.update(session)

        kb_id = await create_knowledge_base(api_key, session.repo_name)
        session.kb_id = kb_id
        store.update(session)

        uploaded = await upload_files_to_kb(api_key, kb_id, files)
        session.indexed_count = uploaded
        store.update(session)

        await trigger_indexing(api_key, kb_id)
        kb_status = await wait_for_indexing(api_key, kb_id)
        if kb_status != "ready":
            raise RuntimeError(f"KB indexing ended with status: {kb_status}")

        repo_context = {
            "repo_name": session.repo_name,
            "github_url": session.github_url,
            "file_paths": session.file_paths,
            "dependencies": session.dependencies,
            "code_patterns": session.code_patterns,
            "file_count": session.file_count,
        }
        agent_id = await create_agent(api_key, kb_id, repo_context)
        session.agent_id = agent_id
        session.status = "ready"
        session.status_message = "Ready — Agent is live and KB is indexed."
        store.update(session)
        log.info("Session %s ready (kb=%s agent=%s)", session_id, kb_id, agent_id)

    except Exception as exc:
        log.exception("Indexing pipeline failed for session %s", session_id)
        session = store.get(session_id)
        if session:
            session.status = "error"
            session.status_message = f"Error: {exc}"
            store.update(session)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "devmate-v2",
        "active_sessions": len(store),
        "do_gradient_key_set": bool(os.getenv("DO_GRADIENT_API_KEY")),
        "github_token_set": bool(os.getenv("GITHUB_TOKEN")),
    }


@app.get("/status/{session_id}")
async def get_status(session_id: str):
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    return session.to_status_dict()


@app.post("/index", status_code=202)
async def index_repo(req: IndexRequest, background_tasks: BackgroundTasks):
    from github_client import parse_github_url
    try:
        owner, repo_name = parse_github_url(req.github_url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    session = store.create(github_url=req.github_url, repo_name=repo_name)
    background_tasks.add_task(_run_indexing_pipeline, session.session_id, req.github_token)
    log.info("Indexing started for %s/%s (session=%s)", owner, repo_name, session.session_id)
    return {"session_id": session.session_id, "repo_name": repo_name, "message": "Indexing started."}


@app.post("/chat")
async def chat_endpoint(req: ChatRequest):
    session = _require_ready_session(req.session_id)
    from gradient_agent import chat as agent_chat
    api_key = os.getenv("DO_GRADIENT_API_KEY", "")
    response = await agent_chat(api_key=api_key, agent_id=session.agent_id, message=req.message, history=req.history)
    return {"response": response, "session_id": req.session_id}


@app.post("/generate")
async def generate_code(req: GenerateRequest):
    session = _require_ready_session(req.session_id)
    from gradient_agent import generate_code as agent_generate
    from code_generator import validate_generated_code
    api_key = os.getenv("DO_GRADIENT_API_KEY", "")
    result = await agent_generate(api_key=api_key, agent_id=session.agent_id, feature_request=req.feature_request, context=_session_repo_context(session))
    result["validation"] = validate_generated_code(result.get("code_blocks", []), session.file_paths)
    return result


@app.post("/debug")
async def debug_code(req: DebugRequest):
    session = _require_ready_session(req.session_id)
    from gradient_agent import debug_code as agent_debug
    api_key = os.getenv("DO_GRADIENT_API_KEY", "")
    return await agent_debug(api_key=api_key, agent_id=session.agent_id, error=req.error, stack_trace=req.stack_trace or "", context=_session_repo_context(session))


@app.post("/docs-fetch")
async def docs_fetch(req: DocsFetchRequest):
    session = _require_ready_session(req.session_id)
    from gradient_agent import chat as agent_chat
    api_key = os.getenv("DO_GRADIENT_API_KEY", "")
    version = req.version
    if not version:
        for ecosystem_deps in session.dependencies.values():
            for k, v in ecosystem_deps.items():
                if k.lower() == req.library.lower():
                    version = v
                    break
    response = await agent_chat(api_key=api_key, agent_id=session.agent_id, message=f"Explain {req.library} {version or ''}: {req.question}", history=[])
    return {"library": req.library, "version": version or "latest", "question": req.question, "answer": response, "session_id": req.session_id}


@app.delete("/session/{session_id}")
async def delete_session(session_id: str):
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    api_key = os.getenv("DO_GRADIENT_API_KEY", "")
    cleanup_errors = []
    if session.agent_id:
        try:
            from gradient_agent import delete_agent
            await delete_agent(api_key, session.agent_id)
        except Exception as exc:
            cleanup_errors.append(f"Agent: {exc}")
    if session.kb_id:
        try:
            from gradient_kb import delete_knowledge_base
            await delete_knowledge_base(api_key, session.kb_id)
        except Exception as exc:
            cleanup_errors.append(f"KB: {exc}")
    store.delete(session_id)
    return {"deleted": True, "session_id": session_id, "cleanup_errors": cleanup_errors}


def _require_ready_session(session_id: str) -> Session:
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    if session.status != "ready":
        raise HTTPException(status_code=409, detail=f"Session not ready ({session.status}): {session.status_message}")
    return session


def _session_repo_context(session: Session) -> dict:
    return {"repo_name": session.repo_name, "github_url": session.github_url, "file_paths": session.file_paths, "dependencies": session.dependencies, "code_patterns": session.code_patterns, "file_count": session.file_count}


@app.get("/")
async def serve_frontend():
    for path in ["/app/frontend/index.html", Path(__file__).parent / "frontend" / "index.html"]:
        if Path(path).exists():
            return FileResponse(str(path))
    return {"error": "frontend not found", "cwd": os.getcwd()}
