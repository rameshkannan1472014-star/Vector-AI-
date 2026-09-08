import os
import time
import json
import uuid
import shutil
import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional, List

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from google import genai
from google.genai import types


# ============================================================
# ENGINEER AI
# COMPLETE BACKEND
# PART 1/5
# ============================================================


APP_TITLE = "Engineer AI Engine"
APP_VERSION = "4.0.0"


# ------------------------------------------------------------
# CONFIGURATION
# ------------------------------------------------------------

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


# ------------------------------------------------------------
# EXECUTION CONFIGURATION
# ------------------------------------------------------------

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


# ------------------------------------------------------------
# LOGGING
# ------------------------------------------------------------

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


# ------------------------------------------------------------
# FASTAPI
# ------------------------------------------------------------

app = FastAPI(
    title=APP_TITLE,
    version=APP_VERSION,
    description=(
        "Engineer AI engineering backend "
        "with AI agents, project intelligence, "
        "testing and safe execution."
    ),
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------------------------------------
# ANALYTICS
# ------------------------------------------------------------

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

    "total_latency_seconds": 0.0,
}


# ------------------------------------------------------------
# REQUEST MODELS
# ------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str = Field(
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


# ------------------------------------------------------------
# CORE SYSTEM INSTRUCTION
# ------------------------------------------------------------

SYSTEM_INSTRUCTION = """
You are Engineer AI, an advanced software engineering
assistant.

Your job is to help users design, understand, write,
debug, test, review and improve software.

Priorities:

1. Correctness
2. Security
3. Reliability
4. Maintainability
5. Performance
6. Clear engineering explanations

When working with projects:

- Understand the existing code before changing it.
- Preserve existing functionality unless a change is required.
- Do not invent files that are unnecessary.
- Do not expose secrets.
- Avoid destructive operations.
- Prefer small, reliable changes.
- Explain important assumptions.
- Produce valid code.
- Consider edge cases.
- Consider security implications.
- Consider testing.

When asked to modify a project, return structured,
actionable engineering information.

You are an engineering system, not merely a chatbot.
"""


# ------------------------------------------------------------
# UTILITY FUNCTIONS
# ------------------------------------------------------------

def clamp_text(
    value: Any,
    limit: int,
) -> str:

    text = str(value)

    if len(text) <= limit:
        return text

    return text[:limit] + "\n...[truncated]"


def record(
    key: str,
    amount: int = 1,
) -> None:

    analytics_data[key] = (
        analytics_data.get(key, 0)
        + amount
    )


def record_latency(
    start_time: float,
) -> None:

    analytics_data[
        "total_latency_seconds"
    ] += time.time() - start_time


def is_retryable_error(
    error: Exception,
) -> bool:

    message = str(error).lower()

    retry_words = [
        "timeout",
        "temporarily",
        "unavailable",
        "rate limit",
        "429",
        "500",
        "503",
        "connection",
    ]

    return any(
        word in message
        for word in retry_words
    )


# ------------------------------------------------------------
# GEMINI CLIENT
# ------------------------------------------------------------

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


# ------------------------------------------------------------
# LOW-LEVEL AI GENERATION
# ------------------------------------------------------------

def generate_sync(
    prompt: str,
    model: Optional[str] = None,
    json_mode: bool = False,
) -> str:

    prompt = clamp_text(
        prompt,
        MAX_PROMPT_LENGTH,
    )

    selected_model = (
        model
        or DEFAULT_MODEL
    )

    client = get_client()

    config_kwargs = {
        "system_instruction":
            SYSTEM_INSTRUCTION,
    }

    if json_mode:
        config_kwargs[
            "response_mime_type"
        ] = "application/json"

    config = types.GenerateContentConfig(
        **config_kwargs
    )

    last_error = None

    for attempt in range(
        AI_RETRIES + 1
    ):

        try:

            response = (
                client.models.generate_content(
                    model=selected_model,
                    contents=prompt,
                    config=config,
                )
            )

            text = getattr(
                response,
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

            logger.exception(
                "AI generation failed "
                "on attempt %s",
                attempt + 1,
            )

            if (
                attempt < AI_RETRIES
                and is_retryable_error(error)
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


# ------------------------------------------------------------
# GENERIC AGENT
# ------------------------------------------------------------

async def run_agent(
    name: str,
    task: str,
    context: str = "",
    model: Optional[str] = None,
) -> Dict[str, Any]:

    start = time.time()

    prompt = f"""
ENGINEERING AGENT: {name}

TASK:
{task}

CONTEXT:
{context}

Perform the task carefully.

Return:

1. Analysis
2. Findings
3. Recommended action
4. Implementation details
5. Risks
6. Tests to perform
"""

    try:

        output = await infer(
            prompt,
            model=model,
        )

        return {
            "agent": name,
            "status": "success",
            "output": output,
            "latency_seconds": round(
                time.time() - start,
                3,
            ),
        }

    except Exception as error:

        return {
            "agent": name,
            "status": "failed",
            "output": "",
            "error": str(error),
            "latency_seconds": round(
                time.time() - start,
                3,
            ),
        }


# ------------------------------------------------------------
# FILE PATH SAFETY
# ------------------------------------------------------------

def safe_relative_path(
    path: str,
) -> str:

    if not path:
        raise ValueError(
            "File path cannot be empty."
        )

    normalized = path.replace(
        "\\",
        "/",
    ).strip()

    candidate = Path(normalized)

    if candidate.is_absolute():
        raise ValueError(
            "Absolute paths are not allowed."
        )

    parts = candidate.parts

    if ".." in parts:
        raise ValueError(
            "Parent-directory traversal "
            "is not allowed."
        )

    if normalized.startswith("/"):
        raise ValueError(
            "Absolute paths are not allowed."
        )

    return str(candidate)


# ------------------------------------------------------------
# PROJECT VALIDATION
# ------------------------------------------------------------

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


def validate_project_files(
    files: List[ProjectFile],
) -> List[ProjectFile]:

    if not files:
        raise ValueError(
            "Project must contain at least one file."
        )

    if len(files) > MAX_PROJECT_FILES:
        raise ValueError(
            f"Project exceeds the maximum "
            f"of {MAX_PROJECT_FILES} files."
        )

    total_size = 0

    validated = []

    for item in files:

        path = safe_relative_path(
            item.path
        )

        suffix = Path(path).suffix.lower()

        if (
            suffix
            and suffix not in
            ALLOWED_PROJECT_EXTENSIONS
        ):
            raise ValueError(
                f"Unsupported project file type: "
                f"{suffix}"
            )

        if len(item.content) > MAX_FILE_SIZE:
            raise ValueError(
                f"File too large: {path}"
            )

        total_size += len(item.content)

        validated.append(
            ProjectFile(
                path=path,
                content=item.content,
            )
        )

    if total_size > MAX_PROJECT_SIZE:
        raise ValueError(
            "Project exceeds maximum size."
        )

    return validated


# ------------------------------------------------------------
# PROJECT CONTEXT
# ------------------------------------------------------------

def build_project_context(
    files: List[ProjectFile],
) -> str:

    sections = []

    for item in files:

        sections.append(
            f"""
===== FILE: {item.path} =====

{clamp_text(
    item.content,
    MAX_FILE_SIZE,
)}
"""
        )

    return clamp_text(
        "\n".join(sections),
        MAX_PROJECT_SIZE,
    )


# ------------------------------------------------------------
# BASIC CODE ANALYSIS
# ------------------------------------------------------------

def detect_language(
    path: str,
) -> str:

    suffix = Path(path).suffix.lower()

    mapping = {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".html": "html",
        ".css": "css",
        ".sql": "sql",
        ".json": "json",
        ".sh": "shell",
    }

    return mapping.get(
        suffix,
        "text",
    )


def basic_project_statistics(
    files: List[ProjectFile],
) -> Dict[str, Any]:

    languages = {}

    total_lines = 0
    total_bytes = 0

    for item in files:

        language = detect_language(
            item.path
        )

        languages[language] = (
            languages.get(language, 0)
            + 1
        )

        total_lines += (
            item.content.count("\n")
            + 1
        )

        total_bytes += len(
            item.content.encode(
                "utf-8",
                errors="ignore",
            )
        )

    return {
        "file_count": len(files),
        "line_count": total_lines,
        "size_bytes": total_bytes,
        "languages": languages,
    }


# ------------------------------------------------------------
# HEALTH
# ------------------------------------------------------------

@app.get("/api/health")
async def health():

    docker_available = (
        shutil.which("docker")
        is not None
    )

    return {
        "status": "ok",
        "service": APP_TITLE,
        "version": APP_VERSION,
        "provider": DEFAULT_PROVIDER,
        "model": DEFAULT_MODEL,
        "execution_mode": EXECUTION_MODE,
        "docker_available": docker_available,
        "gemini_configured": bool(
            GEMINI_API_KEY
        ),
    }


# ------------------------------------------------------------
# CHAT API
# ------------------------------------------------------------

@app.post("/api/chat")
async def chat(
    request: ChatRequest,
):

    start = time.time()

    record("requests")
    record("chat_requests")

    try:

        output = await infer(
            request.message,
            model=request.model,
        )

        record(
            "successful_requests"
        )

        return {
            "status": "success",
            "message": output,
            "model": (
                request.model
                or DEFAULT_MODEL
            ),
            "latency_seconds": round(
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
# PART 2/5
# MULTI-AGENT ENGINEERING + PROJECT INTELLIGENCE
# ============================================================


# ------------------------------------------------------------
# AGENT PROMPTS
# ------------------------------------------------------------

AGENT_ROLES = {
    "planner": """
You are the Lead Software Architect.

Break the user's engineering task into a practical
implementation plan.

Identify:
- requirements
- architecture
- files that need changes
- dependencies
- risks
- testing strategy

Do not write unnecessary code.
""",

    "coder": """
You are the Senior Software Engineer.

Implement the requested engineering changes.

Priorities:
- correctness
- clean architecture
- maintainability
- security
- compatibility

When code changes are required, provide complete
replacement file contents or precise file changes.
""",

    "debugger": """
You are a Senior Debugging Engineer.

Analyze errors, failing tests, exceptions and
unexpected behavior.

Find the root cause rather than merely treating
the symptom.

Return:
- root cause
- affected files
- exact fix
- verification steps
""",

    "tester": """
You are a Software Testing Engineer.

Analyze the project and determine how it should
be tested.

Look for:
- syntax errors
- logic errors
- edge cases
- integration problems
- regression risks

Recommend or generate appropriate tests.
""",

    "security": """
You are an Application Security Engineer.

Review the project for:
- unsafe input handling
- authentication problems
- authorization problems
- secrets exposure
- injection risks
- path traversal
- insecure execution
- dependency risks
- unsafe defaults

Prioritize practical fixes.
""",

    "performance": """
You are a Performance Engineer.

Analyze the project for:
- slow operations
- unnecessary computation
- excessive memory use
- inefficient algorithms
- unnecessary network calls
- scalability problems

Recommend improvements without sacrificing
correctness or security.
""",
}


# ------------------------------------------------------------
# SPECIALIZED AGENT
# ------------------------------------------------------------

async def run_specialized_agent(
    role: str,
    task: str,
    project_context: str,
    model: Optional[str] = None,
) -> Dict[str, Any]:

    role_instruction = AGENT_ROLES.get(
        role,
        AGENT_ROLES["planner"],
    )

    prompt = f"""
{role_instruction}

USER TASK:
{task}

PROJECT:
{project_context}

Return a structured engineering response.
"""

    start = time.time()

    try:

        output = await infer(
            prompt,
            model=model,
        )

        return {
            "agent": role,
            "status": "success",
            "output": output,
            "latency_seconds": round(
                time.time() - start,
                3,
            ),
        }

    except Exception as error:

        logger.exception(
            "Agent '%s' failed",
            role,
        )

        return {
            "agent": role,
            "status": "failed",
            "output": "",
            "error": str(error),
            "latency_seconds": round(
                time.time() - start,
                3,
            ),
        }


# ------------------------------------------------------------
# MULTI-AGENT ENGINE
# ------------------------------------------------------------

async def run_multi_agent_workflow(
    task: str,
    project_context: str = "",
    include_tests: bool = True,
    include_security: bool = True,
    include_performance: bool = False,
    model: Optional[str] = None,
) -> Dict[str, Any]:

    start = time.time()

    record("agent_requests")

    if not task.strip():
        raise ValueError(
            "Task cannot be empty."
        )

    # --------------------------------------------------------
    # STEP 1: PLANNER
    # --------------------------------------------------------

    planner = await run_specialized_agent(
        "planner",
        task,
        project_context,
        model,
    )

    planner_output = planner.get(
        "output",
        "",
    )

    # --------------------------------------------------------
    # STEP 2: PARALLEL ENGINEERING ANALYSIS
    # --------------------------------------------------------

    agent_tasks = [
        run_specialized_agent(
            "coder",
            task,
            (
                project_context
                + "\n\nPLANNER OUTPUT:\n"
                + planner_output
            ),
            model,
        ),
        run_specialized_agent(
            "debugger",
            task,
            (
                project_context
                + "\n\nPLANNER OUTPUT:\n"
                + planner_output
            ),
            model,
        ),
    ]

    if include_tests:

        agent_tasks.append(
            run_specialized_agent(
                "tester",
                task,
                (
                    project_context
                    + "\n\nPLANNER OUTPUT:\n"
                    + planner_output
                ),
                model,
            )
        )

    if include_security:

        agent_tasks.append(
            run_specialized_agent(
                "security",
                task,
                (
                    project_context
                    + "\n\nPLANNER OUTPUT:\n"
                    + planner_output
                ),
                model,
            )
        )

    if include_performance:

        agent_tasks.append(
            run_specialized_agent(
                "performance",
                task,
                (
                    project_context
                    + "\n\nPLANNER OUTPUT:\n"
                    + planner_output
                ),
                model,
            )
        )

    results = await asyncio.gather(
        *agent_tasks
    )

    workflow = {
        "planner": planner,
        "agents": results,
    }

    # --------------------------------------------------------
    # STEP 3: FINAL SYNTHESIS
    # --------------------------------------------------------

    synthesis_prompt = f"""
You are the Lead Engineer reviewing the work of
multiple engineering agents.

USER TASK:
{task}

PROJECT:
{project_context}

PLANNER:
{planner_output}

AGENT RESULTS:
{json.dumps(
    results,
    indent=2,
)}

Create one final engineering recommendation.

Include:
1. What should be changed
2. Why it should be changed
3. Files affected
4. Important implementation details
5. Tests required
6. Security concerns
7. Performance concerns
8. Final recommended approach

Do not blindly trust an agent.
Resolve contradictions yourself.
"""

    try:

        synthesis = await infer(
            synthesis_prompt,
            model=model,
        )

    except Exception as error:

        synthesis = (
            "Synthesis failed: "
            + str(error)
        )

    record(
        "successful_requests"
    )

    return {
        "status": "success",
        "workflow": workflow,
        "final_recommendation": synthesis,
        "latency_seconds": round(
            time.time() - start,
            3,
        ),
    }


# ------------------------------------------------------------
# PROJECT ARCHITECTURE ANALYSIS
# ------------------------------------------------------------

async def analyze_project(
    files: List[ProjectFile],
    task: str,
    model: Optional[str] = None,
) -> Dict[str, Any]:

    validated = validate_project_files(
        files
    )

    context = build_project_context(
        validated
    )

    statistics = basic_project_statistics(
        validated
    )

    prompt = f"""
Analyze this software project as a senior
software architect.

TASK:
{task}

PROJECT STATISTICS:
{json.dumps(
    statistics,
    indent=2,
)}

PROJECT FILES:
{context}

Determine:

- project purpose
- architecture
- major components
- relationships between components
- likely entry points
- dependencies
- potential architectural weaknesses
- security concerns
- scalability concerns
- recommended improvements

Do not invent components that are not supported
by the project.
"""

    output = await infer(
        prompt,
        model=model,
    )

    return {
        "status": "success",
        "statistics": statistics,
        "analysis": output,
    }


# ------------------------------------------------------------
# DEPENDENCY ANALYSIS
# ------------------------------------------------------------

async def analyze_dependencies(
    files: List[ProjectFile],
    model: Optional[str] = None,
) -> Dict[str, Any]:

    validated = validate_project_files(
        files
    )

    context = build_project_context(
        validated
    )

    prompt = f"""
Analyze the dependencies of this software project.

PROJECT:
{context}

Identify:

- Python dependencies
- Node dependencies
- package files
- framework usage
- external services
- APIs
- suspicious or unnecessary dependencies
- missing dependencies that are clearly required

Do not invent package versions.

Return a concise engineering report.
"""

    output = await infer(
        prompt,
        model=model,
    )

    return {
        "status": "success",
        "dependencies": output,
    }


# ------------------------------------------------------------
# PROJECT SECURITY ANALYSIS
# ------------------------------------------------------------

async def analyze_project_security(
    files: List[ProjectFile],
    model: Optional[str] = None,
) -> Dict[str, Any]:

    validated = validate_project_files(
        files
    )

    context = build_project_context(
        validated
    )

    result = await run_specialized_agent(
        "security",
        (
            "Perform a complete security review "
            "of this project."
        ),
        context,
        model,
    )

    return result


# ------------------------------------------------------------
# PROJECT PERFORMANCE ANALYSIS
# ------------------------------------------------------------

async def analyze_project_performance(
    files: List[ProjectFile],
    model: Optional[str] = None,
) -> Dict[str, Any]:

    validated = validate_project_files(
        files
    )

    context = build_project_context(
        validated
    )

    result = await run_specialized_agent(
        "performance",
        (
            "Perform a complete performance review "
            "of this project."
        ),
        context,
        model,
    )

    return result


# ------------------------------------------------------------
# PROJECT DEBUGGING
# ------------------------------------------------------------

async def debug_project(
    files: List[ProjectFile],
    problem: str,
    model: Optional[str] = None,
) -> Dict[str, Any]:

    validated = validate_project_files(
        files
    )

    context = build_project_context(
        validated
    )

    result = await run_specialized_agent(
        "debugger",
        problem,
        context,
        model,
    )

    return result


# ------------------------------------------------------------
# PROJECT TEST PLAN
# ------------------------------------------------------------

async def generate_test_plan(
    files: List[ProjectFile],
    task: str,
    model: Optional[str] = None,
) -> Dict[str, Any]:

    validated = validate_project_files(
        files
    )

    context = build_project_context(
        validated
    )

    result = await run_specialized_agent(
        "tester",
        (
            "Create a comprehensive test plan "
            "for this project.\n\n"
            f"Task:\n{task}"
        ),
        context,
        model,
    )

    return result


# ------------------------------------------------------------
# PROJECT MASTER WORKFLOW
# ------------------------------------------------------------

async def run_project_workflow(
    request: ProjectAgentRequest,
) -> Dict[str, Any]:

    validated = validate_project_files(
        request.files
    )

    context = build_project_context(
        validated
    )

    statistics = basic_project_statistics(
        validated
    )

    result = await run_multi_agent_workflow(
        task=request.task,
        project_context=context,
        include_tests=request.include_tests,
        include_security=request.include_security,
        include_performance=request.include_performance,
    )

    return {
        "status": "success",
        "project_name": request.project_name,
        "statistics": statistics,
        "workflow": result,
    }


# ------------------------------------------------------------
# EXISTING UI-COMPATIBLE AGENT ENDPOINT
# ------------------------------------------------------------

@app.post("/api/agent/execute")
async def agent_execute(
    request: MultiAgentRequest,
):

    task = (
        request.task_description
        or request.task
        or ""
    ).strip()

    if not task:

        raise HTTPException(
            status_code=400,
            detail="Task is required.",
        )

    start = time.time()

    try:

        result = (
            await run_multi_agent_workflow(
                task=task,
                include_tests=(
                    request.include_tests
                ),
                include_security=(
                    request.include_security
                ),
            )
        )

        result["latency_seconds"] = round(
            time.time() - start,
            3,
        )

        return result

    except Exception as error:

        record("failed_requests")

        raise HTTPException(
            status_code=500,
            detail=str(error),
        )


# ------------------------------------------------------------
# CODE REVIEW
# ------------------------------------------------------------

@app.post("/api/code/review")
async def code_review(
    request: CodeReviewRequest,
):

    start = time.time()

    prompt = f"""
Perform a professional code review.

LANGUAGE:
{request.language}

FOCUS:
{request.focus or "general quality"}

CODE:
{request.code}

Review for:

- correctness
- bugs
- security
- maintainability
- performance
- readability
- edge cases
- testing

Return:

1. Summary
2. Critical issues
3. Important issues
4. Improvements
5. Suggested corrected code where useful
6. Testing recommendations
"""

    try:

        output = await infer(
            prompt
        )

        record(
            "successful_requests"
        )

        return {
            "status": "success",
            "review": {
                "output": output
            },
            "latency_seconds": round(
                time.time() - start,
                3,
            ),
        }

    except Exception as error:

        record("failed_requests")

        raise HTTPException(
            status_code=500,
            detail=str(error),
        )


# ------------------------------------------------------------
# PROJECT ENDPOINTS
# ------------------------------------------------------------

@app.post("/api/project/analyze")
async def project_analyze(
    request: ProjectRequest,
):

    try:

        return await analyze_project(
            request.files,
            request.task,
        )

    except Exception as error:

        raise HTTPException(
            status_code=400,
            detail=str(error),
        )


@app.post("/api/project/architecture")
async def project_architecture(
    request: ProjectRequest,
):

    try:

        return await analyze_project(
            request.files,
            (
                request.task
                + "\nFocus specifically on architecture."
            ),
        )

    except Exception as error:

        raise HTTPException(
            status_code=400,
            detail=str(error),
        )


@app.post("/api/project/dependencies")
async def project_dependencies(
    request: ProjectRequest,
):

    try:

        return await analyze_dependencies(
            request.files
        )

    except Exception as error:

        raise HTTPException(
            status_code=400,
            detail=str(error),
        )


@app.post("/api/project/debug")
async def project_debug(
    request: ProjectRequest,
):

    try:

        return await debug_project(
            request.files,
            request.task,
        )

    except Exception as error:

        raise HTTPException(
            status_code=400,
            detail=str(error),
        )


@app.post("/api/project/tests")
async def project_tests(
    request: ProjectRequest,
):

    try:

        return await generate_test_plan(
            request.files,
            request.task,
        )

    except Exception as error:

        raise HTTPException(
            status_code=400,
            detail=str(error),
        )


@app.post("/api/project/security")
async def project_security(
    request: ProjectRequest,
):

    try:

        return await analyze_project_security(
            request.files
        )

    except Exception as error:

        raise HTTPException(
            status_code=400,
            detail=str(error),
        )


@app.post("/api/project/performance")
async def project_performance(
    request: ProjectRequest,
):

    try:

        return await analyze_project_performance(
            request.files
        )

    except Exception as error:

        raise HTTPException(
            status_code=400,
            detail=str(error),
        )


@app.post("/api/project/agent")
async def project_agent(
    request: ProjectAgentRequest,
):

    try:

        return await run_project_workflow(
            request
        )

    except Exception as error:

        raise HTTPException(
            status_code=400,
            detail=str(error),
        )
# ============================================================
# PART 3/5
# SAFE CODE EXECUTION + TEST RUNNER
# ============================================================


# ------------------------------------------------------------
# EXECUTION MODELS
# ------------------------------------------------------------

class ExecutionFile(BaseModel):
    path: str

    content: str = Field(
        max_length=MAX_FILE_SIZE
    )


class ExecuteRequest(BaseModel):
    files: List[ExecutionFile]

    command: Optional[str] = None

    language: str = "python"

    timeout_seconds: int = Field(
        default=EXECUTION_TIMEOUT_SECONDS,
        ge=1,
        le=60,
    )


class TestRequest(BaseModel):
    files: List[ExecutionFile]

    command: Optional[str] = None

    language: str = "python"

    timeout_seconds: int = Field(
        default=EXECUTION_TIMEOUT_SECONDS,
        ge=1,
        le=60,
    )


class ExecutionResult(BaseModel):
    status: str

    exit_code: Optional[int] = None

    stdout: str = ""

    stderr: str = ""

    command: Optional[str] = None

    duration_seconds: float = 0.0


# ------------------------------------------------------------
# SAFE EXECUTION COMMANDS
# ------------------------------------------------------------

SAFE_COMMANDS = {
    "python",
    "python3",
    "pytest",
    "node",
    "npm",
}


def validate_execution_files(
    files: List[ExecutionFile],
) -> List[ExecutionFile]:

    if not files:
        raise ValueError(
            "At least one execution file is required."
        )

    if len(files) > MAX_PROJECT_FILES:
        raise ValueError(
            f"Too many files. Maximum: "
            f"{MAX_PROJECT_FILES}"
        )

    total_size = 0
    validated = []

    for item in files:

        path = safe_relative_path(
            item.path
        )

        if len(item.content) > MAX_FILE_SIZE:
            raise ValueError(
                f"File too large: {path}"
            )

        total_size += len(
            item.content.encode(
                "utf-8",
                errors="ignore",
            )
        )

        validated.append(
            ExecutionFile(
                path=path,
                content=item.content,
            )
        )

    if total_size > MAX_PROJECT_SIZE:
        raise ValueError(
            "Execution project is too large."
        )

    return validated


# ------------------------------------------------------------
# COMMAND VALIDATION
# ------------------------------------------------------------

def validate_command(
    command: str,
) -> List[str]:

    if not command:
        raise ValueError(
            "Command cannot be empty."
        )

    command = command.strip()

    # No shell execution.
    forbidden = [
        ";",
        "&&",
        "||",
        "|",
        ">",
        "<",
        "`",
        "$(",
        "\n",
        "\r",
    ]

    for token in forbidden:

        if token in command:
            raise ValueError(
                "Shell operators are not allowed."
            )

    parts = command.split()

    if not parts:
        raise ValueError(
            "Invalid command."
        )

    executable = Path(
        parts[0]
    ).name.lower()

    if executable not in SAFE_COMMANDS:
        raise ValueError(
            f"Command '{executable}' "
            "is not permitted."
        )

    # Prevent direct path execution.
    if "/" in parts[0] or "\\" in parts[0]:
        raise ValueError(
            "Executable paths are not allowed."
        )

    # Keep arguments conservative.
    dangerous_arguments = [
        "--privileged",
        "--network",
        "--mount",
        "--volume",
        "-v",
        "--rm",
        "sudo",
        "su",
        "chmod",
        "chown",
        "curl",
        "wget",
    ]

    for argument in parts[1:]:

        if argument in dangerous_arguments:
            raise ValueError(
                f"Argument '{argument}' "
                "is not allowed."
            )

    return parts


# ------------------------------------------------------------
# OUTPUT PROTECTION
# ------------------------------------------------------------

def clamp_execution_output(
    text: str,
) -> str:

    if not text:
        return ""

    if len(text) <= EXECUTION_OUTPUT_LIMIT:
        return text

    return (
        text[:EXECUTION_OUTPUT_LIMIT]
        + "\n...[output truncated]"
    )


# ------------------------------------------------------------
# TEMPORARY WORKSPACE
# ------------------------------------------------------------

def create_workspace() -> Path:

    base = Path(
        os.getenv(
            "ENGINEER_WORKSPACE",
            "/tmp",
        )
    )

    base.mkdir(
        parents=True,
        exist_ok=True,
    )

    workspace = (
        base
        / f"engineer-ai-{uuid.uuid4().hex}"
    )

    workspace.mkdir(
        parents=True,
        exist_ok=False,
    )

    return workspace


def cleanup_workspace(
    workspace: Path,
) -> None:

    try:

        if workspace.exists():

            shutil.rmtree(
                workspace,
                ignore_errors=True,
            )

    except Exception:

        logger.exception(
            "Workspace cleanup failed."
        )


# ------------------------------------------------------------
# WRITE PROJECT FILES
# ------------------------------------------------------------

def write_execution_files(
    workspace: Path,
    files: List[ExecutionFile],
) -> None:

    total_size = 0

    for item in files:

        relative = safe_relative_path(
            item.path
        )

        destination = (
            workspace
            / relative
        )

        destination.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        content_bytes = item.content.encode(
            "utf-8",
            errors="ignore",
        )

        total_size += len(
            content_bytes
        )

        if total_size > MAX_PROJECT_SIZE:
            raise ValueError(
                "Project size limit exceeded."
            )

        destination.write_bytes(
            content_bytes
        )


# ------------------------------------------------------------
# ENVIRONMENT
# ------------------------------------------------------------

def build_execution_environment(
    workspace: Path,
) -> Dict[str, str]:

    # Deliberately do NOT forward the server's
    # environment variables into generated code.

    return {
        "HOME": "/tmp",
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "NODE_ENV": "test",
        "ENGINEER_AI_SANDBOX": "1",
    }


# ------------------------------------------------------------
# LANGUAGE / IMAGE SELECTION
# ------------------------------------------------------------

def select_docker_image(
    language: str,
) -> str:

    language = (
        language
        or "python"
    ).lower()

    if language in {
        "python",
        "py",
    }:

        return PYTHON_DOCKER_IMAGE

    if language in {
        "javascript",
        "js",
        "node",
        "typescript",
        "ts",
    }:

        return NODE_DOCKER_IMAGE

    raise ValueError(
        f"Unsupported execution language: "
        f"{language}"
    )


# ------------------------------------------------------------
# DEFAULT TEST COMMAND
# ------------------------------------------------------------

def default_test_command(
    language: str,
) -> str:

    language = (
        language
        or "python"
    ).lower()

    if language in {
        "python",
        "py",
    }:

        return (
            "python -m unittest discover -v"
        )

    if language in {
        "javascript",
        "js",
        "node",
        "typescript",
        "ts",
    }:

        return "npm test"

    raise ValueError(
        f"No default test command for "
        f"language: {language}"
    )


# ------------------------------------------------------------
# HOST PROCESS RUNNER
# ------------------------------------------------------------

def run_host_process(
    workspace: Path,
    command_parts: List[str],
    timeout_seconds: int,
) -> Dict[str, Any]:

    if not ALLOW_UNSANDBOXED_EXECUTION:

        raise RuntimeError(
            "Unsandboxed execution is disabled. "
            "Use Docker execution."
        )

    start = time.time()

    environment = (
        build_execution_environment(
            workspace
        )
    )

    try:

        process = subprocess.run(
            command_parts,
            cwd=str(workspace),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            shell=False,
            check=False,
        )

        return {
            "status": (
                "success"
                if process.returncode == 0
                else "failed"
            ),
            "exit_code": process.returncode,
            "stdout": clamp_execution_output(
                process.stdout.decode(
                    "utf-8",
                    errors="replace",
                )
            ),
            "stderr": clamp_execution_output(
                process.stderr.decode(
                    "utf-8",
                    errors="replace",
                )
            ),
            "duration_seconds": round(
                time.time() - start,
                3,
            ),
        }

    except subprocess.TimeoutExpired:

        return {
            "status": "timeout",
            "exit_code": None,
            "stdout": "",
            "stderr": (
                "Execution timed out."
            ),
            "duration_seconds": round(
                time.time() - start,
                3,
            ),
        }


# ------------------------------------------------------------
# DOCKER PROCESS RUNNER
# ------------------------------------------------------------

def run_docker_process(
    workspace: Path,
    command_parts: List[str],
    language: str,
    timeout_seconds: int,
) -> Dict[str, Any]:

    if shutil.which("docker") is None:

        raise RuntimeError(
            "Docker is not available on this server."
        )

    image = select_docker_image(
        language
    )

    start = time.time()

    workspace_path = str(
        workspace.resolve()
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

        "--mount",
        (
            "type=bind,"
            f"source={workspace_path},"
            "target=/workspace,"
            "readonly"
        ),

        "--workdir",
        "/workspace",

        image,
    ]

    docker_command.extend(
        command_parts
    )

    try:

        process = subprocess.run(
            docker_command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            shell=False,
            check=False,
        )

        return {
            "status": (
                "success"
                if process.returncode == 0
                else "failed"
            ),
            "exit_code": process.returncode,
            "stdout": clamp_execution_output(
                process.stdout.decode(
                    "utf-8",
                    errors="replace",
                )
            ),
            "stderr": clamp_execution_output(
                process.stderr.decode(
                    "utf-8",
                    errors="replace",
                )
            ),
            "duration_seconds": round(
                time.time() - start,
                3,
            ),
        }

    except subprocess.TimeoutExpired:

        return {
            "status": "timeout",
            "exit_code": None,
            "stdout": "",
            "stderr": (
                "Docker execution timed out."
            ),
            "duration_seconds": round(
                time.time() - start,
                3,
            ),
        }


# ------------------------------------------------------------
# EXECUTION ENGINE
# ------------------------------------------------------------

def execute_files(
    files: List[ExecutionFile],
    command: str,
    language: str,
    timeout_seconds: int,
) -> Dict[str, Any]:

    validated = validate_execution_files(
        files
    )

    command_parts = validate_command(
        command
    )

    workspace = create_workspace()

    try:

        write_execution_files(
            workspace,
            validated,
        )

        if EXECUTION_MODE.lower() == "docker":

            result = run_docker_process(
                workspace,
                command_parts,
                language,
                timeout_seconds,
            )

        elif (
            EXECUTION_MODE.lower()
            == "host"
        ):

            result = run_host_process(
                workspace,
                command_parts,
                timeout_seconds,
            )

        else:

            raise RuntimeError(
                f"Unknown execution mode: "
                f"{EXECUTION_MODE}"
            )

        result["command"] = command

        return result

    finally:

        cleanup_workspace(
            workspace
        )


# ------------------------------------------------------------
# EXECUTE API
# ------------------------------------------------------------

@app.post("/api/execute")
async def execute_code(
    request: ExecuteRequest,
):

    start = time.time()

    record(
        "execution_requests"
    )

    if request.command is None:

        language = (
            request.language
            or "python"
        ).lower()

        if language in {
            "python",
            "py",
        }:

            request.command = (
                "python main.py"
            )

        elif language in {
            "javascript",
            "js",
            "node",
        }:

            request.command = (
                "node index.js"
            )

        else:

            raise HTTPException(
                status_code=400,
                detail=(
                    "A command is required "
                    "for this language."
                ),
            )

    try:

        result = await asyncio.to_thread(
            execute_files,
            request.files,
            request.command,
            request.language,
            request.timeout_seconds,
        )

        if result["status"] == "success":

            record(
                "execution_successes"
            )

        else:

            record(
                "execution_failures"
            )

        result["latency_seconds"] = round(
            time.time() - start,
            3,
        )

        return result

    except Exception as error:

        record(
            "execution_failures"
        )

        raise HTTPException(
            status_code=400,
            detail=str(error),
        )

    finally:

        record_latency(start)


# ------------------------------------------------------------
# TEST API
# ------------------------------------------------------------

@app.post("/api/test/run")
async def run_tests(
    request: TestRequest,
):

    start = time.time()

    record(
        "test_requests"
    )

    try:

        command = (
            request.command
            or default_test_command(
                request.language
            )
        )

        result = await asyncio.to_thread(
            execute_files,
            request.files,
            command,
            request.language,
            request.timeout_seconds,
        )

        if result["status"] == "success":

            record(
                "test_successes"
            )

        else:

            record(
                "test_failures"
            )

        result["test_command"] = command

        result["latency_seconds"] = round(
            time.time() - start,
            3,
        )

        return result

    except Exception as error:

        record(
            "test_failures"
        )

        raise HTTPException(
            status_code=400,
            detail=str(error),
        )

    finally:

        record_latency(start)


# ------------------------------------------------------------
# EXECUTION CONTEXT FOR AI
# ------------------------------------------------------------

def build_execution_context(
    files: List[ExecutionFile],
) -> str:

    sections = []

    for item in files:

        sections.append(
            f"""
===== {item.path} =====

{clamp_text(
    item.content,
    MAX_FILE_SIZE,
)}
"""
        )

    return clamp_text(
        "\n".join(sections),
        MAX_PROJECT_SIZE,
    )
# ============================================================
# PART 4/5
# AUTOMATIC DIAGNOSE → FIX → RETEST
# + BENCHMARKING
# ============================================================


# ------------------------------------------------------------
# AUTO-FIX MODELS
# ------------------------------------------------------------

class AutoFixRequest(BaseModel):
    files: List[ExecutionFile]

    command: Optional[str] = None

    language: str = "python"

    max_cycles: int = Field(
        default=MAX_TEST_FIX_CYCLES,
        ge=1,
        le=5,
    )

    timeout_seconds: int = Field(
        default=EXECUTION_TIMEOUT_SECONDS,
        ge=1,
        le=60,
    )

    task: Optional[str] = None


# ------------------------------------------------------------
# BENCHMARK MODELS
# ------------------------------------------------------------

class BenchmarkCase(BaseModel):
    name: str

    files: List[ExecutionFile]

    command: str

    language: str = "python"

    expected_output: Optional[str] = None

    expected_contains: List[str] = []


class BenchmarkRequest(BaseModel):
    cases: List[BenchmarkCase]

    stop_on_failure: bool = False


# ------------------------------------------------------------
# JSON RESPONSE PARSER
# ------------------------------------------------------------

def parse_json_response(
    text: str,
) -> Dict[str, Any]:

    if not text:
        raise ValueError(
            "AI returned an empty response."
        )

    cleaned = text.strip()

    # Remove markdown fences if the model
    # accidentally returns them.
    if cleaned.startswith("```"):

        lines = cleaned.splitlines()

        if lines:
            lines = lines[1:]

        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]

        cleaned = "\n".join(lines).strip()

    try:

        result = json.loads(
            cleaned
        )

    except json.JSONDecodeError:

        # Try to recover the first JSON object.
        start = cleaned.find("{")
        end = cleaned.rfind("}")

        if start == -1 or end == -1:
            raise ValueError(
                "AI response did not contain valid JSON."
            )

        try:

            result = json.loads(
                cleaned[start:end + 1]
            )

        except json.JSONDecodeError as error:

            raise ValueError(
                "Unable to parse AI JSON response."
            ) from error

    if not isinstance(result, dict):

        raise ValueError(
            "AI response must be a JSON object."
        )

    return result


# ------------------------------------------------------------
# VALIDATE AI FILE CHANGES
# ------------------------------------------------------------

def validate_changed_files(
    changes: Any,
) -> List[ExecutionFile]:

    if not isinstance(
        changes,
        list,
    ):

        raise ValueError(
            "AI changes must be a list."
        )

    if len(changes) > MAX_PROJECT_FILES:

        raise ValueError(
            "AI returned too many files."
        )

    validated = []

    total_size = 0

    for change in changes:

        if not isinstance(
            change,
            dict,
        ):

            raise ValueError(
                "Invalid AI file change."
            )

        path = safe_relative_path(
            str(
                change.get(
                    "path",
                    "",
                )
            )
        )

        content = change.get(
            "content"
        )

        if not isinstance(
            content,
            str,
        ):

            raise ValueError(
                f"Invalid content for {path}."
            )

        if len(content) > MAX_FILE_SIZE:

            raise ValueError(
                f"AI-generated file too large: {path}"
            )

        total_size += len(
            content.encode(
                "utf-8",
                errors="ignore",
            )
        )

        validated.append(
            ExecutionFile(
                path=path,
                content=content,
            )
        )

    if total_size > MAX_PROJECT_SIZE:

        raise ValueError(
            "AI-generated project is too large."
        )

    return validated


# ------------------------------------------------------------
# APPLY AI CHANGES
# ------------------------------------------------------------

def apply_file_changes(
    current_files: List[ExecutionFile],
    changed_files: List[ExecutionFile],
) -> List[ExecutionFile]:

    file_map = {
        safe_relative_path(
            item.path
        ): item.content
        for item in current_files
    }

    for item in changed_files:

        path = safe_relative_path(
            item.path
        )

        file_map[path] = item.content

    result = []

    for path, content in file_map.items():

        result.append(
            ExecutionFile(
                path=path,
                content=content,
            )
        )

    if len(result) > MAX_PROJECT_FILES:

        raise ValueError(
            "Resulting project has too many files."
        )

    total_size = sum(
        len(
            item.content.encode(
                "utf-8",
                errors="ignore",
            )
        )
        for item in result
    )

    if total_size > MAX_PROJECT_SIZE:

        raise ValueError(
            "Resulting project is too large."
        )

    return sorted(
        result,
        key=lambda item: item.path,
    )


# ------------------------------------------------------------
# AI AUTO-FIX GENERATOR
# ------------------------------------------------------------

async def generate_auto_fix(
    files: List[ExecutionFile],
    task: str,
    execution_result: Dict[str, Any],
    language: str,
    model: Optional[str] = None,
) -> Dict[str, Any]:

    context = build_execution_context(
        files
    )

    stdout = clamp_execution_output(
        execution_result.get(
            "stdout",
            "",
        )
    )

    stderr = clamp_execution_output(
        execution_result.get(
            "stderr",
            "",
        )
    )

    prompt = f"""
You are Engineer AI's automatic debugging and
code repair engine.

USER TASK:
{task}

LANGUAGE:
{language}

CURRENT PROJECT:
{context}

EXECUTION STATUS:
{execution_result.get(
    "status",
    "unknown",
)}

EXIT CODE:
{execution_result.get(
    "exit_code"
)}

STDOUT:
{stdout}

STDERR:
{stderr}

Diagnose the failure and produce the smallest
safe correction that should make the project
pass its test or execution command.

Return ONLY valid JSON with this exact structure:

{{
  "diagnosis": "root cause",
  "summary": "short explanation",
  "changes": [
    {{
      "path": "relative/path",
      "content": "complete new file content"
    }}
  ],
  "tests": [
    "verification step"
  ]
}}

Rules:

- Only use relative file paths.
- Never use absolute paths.
- Never use ../
- Do not add secrets.
- Do not add destructive commands.
- Do not modify unrelated files.
- Preserve working functionality.
- Return complete file contents for changed files.
- If no safe fix is possible, return an empty changes list.
"""

    raw = await infer(
        prompt,
        model=model,
        json_mode=True,
    )

    return parse_json_response(
        raw
    )


# ------------------------------------------------------------
# SINGLE AUTO-FIX CYCLE
# ------------------------------------------------------------

async def run_one_fix_cycle(
    files: List[ExecutionFile],
    task: str,
    command: str,
    language: str,
    timeout_seconds: int,
) -> Dict[str, Any]:

    execution = await asyncio.to_thread(
        execute_files,
        files,
        command,
        language,
        timeout_seconds,
    )

    if execution["status"] == "success":

        return {
            "status": "success",
            "execution": execution,
            "files": files,
            "diagnosis": None,
            "changes": [],
        }

    diagnosis = await generate_auto_fix(
        files=files,
        task=task,
        execution_result=execution,
        language=language,
    )

    raw_changes = diagnosis.get(
        "changes",
        [],
    )

    changed_files = (
        validate_changed_files(
            raw_changes
        )
    )

    if not changed_files:

        return {
            "status": "failed",
            "execution": execution,
            "files": files,
            "diagnosis": diagnosis,
            "changes": [],
            "message": (
                "AI could not produce "
                "a safe correction."
            ),
        }

    updated_files = apply_file_changes(
        files,
        changed_files,
    )

    return {
        "status": "fixed",
        "execution": execution,
        "files": updated_files,
        "diagnosis": diagnosis,
        "changes": [
            item.model_dump()
            for item in changed_files
        ],
    }


# ------------------------------------------------------------
# COMPLETE AUTO-FIX LOOP
# ------------------------------------------------------------

async def run_auto_fix_loop(
    request: AutoFixRequest,
) -> Dict[str, Any]:

    start = time.time()

    record(
        "auto_fix_requests"
    )

    current_files = (
        validate_execution_files(
            request.files
        )
    )

    language = (
        request.language
        or "python"
    )

    command = (
        request.command
        or default_test_command(
            language
        )
    )

    task = (
        request.task
        or "Make the project pass its tests."
    )

    history = []

    for cycle in range(
        1,
        request.max_cycles + 1,
    ):

        result = await run_one_fix_cycle(
            files=current_files,
            task=task,
            command=command,
            language=language,
            timeout_seconds=(
                request.timeout_seconds
            ),
        )

        history.append({
            "cycle": cycle,
            "status": result["status"],
            "execution": result.get(
                "execution"
            ),
            "diagnosis": result.get(
                "diagnosis"
            ),
            "changes": result.get(
                "changes",
                [],
            ),
        })

        if result["status"] == "success":

            record(
                "auto_fix_successes"
            )

            return {
                "status": "success",
                "message": (
                    "Project passed execution/tests."
                ),
                "cycles": cycle,
                "files": [
                    item.model_dump()
                    for item in current_files
                ],
                "history": history,
                "execution": result[
                    "execution"
                ],
                "fixed": cycle > 1,
                "latency_seconds": round(
                    time.time() - start,
                    3,
                ),
            }

        if result["status"] == "failed":

            record(
                "auto_fix_failures"
            )

            return {
                "status": "failed",
                "message": result.get(
                    "message",
                    "Automatic repair failed.",
                ),
                "cycles": cycle,
                "files": [
                    item.model_dump()
                    for item in current_files
                ],
                "history": history,
                "execution": result.get(
                    "execution"
                ),
                "fixed": False,
                "latency_seconds": round(
                    time.time() - start,
                    3,
                ),
            }

        current_files = result[
            "files"
        ]

    # --------------------------------------------------------
    # FINAL VERIFICATION
    # --------------------------------------------------------

    final_result = await asyncio.to_thread(
        execute_files,
        current_files,
        command,
        language,
        request.timeout_seconds,
    )

    if final_result["status"] == "success":

        record(
            "auto_fix_successes"
        )

        status = "success"

    else:

        record(
            "auto_fix_failures"
        )

        status = "failed"

    return {
        "status": status,
        "message": (
            "Maximum auto-fix cycles reached."
        ),
        "cycles": request.max_cycles,
        "files": [
            item.model_dump()
            for item in current_files
        ],
        "history": history,
        "execution": final_result,
        "fixed": (
            status == "success"
        ),
        "latency_seconds": round(
            time.time() - start,
            3,
        ),
    }


# ------------------------------------------------------------
# AUTO-FIX API
# ------------------------------------------------------------

@app.post("/api/agent/autofix")
async def agent_autofix(
    request: AutoFixRequest,
):

    try:

        return await run_auto_fix_loop(
            request
        )

    except Exception as error:

        record(
            "auto_fix_failures"
        )

        raise HTTPException(
            status_code=400,
            detail=str(error),
        )


@app.post("/api/execute/autofix")
async def execute_autofix(
    request: AutoFixRequest,
):

    try:

        return await run_auto_fix_loop(
            request
        )

    except Exception as error:

        record(
            "auto_fix_failures"
        )

        raise HTTPException(
            status_code=400,
            detail=str(error),
        )


# ------------------------------------------------------------
# BENCHMARK HELPERS
# ------------------------------------------------------------

def evaluate_benchmark_result(
    result: Dict[str, Any],
    case: BenchmarkCase,
) -> Dict[str, Any]:

    passed = (
        result.get("status")
        == "success"
    )

    reasons = []

    if not passed:

        reasons.append(
            "Execution failed."
        )

    stdout = result.get(
        "stdout",
        "",
    )

    if case.expected_output is not None:

        if stdout.strip() != (
            case.expected_output.strip()
        ):

            passed = False

            reasons.append(
                "Output did not exactly match "
                "expected output."
            )

    for expected in (
        case.expected_contains
    ):

        if expected not in stdout:

            passed = False

            reasons.append(
                f"Missing expected output: "
                f"{expected}"
            )

    return {
        "passed": passed,
        "reasons": reasons,
    }


# ------------------------------------------------------------
# BENCHMARK RUNNER
# ------------------------------------------------------------

async def run_benchmarks(
    request: BenchmarkRequest,
) -> Dict[str, Any]:

    start = time.time()

    record(
        "benchmark_requests"
    )

    if not request.cases:

        raise ValueError(
            "At least one benchmark case is required."
        )

    if len(request.cases) > 50:

        raise ValueError(
            "Maximum 50 benchmark cases."
        )

    results = []

    passed_count = 0

    for index, case in enumerate(
        request.cases,
        start=1,
    ):

        try:

            validated_files = (
                validate_execution_files(
                    case.files
                )
            )

            result = await asyncio.to_thread(
                execute_files,
                validated_files,
                case.command,
                case.language,
                EXECUTION_TIMEOUT_SECONDS,
            )

            evaluation = (
                evaluate_benchmark_result(
                    result,
                    case,
                )
            )

            if evaluation["passed"]:

                passed_count += 1

            item = {
                "index": index,
                "name": case.name,
                "execution": result,
                "evaluation": evaluation,
            }

        except Exception as error:

            item = {
                "index": index,
                "name": case.name,
                "execution": {
                    "status": "error",
                    "error": str(error),
                },
                "evaluation": {
                    "passed": False,
                    "reasons": [
                        str(error)
                    ],
                },
            }

        results.append(item)

        if (
            request.stop_on_failure
            and not item[
                "evaluation"
            ]["passed"]
        ):

            break

    total = len(results)

    pass_rate = (
        passed_count / total
        if total
        else 0.0
    )

    if passed_count == total and total > 0:

        record(
            "benchmark_successes"
        )

    return {
        "status": "success",
        "total_cases": total,
        "passed_cases": passed_count,
        "failed_cases": (
            total - passed_count
        ),
        "pass_rate": round(
            pass_rate,
            4,
        ),
        "results": results,
        "latency_seconds": round(
            time.time() - start,
            3,
        ),
    }


# ------------------------------------------------------------
# BENCHMARK API
# ------------------------------------------------------------

@app.post("/api/benchmark/run")
async def benchmark_run(
    request: BenchmarkRequest,
):

    try:

        return await run_benchmarks(
            request
        )

    except Exception as error:

        raise HTTPException(
            status_code=400,
            detail=str(error),
        )
# ============================================================
# PART 5/5
# FINAL API WIRING + ANALYTICS + STARTUP
# ============================================================


# ------------------------------------------------------------
# PROJECT MASTER AGENT
# ------------------------------------------------------------

@app.post("/api/project/run")
async def project_run(
    request: ProjectAgentRequest,
):
    """
    Complete project engineering workflow.

    Runs:
    Planner
    Coder
    Debugger
    Tester
    Security
    Performance
    Final synthesis
    """

    start = time.time()

    record("agent_requests")

    try:

        result = await run_project_workflow(
            request
        )

        result["latency_seconds"] = round(
            time.time() - start,
            3,
        )

        return result

    except Exception as error:

        record("failed_requests")

        raise HTTPException(
            status_code=400,
            detail=str(error),
        )

    finally:

        record_latency(start)


# ------------------------------------------------------------
# PROJECT MEMORY
# ------------------------------------------------------------

project_memory: Dict[str, Dict[str, Any]] = {}


class ProjectMemoryRequest(BaseModel):
    project_id: str = Field(
        min_length=1,
        max_length=200,
    )

    project_name: Optional[str] = None

    files: List[ProjectFile] = []

    notes: Optional[str] = Field(
        default=None,
        max_length=MAX_PROMPT_LENGTH,
    )


@app.post("/api/project/memory/save")
async def save_project_memory(
    request: ProjectMemoryRequest,
):

    try:

        validated = validate_project_files(
            request.files
        ) if request.files else []

        statistics = (
            basic_project_statistics(
                validated
            )
            if validated
            else {}
        )

        project_memory[
            request.project_id
        ] = {
            "project_id":
                request.project_id,

            "project_name":
                request.project_name
                or request.project_id,

            "files": [
                item.model_dump()
                for item in validated
            ],

            "notes":
                request.notes or "",

            "statistics":
                statistics,

            "updated_at":
                time.time(),
        }

        return {
            "status": "success",
            "project_id":
                request.project_id,
            "message":
                "Project memory saved.",
        }

    except Exception as error:

        raise HTTPException(
            status_code=400,
            detail=str(error),
        )


@app.get("/api/project/memory/{project_id}")
async def get_project_memory(
    project_id: str,
):

    memory = project_memory.get(
        project_id
    )

    if memory is None:

        raise HTTPException(
            status_code=404,
            detail="Project memory not found.",
        )

    return {
        "status": "success",
        "project": memory,
    }


@app.delete("/api/project/memory/{project_id}")
async def delete_project_memory(
    project_id: str,
):

    if project_id not in project_memory:

        raise HTTPException(
            status_code=404,
            detail="Project memory not found.",
        )

    del project_memory[
        project_id
    ]

    return {
        "status": "success",
        "project_id": project_id,
        "message":
            "Project memory deleted.",
    }


# ------------------------------------------------------------
# MEMORY-AWARE PROJECT ANALYSIS
# ------------------------------------------------------------

class ProjectQuestionRequest(BaseModel):
    project_id: str = Field(
        min_length=1,
        max_length=200,
    )

    question: str = Field(
        min_length=1,
        max_length=MAX_PROMPT_LENGTH,
    )


@app.post("/api/project/memory/ask")
async def ask_project_memory(
    request: ProjectQuestionRequest,
):

    memory = project_memory.get(
        request.project_id
    )

    if memory is None:

        raise HTTPException(
            status_code=404,
            detail="Project memory not found.",
        )

    files = memory.get(
        "files",
        [],
    )

    context = build_project_context(
        [
            ProjectFile(**item)
            for item in files
        ]
    )

    prompt = f"""
You are Engineer AI working with a previously
saved software project.

PROJECT ID:
{request.project_id}

PROJECT NAME:
{memory.get("project_name", "")}

PROJECT NOTES:
{memory.get("notes", "")}

PROJECT:
{context}

USER QUESTION:
{request.question}

Answer using the project context.

Do not invent files, APIs, dependencies,
or functionality that is not supported
by the project.
"""

    start = time.time()

    try:

        output = await infer(
            prompt
        )

        return {
            "status": "success",
            "project_id":
                request.project_id,
            "answer": output,
            "latency_seconds": round(
                time.time() - start,
                3,
            ),
        }

    except Exception as error:

        raise HTTPException(
            status_code=500,
            detail=str(error),
        )


# ------------------------------------------------------------
# SECURITY STATIC CHECKS
# ------------------------------------------------------------

def basic_security_scan(
    files: List[ProjectFile],
) -> List[Dict[str, Any]]:

    findings = []

    patterns = [
        (
            "hardcoded-secret",
            [
                "api_key =",
                "apikey =",
                "password =",
                "secret =",
                "token =",
            ],
            "Possible hardcoded credential.",
        ),

        (
            "shell-execution",
            [
                "os.system(",
                "shell=True",
            ],
            "Potential shell execution.",
        ),

        (
            "unsafe-eval",
            [
                "eval(",
                "exec(",
            ],
            "Dynamic code execution detected.",
        ),

        (
            "debug-mode",
            [
                "debug=True",
            ],
            "Debug mode may be unsafe in production.",
        ),
    ]

    for item in files:

        content_lower = (
            item.content.lower()
        )

        for rule_name, tokens, message in patterns:

            for token in tokens:

                if token.lower() in content_lower:

                    findings.append({
                        "severity":
                            (
                                "high"
                                if rule_name
                                in {
                                    "hardcoded-secret",
                                    "shell-execution",
                                    "unsafe-eval",
                                }
                                else "medium"
                            ),

                        "rule":
                            rule_name,

                        "file":
                            item.path,

                        "message":
                            message,
                    })

                    break

    return findings


class SecurityScanRequest(BaseModel):
    files: List[ProjectFile]


@app.post("/api/security/scan")
async def security_scan(
    request: SecurityScanRequest,
):

    try:

        files = validate_project_files(
            request.files
        )

        findings = basic_security_scan(
            files
        )

        return {
            "status": "success",
            "findings": findings,
            "finding_count": len(
                findings
            ),
        }

    except Exception as error:

        raise HTTPException(
            status_code=400,
            detail=str(error),
        )


# ------------------------------------------------------------
# AI + STATIC SECURITY REVIEW
# ------------------------------------------------------------

@app.post("/api/security/review")
async def security_review(
    request: SecurityScanRequest,
):

    try:

        files = validate_project_files(
            request.files
        )

        static_findings = (
            basic_security_scan(
                files
            )
        )

        context = build_project_context(
            files
        )

        ai_result = (
            await run_specialized_agent(
                "security",
                (
                    "Review this project for "
                    "security vulnerabilities and "
                    "recommend concrete fixes."
                ),
                context,
            )
        )

        return {
            "status": "success",
            "static_findings":
                static_findings,
            "ai_review":
                ai_result,
        }

    except Exception as error:

        raise HTTPException(
            status_code=400,
            detail=str(error),
        )


# ------------------------------------------------------------
# PERFORMANCE METRICS
# ------------------------------------------------------------

@app.get("/api/ops/analytics")
async def operations_analytics():

    requests = analytics_data.get(
        "requests",
        0,
    )

    successful = analytics_data.get(
        "successful_requests",
        0,
    )

    failed = analytics_data.get(
        "failed_requests",
        0,
    )

    total_latency = (
        analytics_data.get(
            "total_latency_seconds",
            0.0,
        )
    )

    average_latency = (
        total_latency / requests
        if requests
        else 0.0
    )

    success_rate = (
        successful / requests
        if requests
        else 0.0
    )

    return {
        **analytics_data,

        "average_latency_seconds":
            round(
                average_latency,
                3,
            ),

        "success_rate":
            round(
                success_rate,
                4,
            ),

        "auto_fix_rate":
            round(
                (
                    analytics_data[
                        "auto_fix_successes"
                    ]
                    /
                    analytics_data[
                        "auto_fix_requests"
                    ]
                )
                if analytics_data[
                    "auto_fix_requests"
                ]
                else 0.0,
                4,
            ),

        "test_pass_rate":
            round(
                (
                    analytics_data[
                        "test_successes"
                    ]
                    /
                    analytics_data[
                        "test_requests"
                    ]
                )
                if analytics_data[
                    "test_requests"
                ]
                else 0.0,
                4,
            ),
    }


# ------------------------------------------------------------
# SYSTEM STATUS
# ------------------------------------------------------------

@app.get("/api/status")
async def system_status():

    docker_available = (
        shutil.which("docker")
        is not None
    )

    return {
        "service": APP_TITLE,
        "version": APP_VERSION,
        "status": "online",

        "ai": {
            "provider":
                DEFAULT_PROVIDER,

            "model":
                DEFAULT_MODEL,

            "configured":
                bool(
                    GEMINI_API_KEY
                ),
        },

        "execution": {
            "mode":
                EXECUTION_MODE,

            "docker_available":
                docker_available,

            "sandbox_enabled":
                EXECUTION_MODE.lower()
                == "docker",
        },

        "limits": {
            "max_project_files":
                MAX_PROJECT_FILES,

            "max_file_size":
                MAX_FILE_SIZE,

            "max_project_size":
                MAX_PROJECT_SIZE,

            "execution_timeout":
                EXECUTION_TIMEOUT_SECONDS,

            "max_fix_cycles":
                MAX_TEST_FIX_CYCLES,
        },

        "memory": {
            "projects":
                len(project_memory),
        },
    }


# ------------------------------------------------------------
# ROOT ROUTE
# ------------------------------------------------------------

@app.get("/")
async def root():

    # If frontend exists, serve it.
    possible_files = [
        "index.html",
        "frontend/index.html",
        "static/index.html",
    ]

    for filename in possible_files:

        path = Path(filename)

        if path.exists():

            return FileResponse(
                path
            )

    return {
        "name": APP_TITLE,
        "version": APP_VERSION,
        "status": "online",

        "message": (
            "Engineer AI backend is running."
        ),

        "documentation":
            "/docs",
    }


# ------------------------------------------------------------
# STARTUP
# ------------------------------------------------------------

@app.on_event("startup")
async def startup_event():

    logger.info(
        "======================================"
    )

    logger.info(
        "%s v%s starting",
        APP_TITLE,
        APP_VERSION,
    )

    logger.info(
        "AI provider: %s",
        DEFAULT_PROVIDER,
    )

    logger.info(
        "AI model: %s",
        DEFAULT_MODEL,
    )

    logger.info(
        "Execution mode: %s",
        EXECUTION_MODE,
    )

    logger.info(
        "Docker available: %s",
        shutil.which("docker") is not None,
    )

    logger.info(
        "======================================"
    )


# ------------------------------------------------------------
# SHUTDOWN
# ------------------------------------------------------------

@app.on_event("shutdown")
async def shutdown_event():

    logger.info(
        "Engineer AI shutting down."
    )


# ------------------------------------------------------------
# LOCAL DEVELOPMENT SERVER
# ------------------------------------------------------------

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