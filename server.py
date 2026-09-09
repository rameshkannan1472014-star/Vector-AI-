import os
import time
import json
import uuid
import shutil
import asyncio
import logging
import subprocess
import sqlite3
import re
import tempfile

from pathlib import Path
from typing import Dict, Any, Optional, List

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from google import genai
from google.genai import types


# ============================================================
# ENGINEER AI BACKEND v5.0
# PART 1 - CORE / AI / MODELS
# ============================================================

APP_TITLE = "Engineer AI Engine"
APP_VERSION = "5.0.0"

DEFAULT_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-2.5-flash",
)

DEFAULT_PROVIDER = os.getenv(
    "AI_PROVIDER",
    "gemini",
)

GEMINI_API_KEY = os.getenv(
    "GEMINI_API_KEY",
    "",
)

MAX_PROMPT_LENGTH = int(
    os.getenv("MAX_PROMPT_LENGTH", "30000")
)

MAX_OUTPUT_LENGTH = int(
    os.getenv("MAX_OUTPUT_LENGTH", "30000")
)

MAX_PROJECT_FILES = int(
    os.getenv("MAX_PROJECT_FILES", "100")
)

MAX_FILE_SIZE = int(
    os.getenv("MAX_FILE_SIZE", "300000")
)

MAX_PROJECT_SIZE = int(
    os.getenv("MAX_PROJECT_SIZE", "3000000")
)

AI_RETRIES = int(
    os.getenv("AI_RETRIES", "2")
)

AI_RETRY_DELAY = float(
    os.getenv("AI_RETRY_DELAY", "1.5")
)

EXECUTION_MODE = os.getenv(
    "EXECUTION_MODE",
    "docker",
)

EXECUTION_TIMEOUT_SECONDS = int(
    os.getenv(
        "EXECUTION_TIMEOUT_SECONDS",
        "10",
    )
)

EXECUTION_OUTPUT_LIMIT = int(
    os.getenv(
        "EXECUTION_OUTPUT_LIMIT",
        "20000",
    )
)

MAX_TEST_FIX_CYCLES = int(
    os.getenv(
        "MAX_TEST_FIX_CYCLES",
        "3",
    )
)

PYTHON_DOCKER_IMAGE = os.getenv(
    "PYTHON_DOCKER_IMAGE",
    "python:3.12-slim",
)

NODE_DOCKER_IMAGE = os.getenv(
    "NODE_DOCKER_IMAGE",
    "node:22-slim",
)

ALLOW_UNSANDBOXED_EXECUTION = (
    os.getenv(
        "ALLOW_UNSANDBOXED_EXECUTION",
        "false",
    ).lower()
    == "true"
)

DATABASE_PATH = os.getenv(
    "ENGINEER_DATABASE",
    "engineer_ai.db",
)


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s "
        "%(levelname)s "
        "%(name)s "
        "%(message)s"
    ),
)

logger = logging.getLogger(
    "engineer-ai"
)


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title=APP_TITLE,
    version=APP_VERSION,
    description=(
        "Engineer AI engineering platform."
    ),
)


allowed_origins = os.getenv(
    "ALLOWED_ORIGINS",
    "*",
)

cors_origins = (
    ["*"]
    if allowed_origins == "*"
    else [
        x.strip()
        for x in allowed_origins.split(",")
        if x.strip()
    ]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=(
        "*" not in cors_origins
    ),
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# ANALYTICS
# ============================================================

analytics_data = {
    "requests": 0,
    "successful_requests": 0,
    "failed_requests": 0,
    "chat_requests": 0,
    "agent_requests": 0,
    "review_requests": 0,
    "execution_requests": 0,
    "execution_successes": 0,
    "execution_failures": 0,
    "test_requests": 0,
    "test_successes": 0,
    "test_failures": 0,
    "auto_fix_requests": 0,
    "auto_fix_successes": 0,
    "auto_fix_failures": 0,
    "benchmark_requests": 0,
    "benchmark_successes": 0,
    "project_requests": 0,
    "security_requests": 0,
    "debug_requests": 0,
    "terminal_requests": 0,
    "total_latency_seconds": 0.0,
}


# ============================================================
# MODELS
# ============================================================

class ChatRequest(BaseModel):
    message: Optional[str] = Field(
        default=None,
        max_length=MAX_PROMPT_LENGTH,
    )
    prompt: Optional[str] = Field(
        default=None,
        max_length=MAX_PROMPT_LENGTH,
    )
    model: Optional[str] = None


class AITextRequest(BaseModel):
    prompt: str = Field(
        min_length=1,
        max_length=MAX_PROMPT_LENGTH,
    )
    model: Optional[str] = None


class MultiAgentRequest(BaseModel):
    task: Optional[str] = Field(
        default=None,
        max_length=MAX_PROMPT_LENGTH,
    )
    task_description: Optional[str] = Field(
        default=None,
        max_length=MAX_PROMPT_LENGTH,
    )
    include_tests: bool = True
    include_security: bool = True
    include_performance: bool = False


class CodeReviewRequest(BaseModel):
    code: str = Field(
        min_length=1,
        max_length=MAX_OUTPUT_LENGTH,
    )
    language: str = "python"
    focus: Optional[str] = None


class ProjectFile(BaseModel):
    path: str
    content: str = Field(
        max_length=MAX_FILE_SIZE,
    )


class ProjectRequest(BaseModel):
    project_name: str = "EngineerProject"
    files: List[ProjectFile]
    task: str = Field(
        min_length=1,
        max_length=MAX_PROMPT_LENGTH,
    )


class ProjectAgentRequest(ProjectRequest):
    include_tests: bool = True
    include_security: bool = True
    include_performance: bool = True


class ExecuteRequest(BaseModel):
    files: List[ProjectFile]
    entrypoint: str = "main.py"
    language: Optional[str] = None
    command: Optional[List[str]] = None
    timeout_seconds: Optional[int] = None


class TestRequest(BaseModel):
    files: List[ProjectFile]
    command: Optional[List[str]] = None
    language: Optional[str] = None
    timeout_seconds: Optional[int] = None


class TerminalRequest(BaseModel):
    files: List[ProjectFile] = []
    command: str = Field(
        min_length=1,
        max_length=500,
    )
    language: str = "python"
    timeout_seconds: Optional[int] = None


class DebugRequest(BaseModel):
    files: List[ProjectFile]
    error: Optional[str] = None
    language: Optional[str] = None


class AutoFixRequest(BaseModel):
    files: List[ProjectFile]
    task: str = Field(
        min_length=1,
        max_length=MAX_PROMPT_LENGTH,
    )
    max_cycles: int = Field(
        default=3,
        ge=1,
        le=5,
    )
    language: Optional[str] = None


class BenchmarkRequest(BaseModel):
    tasks: List[str] = Field(
        min_length=1,
        max_length=20,
    )


class ProjectCreateRequest(BaseModel):
    name: str = "New Project"
    task: str = ""
    files: List[ProjectFile] = []


class MemoryRequest(BaseModel):
    project_id: str
    key: str
    value: str


class MemoryAskRequest(BaseModel):
    project_id: str
    question: str


# ============================================================
# HELPERS
# ============================================================

def clamp_text(
    value: Any,
    limit: int,
) -> str:

    text = str(value)

    if len(text) <= limit:
        return text

    return (
        text[:limit]
        + "\n...[truncated]"
    )


def record(
    key: str,
    amount: int = 1,
) -> None:

    analytics_data[key] = (
        analytics_data.get(key, 0)
        + amount
    )


def record_latency(
    start: float,
) -> None:

    analytics_data[
        "total_latency_seconds"
    ] += time.time() - start


def success_response(
    response: Any = None,
    **extra,
) -> Dict[str, Any]:

    result = {
        "status": "success",
        "response": response,
    }

    result.update(extra)

    return result


# ============================================================
# SYSTEM INSTRUCTION
# ============================================================

SYSTEM_INSTRUCTION = """
You are Engineer AI, an advanced software
engineering assistant.

Help users design, write, understand,
debug, test, review and improve software.

Priorities:
1. Correctness
2. Security
3. Reliability
4. Maintainability
5. Performance

Understand existing code before proposing
changes. Preserve working functionality.
Avoid destructive operations and secrets.

Never claim code was executed unless the
execution system actually executed it.
"""


# ============================================================
# GEMINI
# ============================================================

_client = None


def get_client():

    global _client

    if _client is not None:
        return _client

    if not GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY is not configured."
        )

    _client = genai.Client(
        api_key=GEMINI_API_KEY
    )

    return _client


def retryable(error: Exception) -> bool:

    text = str(error).lower()

    words = [
        "timeout",
        "temporarily",
        "unavailable",
        "rate limit",
        "429",
        "500",
        "502",
        "503",
        "connection",
    ]

    return any(
        word in text
        for word in words
    )


def generate_sync(
    prompt: str,
    model: Optional[str] = None,
    json_mode: bool = False,
) -> str:

    client = get_client()

    config_args = {
        "system_instruction":
            SYSTEM_INSTRUCTION,
    }

    if json_mode:
        config_args[
            "response_mime_type"
        ] = "application/json"

    config = types.GenerateContentConfig(
        **config_args
    )

    last_error = None

    for attempt in range(
        AI_RETRIES + 1
    ):

        try:

            result = client.models.generate_content(
                model=model or DEFAULT_MODEL,
                contents=clamp_text(
                    prompt,
                    MAX_PROMPT_LENGTH,
                ),
                config=config,
            )

            text = getattr(
                result,
                "text",
                None,
            )

            if not text:
                raise RuntimeError(
                    "AI returned an empty response."
                )

            return clamp_text(
                text,
                MAX_OUTPUT_LENGTH,
            )

        except Exception as error:

            last_error = error

            if (
                attempt < AI_RETRIES
                and retryable(error)
            ):

                time.sleep(
                    AI_RETRY_DELAY
                    * (attempt + 1)
                )

            else:
                break

    raise RuntimeError(
        f"AI generation failed: {last_error}"
    )


async def infer(
    prompt: str,
    model: Optional[str] = None,
    json_mode: bool = False,
) -> str:

    return await asyncio.to_thread(
        generate_sync,
        prompt,
        model,
        json_mode,
    )


# ============================================================
# HEALTH
# ============================================================

@app.get("/api/health")
async def health():

    return {
        "status": "ok",
        "service": APP_TITLE,
        "version": APP_VERSION,
        "provider": DEFAULT_PROVIDER,
        "model": DEFAULT_MODEL,
        "execution_mode": EXECUTION_MODE,
        "docker_available":
            shutil.which("docker") is not None,
        "gemini_configured":
            bool(GEMINI_API_KEY),
    }


# ============================================================
# CHAT
# ============================================================

@app.post("/api/chat")
async def chat(
    request: ChatRequest,
):

    start = time.time()

    record("requests")
    record("chat_requests")

    message = (
        request.message
        or request.prompt
        or ""
    ).strip()

    if not message:
        raise HTTPException(
            status_code=400,
            detail="Message is required.",
        )

    try:

        output = await infer(
            message,
            request.model,
        )

        record(
            "successful_requests"
        )

        return {
            "status": "success",
            "response": output,
            "message": output,
            "model":
                request.model
                or DEFAULT_MODEL,
            "latency_seconds":
                round(
                    time.time() - start,
                    3,
                ),
        }

    except Exception as error:

        record(
            "failed_requests"
        )

        raise HTTPException(
            status_code=500,
            detail=str(error),
        )

    finally:

        record_latency(start)


@app.post("/api/ai/explain")
async def ai_explain(
    request: AITextRequest,
):

    start = time.time()

    record("requests")

    try:

        output = await infer(
            f"""
Explain this software engineering
question clearly:

{request.prompt}
""",
            request.model,
        )

        record(
            "successful_requests"
        )

        return success_response(
            output,
            message=output,
            model=(
                request.model
                or DEFAULT_MODEL
            ),
        )

    except Exception as error:

        record(
            "failed_requests"
        )

        raise HTTPException(
            status_code=500,
            detail=str(error),
        )

    finally:

        record_latency(start)
# ============================================================
# PART 2 - PROJECT INTELLIGENCE + AGENTS
# ============================================================


ALLOWED_PROJECT_EXTENSIONS = {
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
    ".sh",
}


def safe_relative_path(
    path: str,
) -> str:

    if not path:
        raise ValueError(
            "File path cannot be empty."
        )

    normalized = (
        path
        .replace("\\", "/")
        .strip()
    )

    if (
        normalized.startswith("/")
        or "\x00" in normalized
    ):
        raise ValueError(
            "Invalid file path."
        )

    candidate = Path(normalized)

    if candidate.is_absolute():
        raise ValueError(
            "Absolute paths are not allowed."
        )

    if ".." in candidate.parts:
        raise ValueError(
            "Path traversal is not allowed."
        )

    return str(candidate)


def validate_project_files(
    files: List[ProjectFile],
) -> List[ProjectFile]:

    if not files:
        raise ValueError(
            "Project must contain at least one file."
        )

    if len(files) > MAX_PROJECT_FILES:
        raise ValueError(
            f"Maximum {MAX_PROJECT_FILES} files allowed."
        )

    total = 0
    seen = set()
    result = []

    for item in files:

        path = safe_relative_path(
            item.path
        )

        if path in seen:
            raise ValueError(
                f"Duplicate file: {path}"
            )

        seen.add(path)

        suffix = (
            Path(path)
            .suffix
            .lower()
        )

        if (
            suffix
            and suffix not in
            ALLOWED_PROJECT_EXTENSIONS
        ):
            raise ValueError(
                f"Unsupported file type: {suffix}"
            )

        size = len(
            item.content.encode(
                "utf-8",
                errors="ignore",
            )
        )

        if size > MAX_FILE_SIZE:
            raise ValueError(
                f"File too large: {path}"
            )

        total += size

        result.append(
            ProjectFile(
                path=path,
                content=item.content,
            )
        )

    if total > MAX_PROJECT_SIZE:
        raise ValueError(
            "Project is too large."
        )

    return result


def detect_language(
    path: str,
) -> str:

    mapping = {
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
    }

    return mapping.get(
        Path(path).suffix.lower(),
        "text",
    )


def build_project_context(
    files: List[ProjectFile],
) -> str:

    blocks = []

    for item in files:

        blocks.append(
            f"""
===== FILE: {item.path} =====
{item.content}
"""
        )

    return clamp_text(
        "\n".join(blocks),
        MAX_PROJECT_SIZE,
    )


def project_statistics(
    files: List[ProjectFile],
) -> Dict[str, Any]:

    languages = {}
    lines = 0
    bytes_total = 0

    for item in files:

        language = detect_language(
            item.path
        )

        languages[language] = (
            languages.get(language, 0)
            + 1
        )

        lines += (
            item.content.count("\n")
            + 1
        )

        bytes_total += len(
            item.content.encode(
                "utf-8",
                errors="ignore",
            )
        )

    return {
        "file_count": len(files),
        "line_count": lines,
        "size_bytes": bytes_total,
        "languages": languages,
    }


# ============================================================
# AGENTS
# ============================================================

AGENT_ROLES = {

    "planner": """
You are the Planning Agent.
Analyze requirements, architecture,
implementation steps and risks.
""",

    "coder": """
You are the Coding Agent.
Design correct, maintainable implementation
changes without unnecessary modifications.
""",

    "debugger": """
You are the Debugging Agent.
Find root causes, logic errors, runtime
problems and edge cases.
""",

    "tester": """
You are the Testing Agent.
Design useful unit, integration and edge tests.
""",

    "security": """
You are the Security Agent.
Find input validation, injection, secrets,
path traversal, authentication and
data exposure risks.
""",

    "performance": """
You are the Performance Agent.
Find inefficient algorithms, unnecessary
I/O, memory problems and scalability risks.
""",

    "research": """
You are the Research Agent.
Compare technical approaches and provide
practical engineering recommendations.
""",

    "documentation": """
You are the Documentation Agent.
Explain architecture, APIs, setup and usage.
""",
}


async def run_agent(
    role: str,
    task: str,
    context: str = "",
) -> str:

    instruction = AGENT_ROLES.get(
        role,
        AGENT_ROLES["research"],
    )

    prompt = f"""
{instruction}

TASK:
{task}

PROJECT CONTEXT:
{context}

Return a useful engineering result.
Do not claim execution unless execution
actually occurred.
"""

    return await infer(prompt)


async def run_multi_agent_workflow(
    task: str,
    context: str = "",
    include_tests: bool = True,
    include_security: bool = True,
    include_performance: bool = False,
) -> Dict[str, Any]:

    workflow = []

    roles = [
        "planner",
        "coder",
        "debugger",
    ]

    if include_tests:
        roles.append("tester")

    if include_security:
        roles.append("security")

    if include_performance:
        roles.append("performance")

    for role in roles:

        output = await run_agent(
            role,
            task,
            context,
        )

        workflow.append({
            "agent": role,
            "status": "completed",
            "output": output,
        })

    combined = "\n\n".join(
        f"AGENT: {x['agent']}\n{x['output']}"
        for x in workflow
    )

    final = await infer(
        f"""
You are the Lead Engineering Agent.

TASK:
{task}

PROJECT:
{context}

AGENT RESULTS:
{combined}

Produce:
1. Summary
2. Recommended implementation
3. Risks
4. Testing
5. Security
6. Performance
7. Next steps
"""
    )

    return {
        "status": "completed",
        "workflow": workflow,
        "final": final,
        "agent_count": len(workflow),
    }


# ============================================================
# AGENT API
# ============================================================

@app.post("/api/agent/execute")
async def execute_agent(
    request: MultiAgentRequest,
):

    start = time.time()

    record("requests")
    record("agent_requests")

    task = (
        request.task
        or request.task_description
        or ""
    ).strip()

    if not task:
        raise HTTPException(
            status_code=400,
            detail="Task is required.",
        )

    try:

        result = await run_multi_agent_workflow(
            task=task,
            include_tests=request.include_tests,
            include_security=request.include_security,
            include_performance=request.include_performance,
        )

        record(
            "successful_requests"
        )

        return {
            "status": "success",
            "task": task,
            "workflow":
                result["workflow"],
            "final":
                result["final"],
            "agent_count":
                result["agent_count"],
            "latency_seconds":
                round(
                    time.time() - start,
                    3,
                ),
        }

    except Exception as error:

        record(
            "failed_requests"
        )

        raise HTTPException(
            status_code=500,
            detail=str(error),
        )

    finally:

        record_latency(start)


# ============================================================
# CODE REVIEW
# ============================================================

@app.post("/api/code/review")
async def code_review(
    request: CodeReviewRequest,
):

    start = time.time()

    record("requests")
    record("review_requests")

    focus = (
        request.focus
        or "correctness, security, "
           "performance and maintainability"
    )

    try:

        output = await infer(
            f"""
Perform a professional code review.

LANGUAGE:
{request.language}

FOCUS:
{focus}

CODE:
```{request.language}
{request.code}

---

# 🧩 PART 3/5 — Secure Execution + Testing + Terminal

```python
# ============================================================
# PART 3 - EXECUTION / TESTING / TERMINAL
# ============================================================


SAFE_EXECUTABLES = {
    "python",
    "python3",
    "node",
    "npm",
}


def validate_command(
    command: List[str],
) -> List[str]:

    if not command:
        raise ValueError(
            "Command cannot be empty."
        )

    if len(command) > 20:
        raise ValueError(
            "Too many command arguments."
        )

    cleaned = []

    for value in command:

        if not isinstance(value, str):
            raise ValueError(
                "Command arguments must be strings."
            )

        if len(value) > 500:
            raise ValueError(
                "Command argument is too long."
            )

        if "\x00" in value:
            raise ValueError(
                "Null byte detected."
            )

        cleaned.append(value)

    executable = Path(
        cleaned[0]
    ).name.lower()

    if executable not in SAFE_EXECUTABLES:
        raise ValueError(
            f"Command '{executable}' is not allowed."
        )

    return cleaned


def default_command(
    language: str,
    entrypoint: str,
) -> List[str]:

    entrypoint = safe_relative_path(
        entrypoint
    )

    language = language.lower()

    if language == "python":
        return [
            "python",
            entrypoint,
        ]

    if language in (
        "javascript",
        "js",
        "node",
    ):
        return [
            "node",
            entrypoint,
        ]

    raise ValueError(
        f"Unsupported language: {language}"
    )


def output_limit(
    text: str,
) -> str:

    text = text or ""

    return (
        text
        if len(text) <=
        EXECUTION_OUTPUT_LIMIT
        else
        text[:EXECUTION_OUTPUT_LIMIT]
        + "\n...[output truncated]"
    )


def create_workspace(
    files: List[ProjectFile],
) -> Path:

    files = validate_project_files(
        files
    )

    base = Path(
        tempfile.mkdtemp(
            prefix="engineer_ai_"
        )
    )

    for item in files:

        target = base / item.path

        target.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        target.write_text(
            item.content,
            encoding="utf-8",
        )

    return base


def cleanup_workspace(
    workspace: Path,
) -> None:

    shutil.rmtree(
        workspace,
        ignore_errors=True,
    )


def run_docker(
    workspace: Path,
    command: List[str],
    language: str,
    timeout: int,
) -> Dict[str, Any]:

    if shutil.which("docker") is None:
        raise RuntimeError(
            "Docker is not available."
        )

    image = (
        PYTHON_DOCKER_IMAGE
        if language == "python"
        else NODE_DOCKER_IMAGE
    )

    docker_command = [
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
        f"{workspace.resolve()}:/workspace:ro",
        "-w",
        "/workspace",
        image,
    ] + command

    start = time.time()

    try:

        result = subprocess.run(
            docker_command,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        return {
            "status":
                (
                    "success"
                    if result.returncode == 0
                    else "failed"
                ),
            "return_code":
                result.returncode,
            "stdout":
                output_limit(
                    result.stdout
                ),
            "stderr":
                output_limit(
                    result.stderr
                ),
            "duration_seconds":
                round(
                    time.time() - start,
                    3,
                ),
            "sandbox":
                "docker",
        }

    except subprocess.TimeoutExpired as error:

        return {
            "status": "timeout",
            "return_code": None,
            "stdout":
                output_limit(
                    error.stdout or ""
                ),
            "stderr":
                output_limit(
                    error.stderr or ""
                ),
            "duration_seconds":
                round(
                    time.time() - start,
                    3,
                ),
            "sandbox":
                "docker",
        }


def run_host_explicit(
    workspace: Path,
    command: List[str],
    timeout: int,
) -> Dict[str, Any]:

    if not ALLOW_UNSANDBOXED_EXECUTION:
        raise RuntimeError(
            "Host execution is disabled."
        )

    start = time.time()

    try:

        result = subprocess.run(
            command,
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        return {
            "status":
                (
                    "success"
                    if result.returncode == 0
                    else "failed"
                ),
            "return_code":
                result.returncode,
            "stdout":
                output_limit(
                    result.stdout
                ),
            "stderr":
                output_limit(
                    result.stderr
                ),
            "duration_seconds":
                round(
                    time.time() - start,
                    3,
                ),
            "sandbox":
                "host-explicitly-enabled",
        }

    except subprocess.TimeoutExpired:

        return {
            "status": "timeout",
            "return_code": None,
            "stdout": "",
            "stderr": "Execution timed out.",
            "duration_seconds":
                round(
                    time.time() - start,
                    3,
                ),
            "sandbox":
                "host-explicitly-enabled",
        }


def execute_project(
    files: List[ProjectFile],
    command: List[str],
    language: str,
    timeout: int,
) -> Dict[str, Any]:

    workspace = create_workspace(
        files
    )

    try:

        command = validate_command(
            command
        )

        if EXECUTION_MODE == "docker":

            return run_docker(
                workspace,
                command,
                language,
                timeout,
            )

        if EXECUTION_MODE == "host":

            return run_host_explicit(
                workspace,
                command,
                timeout,
            )

        raise RuntimeError(
            "Unknown execution mode."
        )

    finally:

        cleanup_workspace(
            workspace
        )


# ============================================================
# EXECUTE API
# ============================================================

@app.post("/api/execute")
async def execute_code(
    request: ExecuteRequest,
):

    start = time.time()

    record("requests")
    record("execution_requests")

    try:

        files = validate_project_files(
            request.files
        )

        language = (
            request.language
            or detect_language(
                request.entrypoint
            )
        )

        timeout = max(
            1,
            min(
                request.timeout_seconds
                or EXECUTION_TIMEOUT_SECONDS,
                EXECUTION_TIMEOUT_SECONDS,
            ),
        )

        command = (
            request.command
            if request.command
            else default_command(
                language,
                request.entrypoint,
            )
        )

        result = await asyncio.to_thread(
            execute_project,
            files,
            command,
            language,
            timeout,
        )

        if result["status"] == "success":
            record(
                "execution_successes"
            )
        else:
            record(
                "execution_failures"
            )

        return {
            "status": "success",
            "execution": result,
            "latency_seconds":
                round(
                    time.time() - start,
                    3,
                ),
        }

    except ValueError as error:

        record(
            "execution_failures"
        )

        raise HTTPException(
            status_code=400,
            detail=str(error),
        )

    except Exception as error:

        record(
            "execution_failures"
        )

        raise HTTPException(
            status_code=500,
            detail=str(error),
        )

    finally:

        record_latency(start)


# ============================================================
# TEST API
# ============================================================

@app.post("/api/test/run")
async def run_tests(
    request: TestRequest,
):

    start = time.time()

    record("requests")
    record("test_requests")

    try:

        files = validate_project_files(
            request.files
        )

        language = (
            request.language
            or detect_language(
                files[0].path
            )
        )

        if request.command:

            command = validate_command(
                request.command
            )

        elif language == "python":

            command = [
                "python",
                "-m",
                "unittest",
                "discover",
                "-v",
            ]

        elif language in (
            "javascript",
            "js",
            "node",
        ):

            command = [
                "npm",
                "test",
            ]

        else:

            raise ValueError(
                "Automatic testing is not "
                "supported for this language."
            )

        result = await asyncio.to_thread(
            execute_project,
            files,
            command,
            language,
            request.timeout_seconds
            or EXECUTION_TIMEOUT_SECONDS,
        )

        passed = (
            result["status"]
            == "success"
        )

        record(
            "test_successes"
            if passed
            else "test_failures"
        )

        return {
            "status": "success",
            "tests": result,
            "passed": passed,
            "latency_seconds":
                round(
                    time.time() - start,
                    3,
                ),
        }

    except ValueError as error:

        record(
            "test_failures"
        )

        raise HTTPException(
            status_code=400,
            detail=str(error),
        )

    except Exception as error:

        record(
            "test_failures"
        )

        raise HTTPException(
            status_code=500,
            detail=str(error),
        )

    finally:

        record_latency(start)


# ============================================================
# SAFE TERMINAL
# ============================================================

@app.post("/api/terminal/execute")
async def terminal_execute(
    request: TerminalRequest,
):

    start = time.time()

    record("requests")
    record("terminal_requests")

    command_text = request.command.strip()

    if not command_text:
        raise HTTPException(
            status_code=400,
            detail="Command is required.",
        )

    if command_text in (
        "help",
        "?",
    ):

        return {
            "status": "success",
            "output": (
                "Engineer AI Terminal\n"
                "Allowed commands:\n"
                "python <file>\n"
                "node <file>\n"
                "python -m unittest discover -v\n"
                "npm test"
            ),
        }

    if command_text == "pwd":

        return {
            "status": "success",
            "output": "/workspace",
        }

    if command_text == "ls":

        names = [
            x.path
            for x in request.files
        ]

        return {
            "status": "success",
            "output": "\n".join(names),
        }

    parts = command_text.split()

    try:

        command = validate_command(
            parts
        )

        result = await asyncio.to_thread(
            execute_project,
            request.files,
            command,
            request.language,
            request.timeout_seconds
            or EXECUTION_TIMEOUT_SECONDS,
        )

        return {
            "status": "success",
            "execution": result,
        }

    except ValueError as error:

        raise HTTPException(
            status_code=400,
            detail=str(error),
        )

    except Exception as error:

        raise HTTPException(
            status_code=500,
            detail=str(error),
        )

    finally:

        record_latency(start)
# ============================================================
# PART 4 - DEBUGGER / SECURITY / AUTO-FIX / BENCHMARK
# ============================================================


# ============================================================
# SECURITY SCANNER
# ============================================================

SECURITY_PATTERNS = [

    (
        r"(?i)(api[_-]?key|secret|password)"
        r"\s*=\s*['\"][^'\"]+['\"]",
        "Possible hard-coded secret.",
    ),

    (
        r"(?i)eval\s*\(",
        "Use of eval() detected.",
    ),

    (
        r"(?i)exec\s*\(",
        "Use of exec() detected.",
    ),

    (
        r"(?i)subprocess\.(run|Popen|call)"
        r".*shell\s*=\s*True",
        "Potential shell injection risk.",
    ),

    (
        r"(?i)SELECT\s+.*\+",
        "Possible SQL injection pattern.",
    ),

    (
        r"(?i)os\.system\s*\(",
        "os.system() detected.",
    ),

    (
        r"(?i)innerHTML\s*=",
        "Potential DOM injection risk.",
    ),

]


def static_security_scan(
    files: List[ProjectFile],
) -> List[Dict[str, Any]]:

    findings = []

    for item in files:

        for pattern, message in SECURITY_PATTERNS:

            try:
                matches = list(
                    re.finditer(
                        pattern,
                        item.content,
                    )
                )
            except re.error:
                matches = []

            for match in matches:

                line = (
                    item.content[:match.start()]
                    .count("\n")
                    + 1
                )

                findings.append({
                    "file": item.path,
                    "line": line,
                    "severity": "medium",
                    "message": message,
                })

    return findings


@app.post("/api/security/scan")
async def security_scan(
    request: ProjectRequest,
):

    record("requests")
    record("security_requests")

    try:

        files = validate_project_files(
            request.files
        )

        findings = static_security_scan(
            files
        )

        return {
            "status": "success",
            "findings": findings,
            "count": len(findings),
        }

    except ValueError as error:

        raise HTTPException(
            status_code=400,
            detail=str(error),
        )


@app.post("/api/security/review")
async def security_review(
    request: ProjectRequest,
):

    record("requests")
    record("security_requests")

    files = validate_project_files(
        request.files
    )

    context = build_project_context(
        files
    )

    static_findings = static_security_scan(
        files
    )

    ai_review = await infer(
        f"""
Perform a security review of this project.

PROJECT:
{context}

STATIC FINDINGS:
{json.dumps(
    static_findings,
    indent=2,
)}

Look for:
- injection
- secrets
- authentication problems
- authorization problems
- unsafe file access
- unsafe execution
- data exposure
- insecure defaults

Provide severity and recommendations.
"""
    )

    return {
        "status": "success",
        "static_findings":
            static_findings,
        "ai_review":
            ai_review,
    }


# ============================================================
# DEBUGGER
# ============================================================

@app.post("/api/debug")
async def debug_project(
    request: DebugRequest,
):

    start = time.time()

    record("requests")
    record("debug_requests")

    try:

        files = validate_project_files(
            request.files
        )

        context = build_project_context(
            files
        )

        prompt = f"""
You are the Engineer AI Debugger.

Analyze this project for bugs.

PROJECT:
{context}

ERROR:
{request.error or "No error supplied."}

Return:
1. probable root cause
2. affected file
3. affected area
4. explanation
5. recommended fix
6. tests to verify the fix

Do not claim execution.
"""

        analysis = await infer(
            prompt
        )

        return {
            "status": "success",
            "debug": analysis,
            "files":
                [x.path for x in files],
            "latency_seconds":
                round(
                    time.time() - start,
                    3,
                ),
        }

    except ValueError as error:

        raise HTTPException(
            status_code=400,
            detail=str(error),
        )

    finally:

        record_latency(start)


# ============================================================
# JSON EXTRACTION
# ============================================================

def extract_json(
    text: str,
) -> Optional[Any]:

    text = text.strip()

    try:
        return json.loads(text)
    except Exception:
        pass

    match = re.search(
        r"```(?:json)?\s*(.*?)```",
        text,
        re.DOTALL | re.IGNORECASE,
    )

    if match:

        try:
            return json.loads(
                match.group(1).strip()
            )
        except Exception:
            pass

    start = text.find("{")
    end = text.rfind("}")

    if start >= 0 and end > start:

        try:
            return json.loads(
                text[start:end + 1]
            )
        except Exception:
            pass

    return None


# ============================================================
# AUTO-FIX
# ============================================================

def validate_fixed_files(
    original: List[ProjectFile],
    candidate: Any,
) -> List[ProjectFile]:

    if not isinstance(
        candidate,
        list,
    ):
        raise ValueError(
            "AI did not return a file list."
        )

    fixed = []

    for item in candidate:

        if not isinstance(
            item,
            dict,
        ):
            continue

        path = item.get(
            "path"
        )

        content = item.get(
            "content"
        )

        if not isinstance(
            path,
            str,
        ):
            continue

        if not isinstance(
            content,
            str,
        ):
            continue

        fixed.append(
            ProjectFile(
                path=path,
                content=content,
            )
        )

    if not fixed:
        raise ValueError(
            "AI returned no valid files."
        )

    return validate_project_files(
        fixed
    )


async def generate_fixed_project(
    files: List[ProjectFile],
    task: str,
    error_output: str = "",
) -> List[ProjectFile]:

    context = build_project_context(
        files
    )

    prompt = f"""
Fix this software project.

TASK:
{task}

ERROR OUTPUT:
{error_output}

CURRENT PROJECT:
{context}

Return ONLY valid JSON in this format:

[
  {{
    "path": "file.py",
    "content": "complete file content"
  }}
]

Return complete contents for files that
need modification.
"""

    output = await infer(
        prompt,
        json_mode=True,
    )

    parsed = extract_json(
        output
    )

    return validate_fixed_files(
        files,
        parsed,
    )


@app.post("/api/agent/autofix")
async def agent_autofix(
    request: AutoFixRequest,
):

    start = time.time()

    record("requests")
    record("auto_fix_requests")

    current_files = validate_project_files(
        request.files
    )

    history = []
    final_execution = None

    max_cycles = min(
        request.max_cycles,
        MAX_TEST_FIX_CYCLES,
    )

    try:

        for cycle in range(
            1,
            max_cycles + 1,
        ):

            language = (
                request.language
                or detect_language(
                    current_files[0].path
                )
            )

            entrypoint = current_files[0].path

            if language == "python":

                command = [
                    "python",
                    entrypoint,
                ]

            elif language in (
                "javascript",
                "js",
                "node",
            ):

                command = [
                    "node",
                    entrypoint,
                ]

            else:

                break

            result = await asyncio.to_thread(
                execute_project,
                current_files,
                command,
                language,
                EXECUTION_TIMEOUT_SECONDS,
            )

            final_execution = result

            history.append({
                "cycle": cycle,
                "execution": result,
            })

            if result["status"] == "success":

                record(
                    "auto_fix_successes"
                )

                return {
                    "status": "success",
                    "fixed": True,
                    "cycles": cycle,
                    "files": [
                        x.model_dump()
                        for x in current_files
                    ],
                    "history": history,
                    "execution": result,
                }

            current_files = (
                await generate_fixed_project(
                    current_files,
                    request.task,
                    (
                        result.get("stderr", "")
                        + "\n"
                        + result.get("stdout", "")
                    ),
                )
            )

        record(
            "auto_fix_failures"
        )

        return {
            "status": "success",
            "fixed": False,
            "cycles": max_cycles,
            "files": [
                x.model_dump()
                for x in current_files
            ],
            "history": history,
            "execution":
                final_execution,
        }

    except ValueError as error:

        record(
            "auto_fix_failures"
        )

        raise HTTPException(
            status_code=400,
            detail=str(error),
        )

    except Exception as error:

        record(
            "auto_fix_failures"
        )

        raise HTTPException(
            status_code=500,
            detail=str(error),
        )

    finally:

        record_latency(start)


# ============================================================
# BENCHMARK
# ============================================================

@app.post("/api/benchmark/run")
async def benchmark(
    request: BenchmarkRequest,
):

    start = time.time()

    record("requests")
    record("benchmark_requests")

    results = []

    for task in request.tasks:

        task_start = time.time()

        try:

            answer = await infer(
                f"""
Solve this engineering benchmark task:

{task}

Give a correct and concise engineering answer.
"""
            )

            results.append({
                "task": task,
                "status": "success",
                "output": answer,
                "latency_seconds":
                    round(
                        time.time()
                        - task_start,
                        3,
                    ),
            })

        except Exception as error:

            results.append({
                "task": task,
                "status": "failed",
                "error": str(error),
            })

    successful = sum(
        1
        for x in results
        if x["status"] == "success"
    )

    record(
        "benchmark_successes"
        if successful == len(results)
        else "failed_requests"
    )

    return {
        "status": "success",
        "total": len(results),
        "successful": successful,
        "score":
            round(
                successful
                / max(len(results), 1)
                * 100,
                2,
            ),
        "results": results,
        "latency_seconds":
            round(
                time.time() - start,
                3,
            ),
    }
# ============================================================
# PART 5 - DATABASE / PROJECTS / MEMORY / ANALYTICS / STARTUP
# ============================================================


# ============================================================
# DATABASE
# ============================================================

def db_connect():

    connection = sqlite3.connect(
        DATABASE_PATH
    )

    connection.row_factory = (
        sqlite3.Row
    )

    return connection


def initialize_database():

    connection = db_connect()

    try:

        connection.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                task TEXT DEFAULT '',
                files_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS memory (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                memory_key TEXT NOT NULL,
                memory_value TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_memory_project
            ON memory(project_id)
        """)

        connection.commit()

    finally:

        connection.close()


def save_project_db(
    project_id: str,
    name: str,
    task: str,
    files: List[ProjectFile],
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
                    x.model_dump()
                    for x in files
                ]),
                now,
                now,
            ),
        )

        connection.commit()

    finally:

        connection.close()


def get_project_db(
    project_id: str,
):

    connection = db_connect()

    try:

        row = connection.execute(
            """
            SELECT *
            FROM projects
            WHERE id = ?
            """,
            (project_id,),
        ).fetchone()

        if not row:
            return None

        return {
            "id": row["id"],
            "name": row["name"],
            "task": row["task"],
            "files":
                json.loads(
                    row["files_json"]
                ),
            "created_at":
                row["created_at"],
            "updated_at":
                row["updated_at"],
        }

    finally:

        connection.close()


# ============================================================
# PROJECT CRUD
# ============================================================

@app.post("/api/projects")
async def create_project(
    request: ProjectCreateRequest,
):

    files = validate_project_files(
        request.files
    )

    project_id = uuid.uuid4().hex

    save_project_db(
        project_id,
        request.name,
        request.task,
        files,
    )

    return {
        "status": "success",
        "project": {
            "id": project_id,
            "name": request.name,
            "task": request.task,
            "files": [
                x.model_dump()
                for x in files
            ],
        },
    }


@app.get("/api/projects")
async def list_projects():

    connection = db_connect()

    try:

        rows = connection.execute(
            """
            SELECT id, name, task,
                   created_at, updated_at
            FROM projects
            ORDER BY updated_at DESC
            """
        ).fetchall()

        return {
            "status": "success",
            "projects": [
                dict(row)
                for row in rows
            ],
        }

    finally:

        connection.close()


@app.get("/api/projects/{project_id}")
async def get_project(
    project_id: str,
):

    project = get_project_db(
        project_id
    )

    if not project:

        raise HTTPException(
            status_code=404,
            detail="Project not found.",
        )

    return {
        "status": "success",
        "project": project,
    }


@app.put("/api/projects/{project_id}")
async def update_project(
    project_id: str,
    request: ProjectCreateRequest,
):

    if not get_project_db(
        project_id
    ):

        raise HTTPException(
            status_code=404,
            detail="Project not found.",
        )

    files = validate_project_files(
        request.files
    )

    save_project_db(
        project_id,
        request.name,
        request.task,
        files,
    )

    return {
        "status": "success",
        "project":
            get_project_db(
                project_id
            ),
    }


@app.delete("/api/projects/{project_id}")
async def delete_project(
    project_id: str,
):

    connection = db_connect()

    try:

        connection.execute(
            """
            DELETE FROM memory
            WHERE project_id = ?
            """,
            (project_id,),
        )

        result = connection.execute(
            """
            DELETE FROM projects
            WHERE id = ?
            """,
            (project_id,),
        )

        connection.commit()

        if result.rowcount == 0:

            raise HTTPException(
                status_code=404,
                detail="Project not found.",
            )

        return {
            "status": "success",
            "deleted": project_id,
        }

    finally:

        connection.close()


# ============================================================
# PROJECT MASTER API
# ============================================================

@app.post("/api/project/run")
async def project_run(
    request: ProjectAgentRequest,
):

    start = time.time()

    record("requests")
    record("project_requests")

    files = validate_project_files(
        request.files
    )

    project_id = uuid.uuid4().hex

    save_project_db(
        project_id,
        request.project_name,
        request.task,
        files,
    )

    context = build_project_context(
        files
    )

    result = await run_multi_agent_workflow(
        task=request.task,
        context=context,
        include_tests=request.include_tests,
        include_security=request.include_security,
        include_performance=request.include_performance,
    )

    return {
        "status": "success",
        "project_id": project_id,
        "project_name":
            request.project_name,
        "statistics":
            project_statistics(files),
        "workflow":
            result["workflow"],
        "final":
            result["final"],
        "agent_count":
            result["agent_count"],
        "latency_seconds":
            round(
                time.time() - start,
                3,
            ),
    }


# ============================================================
# MEMORY
# ============================================================

@app.post("/api/project/memory/save")
async def memory_save(
    request: MemoryRequest,
):

    project = get_project_db(
        request.project_id
    )

    if not project:

        raise HTTPException(
            status_code=404,
            detail="Project not found.",
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
                now,
            ),
        )

        connection.commit()

    finally:

        connection.close()

    return {
        "status": "success",
        "memory_id": memory_id,
    }


@app.get("/api/project/memory/{project_id}")
async def memory_get(
    project_id: str,
):

    connection = db_connect()

    try:

        rows = connection.execute(
            """
            SELECT id,
                   memory_key,
                   memory_value,
                   created_at,
                   updated_at
            FROM memory
            WHERE project_id = ?
            ORDER BY updated_at DESC
            """,
            (project_id,),
        ).fetchall()

        return {
            "status": "success",
            "memory": [
                dict(row)
                for row in rows
            ],
        }

    finally:

        connection.close()


@app.delete("/api/project/memory/{memory_id}")
async def memory_delete(
    memory_id: str,
):

    connection = db_connect()

    try:

        result = connection.execute(
            """
            DELETE FROM memory
            WHERE id = ?
            """,
            (memory_id,),
        )

        connection.commit()

        return {
            "status": "success",
            "deleted":
                result.rowcount > 0,
        }

    finally:

        connection.close()


@app.post("/api/project/memory/ask")
async def memory_ask(
    request: MemoryAskRequest,
):

    connection = db_connect()

    try:

        rows = connection.execute(
            """
            SELECT memory_key,
                   memory_value
            FROM memory
            WHERE project_id = ?
            ORDER BY updated_at DESC
            LIMIT 50
            """,
            (request.project_id,),
        ).fetchall()

    finally:

        connection.close()

    memory_context = "\n".join(
        f"{row['memory_key']}: "
        f"{row['memory_value']}"
        for row in rows
    )

        answer = await infer(
        f"Answer the user's engineering question "
        f"using the saved project memory.\n\n"
        f"MEMORY:\n"
        f"{memory_context}\n\n"
        f"QUESTION:\n"
        f"{request.question}"
        )

    return {
        "status": "success",
        "answer": answer,
        "memory_items":
            len(rows),
    }


# ============================================================
# ANALYTICS
# ============================================================

@app.get("/api/ops/analytics")
async def ops_analytics():

    requests = analytics_data[
        "requests"
    ]

    average_latency = (
        analytics_data[
            "total_latency_seconds"
        ]
        / max(requests, 1)
    )

    return {
        "status": "success",
        "metrics": {
            **analytics_data,
            "average_latency_seconds":
                round(
                    average_latency,
                    3,
                ),
        },
    }


@app.get("/api/status")
async def api_status():

    return {
        "status": "online",
        "service": APP_TITLE,
        "version": APP_VERSION,
        "ai_provider":
            DEFAULT_PROVIDER,
        "ai_model":
            DEFAULT_MODEL,
        "execution_mode":
            EXECUTION_MODE,
        "database":
            DATABASE_PATH,
        "features": {
            "chat": True,
            "agents": True,
            "projects": True,
            "project_memory": True,
            "code_review": True,
            "execution": True,
            "testing": True,
            "terminal": True,
            "debugger": True,
            "security": True,
            "auto_fix": True,
            "benchmarking": True,
            "analytics": True,
        },
    }


# ============================================================
# ROOT / FRONTEND
# ============================================================

@app.get("/")
async def root():

    index = Path(
        "index.html"
    )

    if index.exists():

        return FileResponse(
            index
        )

    return {
        "service": APP_TITLE,
        "version": APP_VERSION,
        "status": "online",
        "message":
            "Engineer AI backend is running.",
    }


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup():

    initialize_database()

    logger.info(
        "Engineer AI v%s started.",
        APP_VERSION,
    )

    logger.info(
        "Execution mode: %s",
        EXECUTION_MODE,
    )


@app.on_event("shutdown")
async def shutdown():

    logger.info(
        "Engineer AI shutting down."
    )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    import uvicorn

    host = os.getenv(
        "HOST",
        "0.0.0.0",
    )

    port = int(
        os.getenv(
            "PORT",
            "8000",
        )
    )

    uvicorn.run(
        app,
        host=host,
        port=port,
    )
