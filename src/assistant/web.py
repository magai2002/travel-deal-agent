"""
Minimal FastAPI web UI for the assistant — same LangGraph backend as
src/assistant/cli.py, reachable from a browser instead of a terminal.

Run with: uvicorn src.assistant.web:app --host 127.0.0.1 --port 8787
(see deploy/travel-assistant-web.service for the systemd unit; it binds to
localhost only — put it behind the box's existing Nginx for TLS/external
access rather than exposing uvicorn directly).

This is a separate, long-lived process from src/main.py's daily batch run
and from cli.py — it does not touch either. It uses its own LangGraph
thread_id (ASSISTANT_WEB_THREAD_ID, default "web") in the same SQLite
checkpoint DB the CLI uses, so the two have independent conversation
histories against the same routes.yaml.

A single shared username/password (HTTP Basic Auth) gates every route —
this tool can write to routes.yaml, so ASSISTANT_WEB_PASSWORD must be set
or the app refuses to start rather than silently serving unauthenticated.
"""
from __future__ import annotations

import logging
import os
import secrets
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from pydantic import BaseModel

from src.agent.cost import CostTracker
from src.assistant.graph import build_graph, extract_text_content

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[RotatingFileHandler(LOG_DIR / "assistant.log", maxBytes=5_000_000, backupCount=5)],
)
log = logging.getLogger("travel-assistant-web")

DB_PATH = os.environ.get("ASSISTANT_STATE_DB", "assistant_state.db")
THREAD_ID = os.environ.get("ASSISTANT_WEB_THREAD_ID", "web")
STATIC_DIR = Path(__file__).parent / "static"

WEB_USER = os.environ.get("ASSISTANT_WEB_USER", "alexey")
WEB_PASSWORD = os.environ.get("ASSISTANT_WEB_PASSWORD")
if not WEB_PASSWORD:
    raise RuntimeError(
        "ASSISTANT_WEB_PASSWORD is not set — refusing to start a web UI "
        "with config-write access and no auth. Set it in .env."
    )

_security = HTTPBasic()


def _require_auth(credentials: HTTPBasicCredentials = Depends(_security)) -> None:
    # compare_digest on both, always — avoids leaking via timing whether
    # the username alone was right.
    user_ok = secrets.compare_digest(credentials.username, WEB_USER)
    pass_ok = secrets.compare_digest(credentials.password, WEB_PASSWORD)
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=401, detail="Invalid credentials", headers={"WWW-Authenticate": "Basic"}
        )


# Single-user personal tool: one graph, one thread, one running cost total
# for the life of the process — no per-visitor session management.
_state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    checkpointer_cm = SqliteSaver.from_conn_string(DB_PATH)
    checkpointer = checkpointer_cm.__enter__()
    cost_tracker = CostTracker()
    _state["graph"] = build_graph(checkpointer)
    _state["cost_tracker"] = cost_tracker
    _state["config"] = {"configurable": {"thread_id": THREAD_ID, "cost_tracker": cost_tracker}}
    log.info("Assistant web UI started (thread_id=%s)", THREAD_ID)
    try:
        yield
    finally:
        checkpointer_cm.__exit__(None, None, None)
        log.info("Assistant web UI stopped — %s", cost_tracker.summary())


app = FastAPI(lifespan=lifespan)


class TurnIn(BaseModel):
    message: str | None = None
    resume: dict | None = None


def _graph_result_to_response(result: dict) -> dict:
    if "__interrupt__" in result:
        payload = result["__interrupt__"][0].value
        return {"type": "confirm", "calls": payload.get("proposed_calls", [])}
    last_message = result["messages"][-1]
    return {"type": "reply", "text": extract_text_content(last_message.content)}


@app.get("/")
def index(_: None = Depends(_require_auth)) -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/turn")
def turn(body: TurnIn, _: None = Depends(_require_auth)) -> JSONResponse:
    graph = _state["graph"]
    config = _state["config"]

    if body.resume is not None:
        result = graph.invoke(Command(resume=body.resume), config)
    elif body.message:
        result = graph.invoke({"messages": [{"role": "user", "content": body.message}]}, config)
    else:
        raise HTTPException(status_code=400, detail="Provide either 'message' or 'resume'")

    log.info(_state["cost_tracker"].summary())
    return JSONResponse(_graph_result_to_response(result))
