import os
import time
import json
import uuid
import sqlite3
import logging
import asyncio
import shutil
import tempfile
import subprocess
import re

from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from pydantic import BaseModel, Field

from google import genai
from google.genai import types


# ============================================================
# ENGINEER AI
# Backend Version 5.0
# ============================================================

APP_NAME = "Engineer AI"
APP_VERSION = "5.0.0"

GEMINI_API_KEY = os.getenv(
    "GEMINI_API_KEY",
    ""
)

GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-2.5-flash"
)

DATABASE_PATH = os.getenv(
    "DATABASE_PATH",
    "engineer_ai.db"
)

EXECUTION_MODE = os.getenv(
    "EXECUTION_MODE",
    "docker"
)

MAX_PROMPT_LENGTH = 30000
MAX_FILE_SIZE = 300000
MAX_PROJECT_SIZE = 3000000
MAX_PROJECT_FILES = 100

EXECUTION_TIMEOUT = 10
MAX_OUTPUT = 20000


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

logger = logging.getLogger(
    "engineer-ai"
)


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"]
)


# ============================================================
# ANALYTICS
# ============================================================

analytics = {
    "requests": 0,
    "successful": 0,
    "failed": 0,
    "chat_requests": 0,
    "agent_requests": 0,
    "review_requests": 0,
    "execution_requests": 0,
    "test_requests": 0,
    "debug_requests": 0,
    "security_requests": 0,
    "autofix_requests": 0,
    "benchmark_requests": 0,
    "project_requests": 0,
    "terminal_requests": 0,
    "execution_successes": 0,
    "execution_failures": 0,
    "total_latency": 0.0
}


# ============================================================
# REQUEST MODELS
# ============================================================

class ChatRequest(BaseModel):
    message: Optional[str] = None
    prompt: Optional[str] = None
    model: Optional[str] = None


class ExplainRequest(BaseModel):
    prompt: str = Field(
        min_length=1,
        max_length=MAX_PROMPT_LENGTH
    )


class ProjectFile(BaseModel):
    path: str
    content: str = Field(
        max_length=MAX_FILE_SIZE
    )


class ProjectRequest(BaseModel):
    project_name: str = "Engineer Project"
    task: str
    files: List[ProjectFile]


class AgentRequest(BaseModel):
    task: str
    include_tests: bool = True
    include_security: bool = True
    include_performance: bool = False


class ReviewRequest(BaseModel):
    code: str
    language: str = "python"
    focus: str = "general"


class ExecuteRequest(BaseModel):
    files: List[ProjectFile]
    entrypoint: str = "main.py"
    language: Optional[str] = None
    command: Optional[List[str]] = None
    timeout_seconds: int = EXECUTION_TIMEOUT


class TestRequest(BaseModel):
    files: List[ProjectFile]
    language: Optional[str] = None
    command: Optional[List[str]] = None
    timeout_seconds: int = EXECUTION_TIMEOUT


# ============================================================
# BASIC HELPERS
# ============================================================

def record_request():
    analytics["requests"] += 1


def record_success():
    analytics["successful"] += 1


def record_failure():
    analytics["failed"] += 1


def finish_timer(start):
    analytics["total_latency"] += (
        time.time() - start
    )


def trim_text(
    text: Any,
    limit: int = MAX_OUTPUT
) -> str:

    value = str(text)

    if len(value) <= limit:
        return value

    return (
        value[:limit]
        + "\n...[truncated]"
    )


def model_dict(model):
    if hasattr(model, "model_dump"):
        return model.model_dump()

    return model.dict()


# ============================================================
# AI CLIENT
# ============================================================

_ai_client = None


def get_ai_client():

    global _ai_client

    if _ai_client is None:

        if not GEMINI_API_KEY:
            raise RuntimeError(
                "GEMINI_API_KEY is not configured."
            )

        _ai_client = genai.Client(
            api_key=GEMINI_API_KEY
        )

    return _ai_client


SYSTEM_INSTRUCTION = (
    "You are Engineer AI, an advanced "
    "software engineering assistant. "
    "Prioritize correctness, security, "
    "reliability, maintainability and "
    "performance. Never claim that code "
    "was executed unless the execution "
    "system actually executed it."
)


def generate_ai(
    prompt: str,
    model: Optional[str] = None
) -> str:

    client = get_ai_client()

    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION
    )

    result = client.models.generate_content(
        model=model or GEMINI_MODEL,
        contents=trim_text(
            prompt,
            MAX_PROMPT_LENGTH
        ),
        config=config
    )

    text = getattr(
        result,
        "text",
        None
    )

    if not text:
        raise RuntimeError(
            "AI returned an empty response."
        )

    return trim_text(text)


async def ask_ai(
    prompt: str,
    model: Optional[str] = None
) -> str:

    return await asyncio.to_thread(
        generate_ai,
        prompt,
        model
    )


# ============================================================
# HEALTH
# ============================================================

@app.get("/api/health")
async def health():

    return {
        "status": "ok",
        "service": APP_NAME,
        "version": APP_VERSION,
        "model": GEMINI_MODEL,
        "gemini_configured": bool(
            GEMINI_API_KEY
        ),
        "execution_mode": EXECUTION_MODE,
        "docker_available": bool(
            shutil.which("docker")
        )
}
    # ============================================================
# PART 2 - PROJECT FILE SAFETY + INTELLIGENCE
# ============================================================

ALLOWED_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".json",
    ".html",
    ".css",
    ".md",
    ".txt",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".sql",
    ".sh"
}


BLOCKED_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "id_rsa",
    "id_ed25519",
    "credentials.json",
    "secrets.json"
}


def safe_path(path: str) -> str:

    if not path:
        raise ValueError(
            "File path cannot be empty."
        )

    path = path.replace(
        "\\",
        "/"
    ).strip()

    if "\x00" in path:
        raise ValueError(
            "Invalid null character."
        )

    if path.startswith("/"):
        raise ValueError(
            "Absolute paths are not allowed."
        )

    parts = Path(path).parts

    if ".." in parts:
        raise ValueError(
            "Path traversal is not allowed."
        )

    filename = Path(path).name.lower()

    if filename in BLOCKED_NAMES:
        raise ValueError(
            "Sensitive files are not allowed."
        )

    if filename.endswith(
        (".pem", ".key", ".p12", ".pfx")
    ):
        raise ValueError(
            "Private key files are not allowed."
        )

    return path


def validate_files(
    files: List[ProjectFile]
) -> List[ProjectFile]:

    if not files:
        raise ValueError(
            "At least one project file is required."
        )

    if len(files) > MAX_PROJECT_FILES:
        raise ValueError(
            "Too many project files."
        )

    total_size = 0
    seen = set()
    result = []

    for item in files:

        path = safe_path(
            item.path
        )

        if path in seen:
            raise ValueError(
                f"Duplicate file: {path}"
            )

        seen.add(path)

        suffix = Path(
            path
        ).suffix.lower()

        if (
            suffix
            and suffix not in ALLOWED_EXTENSIONS
        ):
            raise ValueError(
                f"Unsupported file type: {suffix}"
            )

        content_size = len(
            item.content.encode(
                "utf-8",
                errors="ignore"
            )
        )

        if content_size > MAX_FILE_SIZE:
            raise ValueError(
                f"File too large: {path}"
            )

        total_size += content_size

        result.append(
            ProjectFile(
                path=path,
                content=item.content
            )
        )

    if total_size > MAX_PROJECT_SIZE:
        raise ValueError(
            "Project is too large."
        )

    return result


# ============================================================
# LANGUAGE DETECTION
# ============================================================

LANGUAGE_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".html": "html",
    ".css": "css",
    ".json": "json",
    ".sql": "sql",
    ".sh": "shell",
    ".md": "markdown"
}


def detect_language(
    filename: str
) -> str:

    suffix = Path(
        filename
    ).suffix.lower()

    return LANGUAGE_MAP.get(
        suffix,
        "text"
    )


# ============================================================
# PROJECT STATISTICS
# ============================================================

def get_project_stats(
    files: List[ProjectFile]
) -> Dict[str, Any]:

    languages = {}
    total_lines = 0
    total_bytes = 0

    for item in files:

        language = detect_language(
            item.path
        )

        languages[language] = (
            languages.get(
                language,
                0
            ) + 1
        )

        total_lines += (
            item.content.count("\n")
            + 1
        )

        total_bytes += len(
            item.content.encode(
                "utf-8",
                errors="ignore"
            )
        )

    return {
        "files": len(files),
        "lines": total_lines,
        "bytes": total_bytes,
        "languages": languages
    }


# ============================================================
# PROJECT CONTEXT
# ============================================================

def build_context(
    files: List[ProjectFile]
) -> str:

    sections = []

    for item in files:

        sections.append(
            "===== FILE: "
            + item.path
            + " =====\n"
            + item.content
        )

    return trim_text(
        "\n\n".join(sections),
        MAX_PROJECT_SIZE
    )


# ============================================================
# PROJECT OVERVIEW
# ============================================================

async def generate_project_overview(
    project_name: str,
    task: str,
    files: List[ProjectFile]
) -> str:

    stats = get_project_stats(
        files
    )

    context = build_context(
        files
    )

    prompt = (
        "Analyze this software project.\n\n"
        "PROJECT NAME:\n"
        + project_name
        + "\n\n"
        "TASK:\n"
        + task
        + "\n\n"
        "STATISTICS:\n"
        + json.dumps(
            stats,
            indent=2
        )
        + "\n\n"
        "FILES:\n"
        + context
        + "\n\n"
        "Explain:\n"
        "1. Architecture\n"
        "2. Main components\n"
        "3. Potential bugs\n"
        "4. Security concerns\n"
        "5. Performance concerns\n"
        "6. Testing strategy\n"
        "7. Recommended improvements"
    )

    return await ask_ai(
        prompt
    )


# ============================================================
# PROJECT ANALYSIS API
# ============================================================

@app.post("/api/project/analyze")
async def project_analyze(
    request: ProjectRequest
):

    start = time.time()

    record_request()
    analytics["project_requests"] += 1

    try:

        files = validate_files(
            request.files
        )

        stats = get_project_stats(
            files
        )

        overview = (
            await generate_project_overview(
                request.project_name,
                request.task,
                files
            )
        )

        record_success()

        return {
            "status": "success",
            "project_name":
                request.project_name,
            "statistics": stats,
            "analysis": overview
        }

    except ValueError as error:

        record_failure()

        raise HTTPException(
            status_code=400,
            detail=str(error)
        )

    except Exception as error:

        record_failure()

        raise HTTPException(
            status_code=500,
            detail=str(error)
        )

    finally:

        finish_timer(start)


# ============================================================
# PROJECT STATS API
# ============================================================

@app.post("/api/project/stats")
async def project_statistics(
    request: ProjectRequest
):

    try:

        files = validate_files(
            request.files
        )

        stats = get_project_stats(
            files
        )

        file_details = []

        for item in files:

            file_details.append({
                "path": item.path,
                "language":
                    detect_language(
                        item.path
                    ),
                "lines":
                    item.content.count(
                        "\n"
                    ) + 1,
                "bytes":
                    len(
                        item.content.encode(
                            "utf-8",
                            errors="ignore"
                        )
                    )
            })

        return {
            "status": "success",
            "statistics": stats,
            "files": file_details
        }

    except ValueError as error:

        raise HTTPException(
            status_code=400,
            detail=str(error)
        )


# ============================================================
# PROJECT CONTEXT API
# ============================================================

@app.post("/api/project/context")
async def project_context(
    request: ProjectRequest
):

    try:

        files = validate_files(
            request.files
        )

        return {
            "status": "success",
            "project_name":
                request.project_name,
            "context":
                build_context(files),
            "statistics":
                get_project_stats(files)
        }

    except ValueError as error:

        raise HTTPException(
            status_code=400,
            detail=str(error)
    )
        # ============================================================
# PART 3 - AGENT SYSTEM
# ============================================================

AGENT_ROLES = {
    "planner": (
        "You are the Planning Agent. "
        "Break engineering requirements into "
        "clear implementation steps."
    ),

    "coder": (
        "You are the Coding Agent. "
        "Design clean, correct and maintainable "
        "code solutions."
    ),

    "debugger": (
        "You are the Debugging Agent. "
        "Find root causes, bugs and edge cases."
    ),

    "tester": (
        "You are the Testing Agent. "
        "Design unit, integration and edge tests."
    ),

    "security": (
        "You are the Security Agent. "
        "Look for vulnerabilities, unsafe input, "
        "secrets and data exposure."
    ),

    "performance": (
        "You are the Performance Agent. "
        "Look for slow algorithms, unnecessary "
        "work and scalability problems."
    ),

    "research": (
        "You are the Research Agent. "
        "Compare practical engineering approaches."
    ),

    "documentation": (
        "You are the Documentation Agent. "
        "Explain architecture, APIs and usage."
    )
}


async def run_single_agent(
    role: str,
    task: str,
    context: str = ""
) -> str:

    instruction = AGENT_ROLES.get(
        role,
        AGENT_ROLES["research"]
    )

    prompt = (
        instruction
        + "\n\nTASK:\n"
        + task
        + "\n\nPROJECT CONTEXT:\n"
        + trim_text(
            context,
            MAX_PROJECT_SIZE
        )
        + "\n\n"
        + "Return a useful engineering result."
    )

    return await ask_ai(
        prompt
    )


async def run_agent_workflow(
    task: str,
    context: str = "",
    include_tests: bool = True,
    include_security: bool = True,
    include_performance: bool = False
) -> Dict[str, Any]:

    roles = [
        "planner",
        "coder",
        "debugger"
    ]

    if include_tests:
        roles.append("tester")

    if include_security:
        roles.append("security")

    if include_performance:
        roles.append("performance")

    workflow = []

    for role in roles:

        started = time.time()

        try:

            output = await run_single_agent(
                role,
                task,
                context
            )

            workflow.append({
                "agent": role,
                "status": "completed",
                "output": output,
                "latency_seconds": round(
                    time.time() - started,
                    3
                )
            })

        except Exception as error:

            workflow.append({
                "agent": role,
                "status": "failed",
                "output": "",
                "error": str(error)
            })

    successful = [
        item
        for item in workflow
        if item["status"] == "completed"
    ]

    combined = "\n\n".join(
        "AGENT: "
        + item["agent"]
        + "\n"
        + item["output"]
        for item in successful
    )

    final_prompt = (
        "You are the Lead Engineer.\n\n"
        "TASK:\n"
        + task
        + "\n\n"
        "PROJECT CONTEXT:\n"
        + trim_text(
            context,
            MAX_PROJECT_SIZE
        )
        + "\n\n"
        "SPECIALIST RESULTS:\n"
        + trim_text(
            combined,
            MAX_OUTPUT
        )
        + "\n\n"
        "Create a final engineering plan.\n"
        "Include:\n"
        "1. Summary\n"
        "2. Implementation\n"
        "3. Bugs and risks\n"
        "4. Testing\n"
        "5. Security\n"
        "6. Performance\n"
        "7. Next steps"
    )

    final_output = await ask_ai(
        final_prompt
    )

    return {
        "status": "completed",
        "workflow": workflow,
        "final": final_output,
        "agent_count": len(workflow),
        "successful_agents":
            len(successful)
    }


# ============================================================
# MULTI-AGENT API
# ============================================================

@app.post("/api/agent/execute")
async def execute_agents(
    request: AgentRequest
):

    start = time.time()

    record_request()
    analytics["agent_requests"] += 1

    if not request.task.strip():

        raise HTTPException(
            status_code=400,
            detail="Task is required."
        )

    try:

        result = await run_agent_workflow(
            task=request.task,
            include_tests=request.include_tests,
            include_security=request.include_security,
            include_performance=
                request.include_performance
        )

        record_success()

        return {
            "status": "success",
            "task": request.task,
            "workflow":
                result["workflow"],
            "final":
                result["final"],
            "agent_count":
                result["agent_count"],
            "successful_agents":
                result["successful_agents"],
            "latency_seconds":
                round(
                    time.time() - start,
                    3
                )
        }

    except Exception as error:

        record_failure()

        raise HTTPException(
            status_code=500,
            detail=str(error)
        )

    finally:

        finish_timer(start)


# ============================================================
# CODE REVIEW
# ============================================================

@app.post("/api/code/review")
async def code_review(
    request: ReviewRequest
):

    start = time.time()

    record_request()
    analytics["review_requests"] += 1

    prompt = (
        "Perform a professional code review.\n\n"
        "LANGUAGE:\n"
        + request.language
        + "\n\n"
        "FOCUS:\n"
        + request.focus
        + "\n\n"
        "CODE:\n"
        + request.code
        + "\n\n"
        "Review for:\n"
        "1. Bugs\n"
        "2. Security\n"
        "3. Performance\n"
        "4. Maintainability\n"
        "5. Error handling\n"
        "6. Recommended improvements"
    )

    try:

        output = await ask_ai(
            prompt
        )

        record_success()

        return {
            "status": "success",
            "review": {
                "output": output,
                "language":
                    request.language,
                "focus":
                    request.focus
            },
            "latency_seconds":
                round(
                    time.time() - start,
                    3
                )
        }

    except Exception as error:

        record_failure()

        raise HTTPException(
            status_code=500,
            detail=str(error)
        )

    finally:

        finish_timer(start)


# ============================================================
# AGENT QUICK ACTION
# ============================================================

@app.post("/api/agent/research")
async def research_agent(
    request: AgentRequest
):

    record_request()
    analytics["agent_requests"] += 1

    try:

        output = await run_single_agent(
            "research",
            request.task
        )

        record_success()

        return {
            "status": "success",
            "agent": "research",
            "output": output
        }

    except Exception as error:

        record_failure()

        raise HTTPException(
            status_code=500,
            detail=str(error)
        )


@app.post("/api/agent/document")
async def documentation_agent(
    request: AgentRequest
):

    record_request()
    analytics["agent_requests"] += 1

    try:

        output = await run_single_agent(
            "documentation",
            request.task
        )

        record_success()

        return {
            "status": "success",
            "agent": "documentation",
            "output": output
        }

    except Exception as error:

        record_failure()

        raise HTTPException(
            status_code=500,
            detail=str(error)
        )
        # ============================================================
# PART 4 - SQLITE DATABASE
# ============================================================

def db_connect():

    connection = sqlite3.connect(
        DATABASE_PATH,
        timeout=10
    )

    connection.row_factory = sqlite3.Row

    return connection


def initialize_database():

    connection = db_connect()

    try:

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                task TEXT NOT NULL,
                files_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS memory (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                memory_key TEXT NOT NULL,
                memory_value TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_memory_project
            ON memory(project_id)
            """
        )

        connection.commit()

    finally:

        connection.close()


def save_project(
    project_id: str,
    name: str,
    task: str,
    files: List[ProjectFile]
):

    now = time.time()

    connection = db_connect()

    try:

        connection.execute(
            """
            INSERT OR REPLACE INTO projects
            (
                id,
                name,
                task,
                files_json,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                name,
                task,
                json.dumps([
                    model_dict(file)
                    for file in files
                ]),
                now,
                now
            )
        )

        connection.commit()

    finally:

        connection.close()


def load_project(
    project_id: str
):

    connection = db_connect()

    try:

        row = connection.execute(
            """
            SELECT *
            FROM projects
            WHERE id = ?
            """,
            (project_id,)
        ).fetchone()

        if row is None:
            return None

        return {
            "id": row["id"],
            "name": row["name"],
            "task": row["task"],
            "files": json.loads(
                row["files_json"]
            ),
            "created_at":
                row["created_at"],
            "updated_at":
                row["updated_at"]
        }

    finally:

        connection.close()


# ============================================================
# PROJECT CREATION
# ============================================================

class CreateProjectRequest(BaseModel):
    name: str = "New Project"
    task: str = ""
    files: List[ProjectFile] = []


@app.post("/api/projects")
async def create_project(
    request: CreateProjectRequest
):

    try:

        files = validate_files(
            request.files
        )

        project_id = uuid.uuid4().hex

        save_project(
            project_id,
            request.name,
            request.task,
            files
        )

        return {
            "status": "success",
            "project": {
                "id": project_id,
                "name": request.name,
                "task": request.task,
                "files": [
                    model_dict(file)
                    for file in files
                ]
            }
        }

    except ValueError as error:

        raise HTTPException(
            status_code=400,
            detail=str(error)
        )


# ============================================================
# PROJECT LIST
# ============================================================

@app.get("/api/projects")
async def list_projects():

    connection = db_connect()

    try:

        rows = connection.execute(
            """
            SELECT
                id,
                name,
                task,
                created_at,
                updated_at
            FROM projects
            ORDER BY updated_at DESC
            """
        ).fetchall()

        return {
            "status": "success",
            "projects": [
                dict(row)
                for row in rows
            ]
        }

    finally:

        connection.close()


# ============================================================
# GET PROJECT
# ============================================================

@app.get("/api/projects/{project_id}")
async def get_project(
    project_id: str
):

    project = load_project(
        project_id
    )

    if project is None:

        raise HTTPException(
            status_code=404,
            detail="Project not found."
        )

    return {
        "status": "success",
        "project": project
    }


# ============================================================
# UPDATE PROJECT
# ============================================================

@app.put("/api/projects/{project_id}")
async def update_project(
    project_id: str,
    request: CreateProjectRequest
):

    existing = load_project(
        project_id
    )

    if existing is None:

        raise HTTPException(
            status_code=404,
            detail="Project not found."
        )

    try:

        files = validate_files(
            request.files
        )

        save_project(
            project_id,
            request.name,
            request.task,
            files
        )

        return {
            "status": "success",
            "project":
                load_project(
                    project_id
                )
        }

    except ValueError as error:

        raise HTTPException(
            status_code=400,
            detail=str(error)
        )


# ============================================================
# DELETE PROJECT
# ============================================================

@app.delete("/api/projects/{project_id}")
async def delete_project(
    project_id: str
):

    connection = db_connect()

    try:

        result = connection.execute(
            """
            DELETE FROM projects
            WHERE id = ?
            """,
            (project_id,)
        )

        connection.execute(
            """
            DELETE FROM memory
            WHERE project_id = ?
            """,
            (project_id,)
        )

        connection.commit()

        if result.rowcount == 0:

            raise HTTPException(
                status_code=404,
                detail="Project not found."
            )

        return {
            "status": "success",
            "deleted": project_id
        }

    finally:

        connection.close()


# ============================================================
# DATABASE STATUS
# ============================================================

@app.get("/api/database")
async def database_status():

    connection = db_connect()

    try:

        project_count = connection.execute(
            "SELECT COUNT(*) FROM projects"
        ).fetchone()[0]

        memory_count = connection.execute(
            "SELECT COUNT(*) FROM memory"
        ).fetchone()[0]

        return {
            "status": "success",
            "database": DATABASE_PATH,
            "projects":
                project_count,
            "memory_items":
                memory_count
        }

    finally:

        connection.close()
        # ============================================================
# PART 5 - PROJECT MEMORY + MASTER WORKFLOW
# ============================================================


class MemorySaveRequest(BaseModel):
    project_id: str
    key: str
    value: str


class MemoryAskRequest(BaseModel):
    project_id: str
    question: str


# ============================================================
# SAVE MEMORY
# ============================================================

@app.post("/api/project/memory/save")
async def save_memory(
    request: MemorySaveRequest
):

    project = load_project(
        request.project_id
    )

    if project is None:

        raise HTTPException(
            status_code=404,
            detail="Project not found."
        )

    memory_id = uuid.uuid4().hex
    now = time.time()

    connection = db_connect()

    try:

        connection.execute(
            """
            INSERT INTO memory
            (
                id,
                project_id,
                memory_key,
                memory_value,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                memory_id,
                request.project_id,
                request.key,
                request.value,
                now,
                now
            )
        )

        connection.commit()

    finally:

        connection.close()

    return {
        "status": "success",
        "memory_id": memory_id
    }


# ============================================================
# GET MEMORY
# ============================================================

@app.get("/api/project/memory/{project_id}")
async def get_memory(
    project_id: str
):

    connection = db_connect()

    try:

        rows = connection.execute(
            """
            SELECT
                id,
                memory_key,
                memory_value,
                created_at,
                updated_at
            FROM memory
            WHERE project_id = ?
            ORDER BY updated_at DESC
            """,
            (project_id,)
        ).fetchall()

        return {
            "status": "success",
            "memory": [
                dict(row)
                for row in rows
            ]
        }

    finally:

        connection.close()


# ============================================================
# DELETE MEMORY
# ============================================================

@app.delete("/api/project/memory/{memory_id}")
async def delete_memory(
    memory_id: str
):

    connection = db_connect()

    try:

        result = connection.execute(
            """
            DELETE FROM memory
            WHERE id = ?
            """,
            (memory_id,)
        )

        connection.commit()

        return {
            "status": "success",
            "deleted":
                result.rowcount > 0
        }

    finally:

        connection.close()


# ============================================================
# ASK USING PROJECT MEMORY
# ============================================================

@app.post("/api/project/memory/ask")
async def ask_memory(
    request: MemoryAskRequest
):

    connection = db_connect()

    try:

        rows = connection.execute(
            """
            SELECT
                memory_key,
                memory_value
            FROM memory
            WHERE project_id = ?
            ORDER BY updated_at DESC
            LIMIT 50
            """,
            (request.project_id,)
        ).fetchall()

    finally:

        connection.close()

    memory_text = "\n".join(
        row["memory_key"]
        + ": "
        + row["memory_value"]
        for row in rows
    )

    prompt = (
        "Answer the engineering question "
        "using the project's saved memory.\n\n"
        "PROJECT MEMORY:\n"
        + memory_text
        + "\n\nQUESTION:\n"
        + request.question
    )

    try:

        answer = await ask_ai(
            prompt
        )

        return {
            "status": "success",
            "answer": answer,
            "memory_items":
                len(rows)
        }

    except Exception as error:

        raise HTTPException(
            status_code=500,
            detail=str(error)
        )


# ============================================================
# MASTER PROJECT REQUEST
# ============================================================

class MasterProjectRequest(BaseModel):
    project_name: str = "Engineer Project"
    task: str
    files: List[ProjectFile]
    include_tests: bool = True
    include_security: bool = True
    include_performance: bool = False


# ============================================================
# MASTER PROJECT WORKFLOW
# ============================================================

@app.post("/api/project/run")
async def run_project(
    request: MasterProjectRequest
):

    start = time.time()

    record_request()
    analytics["project_requests"] += 1

    try:

        files = validate_files(
            request.files
        )

        project_id = uuid.uuid4().hex

        save_project(
            project_id,
            request.project_name,
            request.task,
            files
        )

        stats = get_project_stats(
            files
        )

        context = build_context(
            files
        )

        workflow = await run_agent_workflow(
            task=request.task,
            context=context,
            include_tests=
                request.include_tests,
            include_security=
                request.include_security,
            include_performance=
                request.include_performance
        )

        record_success()

        return {
            "status": "success",
            "project_id":
                project_id,
            "project_name":
                request.project_name,
            "statistics":
                stats,
            "workflow":
                workflow["workflow"],
            "final":
                workflow["final"],
            "agent_count":
                workflow["agent_count"],
            "latency_seconds":
                round(
                    time.time() - start,
                    3
                )
        }

    except ValueError as error:

        record_failure()

        raise HTTPException(
            status_code=400,
            detail=str(error)
        )

    except Exception as error:

        record_failure()

        raise HTTPException(
            status_code=500,
            detail=str(error)
        )

    finally:

        finish_timer(start)


# ============================================================
# QUICK PROJECT QUESTION
# ============================================================

class ProjectQuestionRequest(BaseModel):
    project_id: str
    question: str


@app.post("/api/project/ask")
async def project_ask(
    request: ProjectQuestionRequest
):

    project = load_project(
        request.project_id
    )

    if project is None:

        raise HTTPException(
            status_code=404,
            detail="Project not found."
        )

    files = [
        ProjectFile(**item)
        for item in project["files"]
    ]

    context = build_context(
        files
    )

    prompt = (
        "Answer this question about the "
        "software project.\n\n"
        "PROJECT:\n"
        + context
        + "\n\nQUESTION:\n"
        + request.question
    )

    try:

        answer = await ask_ai(
            prompt
        )

        return {
            "status": "success",
            "project_id":
                request.project_id,
            "answer": answer
        }

    except Exception as error:

        raise HTTPException(
            status_code=500,
            detail=str(error)
    )
        # ============================================================
# PART 6 - SAFE CODE EXECUTION ENGINE
# ============================================================


SAFE_EXECUTABLES = {
    "python": "python",
    "python3": "python3",
    "node": "node",
    "npm": "npm"
}


def validate_execution_files(
    files: List[ProjectFile]
):
    validated = []

    for file in files:

        path = safe_path(
            file.path
        )

        validated.append(
            ProjectFile(
                path=path,
                content=file.content
            )
        )

    return validated


def create_workspace(
    files: List[ProjectFile]
):

    workspace = tempfile.mkdtemp(
        prefix="engineer_ai_"
    )

    try:

        for file in files:

            path = safe_path(
                file.path
            )

            full_path = os.path.join(
                workspace,
                path
            )

            folder = os.path.dirname(
                full_path
            )

            if folder:
                os.makedirs(
                    folder,
                    exist_ok=True
                )

            with open(
                full_path,
                "w",
                encoding="utf-8"
            ) as output:

                output.write(
                    file.content
                )

        return workspace

    except Exception:

        shutil.rmtree(
            workspace,
            ignore_errors=True
        )

        raise


def get_entrypoint(
    files: List[ProjectFile]
):

    preferred = [
        "main.py",
        "app.py",
        "server.py",
        "index.js",
        "main.js",
        "app.js"
    ]

    paths = {
        file.path
        for file in files
    }

    for name in preferred:

        if name in paths:
            return name

    if files:
        return files[0].path

    return None


def detect_runtime(
    entrypoint: str
):

    extension = os.path.splitext(
        entrypoint
    )[1].lower()

    if extension == ".py":
        return "python"

    if extension in {
        ".js",
        ".mjs",
        ".cjs"
    }:
        return "node"

    return None


def validate_command(
    command: List[str]
):

    if not command:
        raise ValueError(
            "Execution command cannot be empty."
        )

    executable = command[0]

    if executable not in SAFE_EXECUTABLES:

        raise ValueError(
            "Executable is not allowed."
        )

    if len(command) > 20:

        raise ValueError(
            "Command has too many arguments."
        )

    for argument in command:

        if "\x00" in argument:
            raise ValueError(
                "Invalid command argument."
            )

        if len(argument) > 500:
            raise ValueError(
                "Command argument is too long."
            )

        blocked = [
            ";",
            "&&",
            "||",
            "|",
            ">",
            "<",
            "`",
            "$("
        ]

        if any(
            item in argument
            for item in blocked
        ):

            raise ValueError(
                "Unsafe shell syntax detected."
            )

    return True


def build_execution_command(
    files: List[ProjectFile],
    entrypoint: str = None
):

    if not entrypoint:

        entrypoint = get_entrypoint(
            files
        )

    if not entrypoint:

        raise ValueError(
            "No entrypoint found."
        )

    runtime = detect_runtime(
        entrypoint
    )

    if runtime == "python":

        command = [
            "python",
            entrypoint
        ]

    elif runtime == "node":

        command = [
            "node",
            entrypoint
        ]

    else:

        raise ValueError(
            "Unsupported project runtime."
        )

    validate_command(
        command
    )

    return command


def normalize_execution_result(
    result,
    started_at
):

    stdout = trim_text(
        result.get("stdout", "")
    )

    stderr = trim_text(
        result.get("stderr", "")
    )

    return {
        "status":
            result.get(
                "status",
                "unknown"
            ),
        "stdout":
            stdout,
        "stderr":
            stderr,
        "return_code":
            result.get(
                "return_code"
            ),
        "timed_out":
            bool(
                result.get(
                    "timed_out",
                    False
                )
            ),
        "latency_seconds":
            round(
                time.time() - started_at,
                3
            )
    }


async def execute_host_command(
    command: List[str],
    workspace: str,
    timeout: int
):

    if os.getenv(
        "ALLOW_UNSANDBOXED_EXECUTION",
        "false"
    ).lower() != "true":

        raise RuntimeError(
            "Host execution is disabled."
        )

    validate_command(
        command
    )

    started_at = time.time()

    try:

        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        try:

            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout
            )

            result = {
                "status":
                    "success"
                    if process.returncode == 0
                    else "failed",
                "stdout":
                    stdout.decode(
                        "utf-8",
                        errors="replace"
                    ),
                "stderr":
                    stderr.decode(
                        "utf-8",
                        errors="replace"
                    ),
                "return_code":
                    process.returncode,
                "timed_out":
                    False
            }

        except asyncio.TimeoutError:

            process.kill()

            await process.communicate()

            result = {
                "status": "timeout",
                "stdout": "",
                "stderr":
                    "Execution timed out.",
                "return_code": None,
                "timed_out": True
            }

        return normalize_execution_result(
            result,
            started_at
        )

    except Exception as error:

        return normalize_execution_result(
            {
                "status": "error",
                "stdout": "",
                "stderr": str(error),
                "return_code": None,
                "timed_out": False
            },
            started_at
        )
        # ============================================================
# PART 7 - DOCKER SANDBOX + EXECUTION API
# ============================================================


def docker_available():
    return shutil.which("docker") is not None


def docker_command(
    command: List[str],
    workspace: str,
    timeout: int
):

    if not docker_available():

        raise RuntimeError(
            "Docker is not available on this server."
        )

    validate_command(
        command
    )

    if not os.path.isdir(workspace):

        raise ValueError(
            "Execution workspace does not exist."
        )

    return [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--cpus",
        "0.5",
        "--memory",
        "256m",
        "--pids-limit",
        "64",
        "--read-only",
        "--security-opt",
        "no-new-privileges",
        "--cap-drop",
        "ALL",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=64m",
        "-v",
        workspace + ":/workspace:ro",
        "-w",
        "/workspace",
        "python:3.12-slim",
        *command
    ]


async def execute_docker_command(
    command: List[str],
    workspace: str,
    timeout: int
):

    started_at = time.time()

    try:

        docker_args = docker_command(
            command,
            workspace,
            timeout
        )

        process = await asyncio.create_subprocess_exec(
            *docker_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        try:

            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout
            )

            result = {
                "status":
                    "success"
                    if process.returncode == 0
                    else "failed",
                "stdout":
                    stdout.decode(
                        "utf-8",
                        errors="replace"
                    ),
                "stderr":
                    stderr.decode(
                        "utf-8",
                        errors="replace"
                    ),
                "return_code":
                    process.returncode,
                "timed_out":
                    False
            }

        except asyncio.TimeoutError:

            process.kill()

            await process.communicate()

            result = {
                "status":
                    "timeout",
                "stdout":
                    "",
                "stderr":
                    "Execution timed out.",
                "return_code":
                    None,
                "timed_out":
                    True
            }

        return normalize_execution_result(
            result,
            started_at
        )

    except Exception as error:

        return normalize_execution_result(
            {
                "status":
                    "error",
                "stdout":
                    "",
                "stderr":
                    str(error),
                "return_code":
                    None,
                "timed_out":
                    False
            },
            started_at
        )


async def execute_code(
    files: List[ProjectFile],
    command: List[str],
    timeout: int
):

    timeout = max(
        1,
        min(
            timeout,
            EXECUTION_TIMEOUT
        )
    )

    validated = validate_execution_files(
        files
    )

    workspace = create_workspace(
        validated
    )

    try:

        if EXECUTION_MODE == "docker":

            return await execute_docker_command(
                command,
                workspace,
                timeout
            )

        if EXECUTION_MODE == "host":

            return await execute_host_command(
                command,
                workspace,
                timeout
            )

        raise RuntimeError(
            "Unknown execution mode."
        )

    finally:

        shutil.rmtree(
            workspace,
            ignore_errors=True
        )


# ============================================================
# CODE EXECUTION ENDPOINT
# ============================================================


@app.post("/api/execute")
async def execute_endpoint(
    request: ExecuteRequest
):

    start = time.time()

    record_request()

    try:

        files = validate_execution_files(
            request.files
        )

        command = request.command

        if not command:

            command = build_execution_command(
                files,
                request.entrypoint
            )

        validate_command(
            command
        )

        result = await execute_code(
            files,
            command,
            request.timeout
        )

        if result["status"] == "success":
            record_success()
        else:
            record_failure()

        return {
            "status":
                result["status"],
            "command":
                command,
            "stdout":
                result["stdout"],
            "stderr":
                result["stderr"],
            "return_code":
                result["return_code"],
            "timed_out":
                result["timed_out"],
            "latency_seconds":
                result["latency_seconds"]
        }

    except ValueError as error:

        record_failure()

        raise HTTPException(
            status_code=400,
            detail=str(error)
        )

    except RuntimeError as error:

        record_failure()

        raise HTTPException(
            status_code=503,
            detail=str(error)
        )

    except Exception as error:

        record_failure()

        raise HTTPException(
            status_code=500,
            detail=str(error)
        )

    finally:

        finish_timer(start)


# ============================================================
# EXECUTION HEALTH
# ============================================================


@app.get("/api/execute/status")
async def execution_status():

    return {
        "execution_mode":
            EXECUTION_MODE,
        "docker_available":
            docker_available(),
        "host_execution_allowed":
            os.getenv(
                "ALLOW_UNSANDBOXED_EXECUTION",
                "false"
            ).lower() == "true",
        "supported_runtimes": [
            "python",
            "node"
        ]
            }
    # ============================================================
# PART 8 - TESTING + SAFE TERMINAL
# ============================================================


def build_test_command(
    files: List[ProjectFile],
    entrypoint: str = None
):

    paths = {
        file.path
        for file in files
    }

    has_python = any(
        path.endswith(".py")
        for path in paths
    )

    has_node = any(
        path.endswith(
            (".js", ".mjs", ".cjs")
        )
        for path in paths
    )

    if has_python:

        return [
            "python",
            "-m",
            "unittest",
            "discover",
            "-v"
        ]

    if has_node:

        return [
            "npm",
            "test"
        ]

    raise ValueError(
        "No supported test runtime found."
    )


# ============================================================
# TEST REQUEST
# ============================================================


class TestRunRequest(BaseModel):
    files: List[ProjectFile]
    timeout: int = 20


# ============================================================
# TEST ENDPOINT
# ============================================================


@app.post("/api/test/run")
async def run_tests(
    request: TestRunRequest
):

    start = time.time()

    record_request()

    try:

        files = validate_execution_files(
            request.files
        )

        command = build_test_command(
            files
        )

        result = await execute_code(
            files,
            command,
            request.timeout
        )

        if result["status"] == "success":
            record_success()
        else:
            record_failure()

        return {
            "status":
                result["status"],
            "command":
                command,
            "stdout":
                result["stdout"],
            "stderr":
                result["stderr"],
            "return_code":
                result["return_code"],
            "timed_out":
                result["timed_out"],
            "latency_seconds":
                result["latency_seconds"]
        }

    except ValueError as error:

        record_failure()

        raise HTTPException(
            status_code=400,
            detail=str(error)
        )

    except RuntimeError as error:

        record_failure()

        raise HTTPException(
            status_code=503,
            detail=str(error)
        )

    except Exception as error:

        record_failure()

        raise HTTPException(
            status_code=500,
            detail=str(error)
        )

    finally:

        finish_timer(start)


# ============================================================
# SAFE TERMINAL
# ============================================================


class TerminalRequest(BaseModel):
    files: List[ProjectFile] = []
    command: str
    timeout: int = 10


SAFE_TERMINAL_COMMANDS = {
    "help",
    "pwd",
    "ls",
    "python",
    "python3",
    "node",
    "npm"
}


def parse_terminal_command(
    command_text: str
):

    command_text = command_text.strip()

    if not command_text:

        raise ValueError(
            "Terminal command cannot be empty."
        )

    parts = command_text.split()

    executable = parts[0]

    if executable not in SAFE_TERMINAL_COMMANDS:

        raise ValueError(
            "Terminal command is not allowed."
        )

    return parts


def terminal_help():

    return {
        "status": "success",
        "output": (
            "Safe Engineer AI Terminal\n"
            "\n"
            "Available commands:\n"
            "help\n"
            "pwd\n"
            "ls\n"
            "python <file.py>\n"
            "python3 <file.py>\n"
            "node <file.js>\n"
            "npm test\n"
        )
    }


def terminal_virtual_command(
    parts: List[str],
    workspace: str
):

    command = parts[0]

    if command == "help":
        return terminal_help()

    if command == "pwd":

        return {
            "status": "success",
            "output": "/workspace"
        }

    if command == "ls":

        entries = sorted(
            os.listdir(workspace)
        )

        output = "\n".join(
            entries
        )

        return {
            "status": "success",
            "output": output
        }

    return None


# ============================================================
# TERMINAL ENDPOINT
# ============================================================


@app.post("/api/terminal/execute")
async def terminal_execute(
    request: TerminalRequest
):

    start = time.time()

    record_request()

    try:

        parts = parse_terminal_command(
            request.command
        )

        files = validate_execution_files(
            request.files
        )

        workspace = create_workspace(
            files
        )

        try:

            virtual_result = (
                terminal_virtual_command(
                    parts,
                    workspace
                )
            )

            if virtual_result is not None:

                record_success()

                return {
                    **virtual_result,
                    "command":
                        request.command
                }

            validate_command(
                parts
            )

            result = await execute_code(
                files,
                parts,
                request.timeout
            )

            if result["status"] == "success":
                record_success()
            else:
                record_failure()

            return {
                "status":
                    result["status"],
                "command":
                    request.command,
                "stdout":
                    result["stdout"],
                "stderr":
                    result["stderr"],
                "return_code":
                    result["return_code"],
                "timed_out":
                    result["timed_out"],
                "latency_seconds":
                    result["latency_seconds"]
            }

        finally:

            shutil.rmtree(
                workspace,
                ignore_errors=True
            )

    except ValueError as error:

        record_failure()

        raise HTTPException(
            status_code=400,
            detail=str(error)
        )

    except RuntimeError as error:

        record_failure()

        raise HTTPException(
            status_code=503,
            detail=str(error)
        )

    except Exception as error:

        record_failure()

        raise HTTPException(
            status_code=500,
            detail=str(error)
        )

    finally:

        finish_timer(start)
        # ============================================================
# PART 9 - SECURITY SCANNER
# ============================================================


SECURITY_RULES = [
    {
        "name": "hardcoded_secret",
        "pattern": r"(?i)(api[_-]?key|secret|password|token)\s*=\s*['\"][^'\"]{8,}['\"]",
        "severity": "high",
        "message": "Possible hardcoded secret detected."
    },
    {
        "name": "eval_usage",
        "pattern": r"\beval\s*\(",
        "severity": "high",
        "message": "Dynamic eval() usage can execute untrusted code."
    },
    {
        "name": "exec_usage",
        "pattern": r"\bexec\s*\(",
        "severity": "high",
        "message": "Dynamic exec() usage can execute arbitrary code."
    },
    {
        "name": "shell_true",
        "pattern": r"shell\s*=\s*True",
        "severity": "high",
        "message": "subprocess shell=True can create command injection risk."
    },
    {
        "name": "os_system",
        "pattern": r"\bos\.system\s*\(",
        "severity": "high",
        "message": "os.system() executes operating-system commands."
    },
    {
        "name": "sql_concatenation",
        "pattern": r"(?i)(SELECT|INSERT|UPDATE|DELETE).*(\+|f['\"]|format\s*\()",
        "severity": "medium",
        "message": "Possible SQL string construction detected."
    },
    {
        "name": "inner_html",
        "pattern": r"\.innerHTML\s*=",
        "severity": "medium",
        "message": "innerHTML assignment can create XSS risk with untrusted data."
    }
]


def scan_file_security(
    path: str,
    content: str
):

    findings = []

    lines = content.splitlines()

    for line_number, line in enumerate(
        lines,
        start=1
    ):

        for rule in SECURITY_RULES:

            try:

                matched = re.search(
                    rule["pattern"],
                    line
                )

            except re.error:

                matched = False

            if matched:

                findings.append(
                    {
                        "file":
                            path,
                        "line":
                            line_number,
                        "severity":
                            rule["severity"],
                        "rule":
                            rule["name"],
                        "message":
                            rule["message"],
                        "code":
                            trim_text(
                                line,
                                300
                            )
                    }
                )

    return findings


def security_scan(
    files: List[ProjectFile]
):

    findings = []

    for file in files:

        file_findings = scan_file_security(
            file.path,
            file.content
        )

        findings.extend(
            file_findings
        )

    severity_order = {
        "critical": 0,
        "high": 1,
        "medium": 2,
        "low": 3
    }

    findings.sort(
        key=lambda item:
        severity_order.get(
            item["severity"],
            99
        )
    )

    counts = {
        "critical": 0,
        "high": 0,
        "medium": 0,
        "low": 0
    }

    for finding in findings:

        severity = finding[
            "severity"
        ]

        if severity in counts:
            counts[severity] += 1

    if counts["critical"] > 0:
        overall = "critical"
    elif counts["high"] > 0:
        overall = "high"
    elif counts["medium"] > 0:
        overall = "medium"
    elif counts["low"] > 0:
        overall = "low"
    else:
        overall = "clean"

    return {
        "status": "success",
        "overall": overall,
        "counts": counts,
        "total_findings":
            len(findings),
        "findings": findings
    }


# ============================================================
# SECURITY SCAN REQUEST
# ============================================================


class SecurityScanRequest(BaseModel):
    files: List[ProjectFile]


# ============================================================
# SECURITY SCAN ENDPOINT
# ============================================================


@app.post("/api/security/scan")
async def security_scan_endpoint(
    request: SecurityScanRequest
):

    start = time.time()

    record_request()

    try:

        files = validate_files(
            request.files
        )

        result = security_scan(
            files
        )

        record_success()

        return {
            **result,
            "latency_seconds":
                round(
                    time.time() - start,
                    3
                )
        }

    except ValueError as error:

        record_failure()

        raise HTTPException(
            status_code=400,
            detail=str(error)
        )

    except Exception as error:

        record_failure()

        raise HTTPException(
            status_code=500,
            detail=str(error)
        )

    finally:

        finish_timer(start)


# ============================================================
# AI SECURITY REVIEW
# ============================================================


class SecurityReviewRequest(BaseModel):
    files: List[ProjectFile]
    focus: str = "security"


@app.post("/api/security/review")
async def security_review(
    request: SecurityReviewRequest
):

    start = time.time()

    record_request()

    try:

        files = validate_files(
            request.files
        )

        static_result = security_scan(
            files
        )

        context = build_context(
            files
        )

        prompt = (
            "Perform a careful software security review.\n\n"
            "Focus: "
            + request.focus
            + "\n\n"
            "Static scanner findings:\n"
            + json.dumps(
                static_result["findings"],
                indent=2
            )
            + "\n\n"
            "PROJECT CONTEXT:\n"
            + context
            + "\n\n"
            "Explain the important risks, "
            "why they matter, and safe fixes. "
            "Do not provide instructions for "
            "attacking real systems."
        )

        ai_review = await ask_ai(
            prompt
        )

        record_success()

        return {
            "status": "success",
            "static_scan":
                static_result,
            "review": {
                "output":
                    ai_review
            },
            "latency_seconds":
                round(
                    time.time() - start,
                    3
                )
        }

    except ValueError as error:

        record_failure()

        raise HTTPException(
            status_code=400,
            detail=str(error)
        )

    except Exception as error:

        record_failure()

        raise HTTPException(
            status_code=500,
            detail=str(error)
        )

    finally:

        finish_timer(start)
        # ============================================================
# PART 10 - DEBUGGER + PERFORMANCE ANALYZER
# ============================================================


class DebugRequest(BaseModel):
    files: List[ProjectFile]
    error: str = ""
    question: str = ""


def find_debug_hints(
    files: List[ProjectFile]
):

    hints = []

    for file in files:

        lines = file.content.splitlines()

        for number, line in enumerate(
            lines,
            start=1
        ):

            stripped = line.strip()

            if "except:" in stripped:

                hints.append({
                    "file": file.path,
                    "line": number,
                    "type": "bare_except",
                    "message":
                        "Use a specific exception type when possible."
                })

            if "TODO" in line:

                hints.append({
                    "file": file.path,
                    "line": number,
                    "type": "todo",
                    "message":
                        "TODO marker found."
                })

            if "print(" in stripped:

                hints.append({
                    "file": file.path,
                    "line": number,
                    "type": "debug_output",
                    "message":
                        "Debug print statement detected."
                })

    return hints


@app.post("/api/debug")
async def debug_project(
    request: DebugRequest
):

    start = time.time()

    record_request()

    try:

        files = validate_files(
            request.files
        )

        hints = find_debug_hints(
            files
        )

        context = build_context(
            files
        )

        prompt = (
            "Act as an expert software debugger.\n\n"
            "Analyze the project and identify "
            "likely bugs, errors, and improvements.\n\n"
            "KNOWN ERROR:\n"
            + request.error
            + "\n\n"
            "USER QUESTION:\n"
            + request.question
            + "\n\n"
            "STATIC DEBUG HINTS:\n"
            + json.dumps(
                hints,
                indent=2
            )
            + "\n\n"
            "PROJECT CONTEXT:\n"
            + context
            + "\n\n"
            "Give clear explanations and safe "
            "code-fix suggestions."
        )

        answer = await ask_ai(
            prompt
        )

        record_success()

        return {
            "status": "success",
            "static_hints":
                hints,
            "debug": {
                "output":
                    answer
            },
            "latency_seconds":
                round(
                    time.time() - start,
                    3
                )
        }

    except ValueError as error:

        record_failure()

        raise HTTPException(
            status_code=400,
            detail=str(error)
        )

    except Exception as error:

        record_failure()

        raise HTTPException(
            status_code=500,
            detail=str(error)
        )

    finally:

        finish_timer(start)


# ============================================================
# PERFORMANCE ANALYZER
# ============================================================


class PerformanceRequest(BaseModel):
    files: List[ProjectFile]


def analyze_performance(
    files: List[ProjectFile]
):

    findings = []

    for file in files:

        lines = file.content.splitlines()

        loop_depth = 0

        for number, line in enumerate(
            lines,
            start=1
        ):

            stripped = line.strip()

            if stripped.startswith(
                ("for ", "while ")
            ):

                loop_depth += 1

                if loop_depth >= 2:

                    findings.append({
                        "file":
                            file.path,
                        "line":
                            number,
                        "type":
                            "nested_loop",
                        "severity":
                            "medium",
                        "message":
                            "Possible nested loop detected."
                    })

            if ".sort(" in stripped:

                findings.append({
                    "file":
                        file.path,
                    "line":
                        number,
                    "type":
                        "sort_operation",
                    "severity":
                        "low",
                    "message":
                        "Review repeated sorting for performance."
                })

            if "SELECT *" in line.upper():

                findings.append({
                    "file":
                        file.path,
                    "line":
                        number,
                    "type":
                        "select_all",
                    "severity":
                        "low",
                    "message":
                        "Selecting all database columns may be unnecessary."
                })

            if "time.sleep(" in stripped:

                findings.append({
                    "file":
                        file.path,
                    "line":
                        number,
                    "type":
                        "blocking_sleep",
                    "severity":
                        "medium",
                    "message":
                        "Blocking sleep may reduce responsiveness."
                })

        if len(
            file.content
        ) > 500000:

            findings.append({
                "file":
                    file.path,
                "line":
                    1,
                "type":
                    "large_file",
                "severity":
                    "medium",
                "message":
                    "Large source file detected."
            })

    return findings


@app.post("/api/performance/analyze")
async def performance_analyze(
    request: PerformanceRequest
):

    start = time.time()

    record_request()

    try:

        files = validate_files(
            request.files
        )

        findings = analyze_performance(
            files
        )

        record_success()

        return {
            "status":
                "success",
            "total_findings":
                len(findings),
            "findings":
                findings,
            "latency_seconds":
                round(
                    time.time() - start,
                    3
                )
        }

    except ValueError as error:

        record_failure()

        raise HTTPException(
            status_code=400,
            detail=str(error)
        )

    except Exception as error:

        record_failure()

        raise HTTPException(
            status_code=500,
            detail=str(error)
        )

    finally:

        finish_timer(start)


# ============================================================
# AI PERFORMANCE REVIEW
# ============================================================


class PerformanceReviewRequest(BaseModel):
    files: List[ProjectFile]


@app.post("/api/performance/review")
async def performance_review(
    request: PerformanceReviewRequest
):

    start = time.time()

    record_request()

    try:

        files = validate_files(
            request.files
        )

        findings = analyze_performance(
            files
        )

        context = build_context(
            files
        )

        prompt = (
            "Act as a senior performance engineer.\n\n"
            "Review this software project for "
            "performance bottlenecks and scalability issues.\n\n"
            "STATIC FINDINGS:\n"
            + json.dumps(
                findings,
                indent=2
            )
            + "\n\n"
            "PROJECT CONTEXT:\n"
            + context
            + "\n\n"
            "Explain the biggest issues and "
            "suggest practical optimizations."
        )

        answer = await ask_ai(
            prompt
        )

        record_success()

        return {
            "status":
                "success",
            "static_findings":
                findings,
            "review": {
                "output":
                    answer
            },
            "latency_seconds":
                round(
                    time.time() - start,
                    3
                )
        }

    except ValueError as error:

        record_failure()

        raise HTTPException(
            status_code=400,
            detail=str(error)
        )

    except Exception as error:

        record_failure()

        raise HTTPException(
            status_code=500,
            detail=str(error)
        )

    finally:

        finish_timer(start)
