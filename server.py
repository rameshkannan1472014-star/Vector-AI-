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
