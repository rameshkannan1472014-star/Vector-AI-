# ============================================================
# ENGINEER AI ENGINE
# Backend Version 2.4.0
# ============================================================

import os
import time
import asyncio
import logging

from typing import (
    Dict,
    Any,
    Optional,
    List,
)

from fastapi import (
    FastAPI,
    HTTPException,
)

from fastapi.responses import FileResponse

from fastapi.middleware.cors import CORSMiddleware

from pydantic import (
    BaseModel,
    Field,
)

from google import genai
from google.genai import types


# ============================================================
# CONFIGURATION
# ============================================================

APP_TITLE = os.getenv(
    "APP_TITLE",
    "Engineer AI Engine"
)

APP_VERSION = "2.4.0"

DEFAULT_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-2.5-flash"
)

DEFAULT_PROVIDER = os.getenv(
    "AI_PROVIDER",
    "gemini"
)

MAX_RETRIES = int(
    os.getenv(
        "GEMINI_MAX_RETRIES",
        "2"
    )
)

RETRY_BASE_SECONDS = float(
    os.getenv(
        "GEMINI_RETRY_BASE_SECONDS",
        "0.8"
    )
)

CHAT_MAX_OUTPUT_TOKENS = int(
    os.getenv(
        "CHAT_MAX_OUTPUT_TOKENS",
        "1536"
    )
)

AGENT_MAX_OUTPUT_TOKENS = int(
    os.getenv(
        "AGENT_MAX_OUTPUT_TOKENS",
        "1400"
    )
)

REVIEW_MAX_OUTPUT_TOKENS = int(
    os.getenv(
        "REVIEW_MAX_OUTPUT_TOKENS",
        "1200"
    )
)

MAX_PROMPT_CHARS = int(
    os.getenv(
        "MAX_PROMPT_CHARS",
        "24000"
    )
)

MAX_CODE_CHARS = int(
    os.getenv(
        "MAX_CODE_CHARS",
        "100000"
    )
)

MAX_PROJECT_FILES = int(
    os.getenv(
        "MAX_PROJECT_FILES",
        "100"
    )
)

MAX_FILE_SIZE = int(
    os.getenv(
        "MAX_FILE_SIZE",
        "300000"
    )
)

MAX_PROJECT_CONTEXT_CHARS = int(
    os.getenv(
        "MAX_PROJECT_CONTEXT_CHARS",
        "120000"
    )
)


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=os.getenv(
        "LOG_LEVEL",
        "INFO"
    ).upper(),
    format=(
        "%(asctime)s | "
        "%(levelname)s | "
        "%(message)s"
    ),
)

logger = logging.getLogger(
    "EngineerAI"
)


# ============================================================
# ANALYTICS
# ============================================================

analytics_data = {

    "total_requests": 0,

    "successful_responses": 0,

    "failed_requests": 0,

    "total_latency_seconds": 0.0,

    "agent_tasks_executed": 0,

    "project_analysis_runs": 0,

    "architecture_runs": 0,

    "dependency_analysis_runs": 0,

    "debugger_runs": 0,

    "tests_generated": 0,

    "security_scans_completed": 0,

    "performance_analyses": 0,

    "benchmark_runs": 0,

    "benchmark_cases": 0,

    "benchmark_bugs_found": 0,

    "benchmark_bugs_missed": 0,
}


# ============================================================
# FASTAPI APPLICATION
# ============================================================

app = FastAPI(
    title=APP_TITLE,
    version=APP_VERSION,
)


# ============================================================
# CORS
# ============================================================

def get_origins():

    raw = os.getenv(
        "CORS_ORIGINS",
        "*"
    ).strip()

    if raw == "*":
        return ["*"]

    return [
        item.strip()
        for item in raw.split(",")
        if item.strip()
    ]


origins = get_origins()


app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=(
        False
        if "*" in origins
        else True
    ),
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# REQUEST MODELS
# ============================================================

class ChatRequest(BaseModel):

    prompt: str = Field(
        ...,
        min_length=1,
        max_length=MAX_PROMPT_CHARS,
    )

    provider: Optional[str] = Field(
        default=DEFAULT_PROVIDER,
        max_length=50,
    )


class MultiAgentRequest(BaseModel):

    task_description: Optional[str] = Field(
        default=None,
        max_length=MAX_PROMPT_CHARS,
    )

    task: Optional[str] = Field(
        default=None,
        max_length=MAX_PROMPT_CHARS,
    )

    include_tests: bool = True

    include_security_scan: bool = True

    include_debugger: bool = True

    project_context: Optional[str] = Field(
        default=None,
        max_length=MAX_PROMPT_CHARS,
    )

    def get_task(self):

        value = (
            self.task_description
            or self.task
        )

        if not value or not value.strip():

            raise ValueError(
                "task or task_description is required"
            )

        return value.strip()


class CodeReviewRequest(BaseModel):

    code_snippet: str = Field(
        ...,
        min_length=1,
        max_length=MAX_CODE_CHARS,
    )

    language: str = Field(
        default="text",
        min_length=1,
        max_length=50,
    )


class ProjectFile(BaseModel):

    path: str = Field(
        ...,
        min_length=1,
        max_length=500,
    )

    content: str = Field(
        ...,
        max_length=MAX_FILE_SIZE,
    )


class ProjectRequest(BaseModel):

    project_name: Optional[str] = Field(
        default="Engineer AI Project",
        max_length=200,
    )

    files: List[ProjectFile] = Field(
        ...,
        min_length=1,
        max_length=MAX_PROJECT_FILES,
    )


class ProjectAgentRequest(ProjectRequest):

    task: str = Field(
        ...,
        min_length=1,
        max_length=MAX_PROMPT_CHARS,
    )

    include_tests: bool = True

    include_security: bool = True

    include_performance: bool = True


# ============================================================
# BENCHMARK MODELS
# ============================================================

class BenchmarkCase(BaseModel):

    name: str = Field(
        ...,
        min_length=1,
        max_length=200,
    )

    language: str = Field(
        default="python",
        min_length=1,
        max_length=50,
    )

    broken_code: str = Field(
        ...,
        min_length=1,
        max_length=MAX_CODE_CHARS,
    )

    expected_issues: List[str] = Field(
        default_factory=list
    )


class BenchmarkRequest(BaseModel):

    cases: List[BenchmarkCase] = Field(
        ...,
        min_length=1,
        max_length=20,
    )

    run_tests: bool = True
    # ============================================================
# TEXT UTILITIES
# ============================================================

def clamp_text(
    value: Optional[str],
    limit: int
) -> str:

    value = value or ""

    if len(value) <= limit:
        return value

    return (
        value[:limit]
        + "\n\n"
        "[Input truncated by Engineer AI backend.]"
    )


def record(
    start: float,
    success: bool
) -> None:

    analytics_data[
        "total_requests"
    ] += 1

    analytics_data[
        "total_latency_seconds"
    ] += (
        time.perf_counter()
        - start
    )

    if success:

        analytics_data[
            "successful_responses"
        ] += 1

    else:

        analytics_data[
            "failed_requests"
        ] += 1


def is_retryable_error(
    exc: Exception
) -> bool:

    message = str(exc).upper()

    retryable_terms = (
        "503",
        "UNAVAILABLE",
        "429",
        "RESOURCE_EXHAUSTED",
        "DEADLINE",
        "TIMEOUT",
        "INTERNAL",
        "SERVICE_UNAVAILABLE",
    )

    return any(
        term in message
        for term in retryable_terms
    )


# ============================================================
# ENGINEER AI SYSTEM INSTRUCTION
# ============================================================

SYSTEM_INSTRUCTION = (
    "You are Engineer AI, an advanced engineering "
    "and software engineering assistant.\n\n"

    "Your purpose is to help users understand, "
    "design, calculate, troubleshoot, analyze, "
    "architect, test, and implement engineering "
    "and software systems.\n\n"

    "CORE RULES:\n"
    "1. Answer the user's exact request.\n"
    "2. Be technically accurate.\n"
    "3. Explain concepts clearly and logically.\n"
    "4. Never invent facts, specifications, "
    "measurements, test results, or sources.\n"
    "5. Clearly distinguish assumptions from "
    "confirmed information.\n"
    "6. For calculations, show formulas, known "
    "values, calculations, results, and units.\n"
    "7. Check calculations before giving results.\n"
    "8. Explain important trade-offs when multiple "
    "solutions exist.\n"
    "9. Never claim code was executed unless the "
    "backend actually executed it.\n"
    "10. Never claim tests passed unless tests "
    "were actually executed.\n"
    "11. Never claim a live security scan occurred "
    "unless one actually occurred.\n\n"

    "SOFTWARE ENGINEERING:\n"
    "Provide complete runnable code when practical.\n"
    "Consider architecture, maintainability, "
    "error handling, security, testing, and "
    "performance.\n\n"

    "ENGINEERING SAFETY:\n"
    "When relevant, consider electrical, thermal, "
    "mechanical, reliability, and operational "
    "constraints.\n\n"

    "DIAGRAMS:\n"
    "When generating Mermaid diagrams, use a fenced "
    "```mermaid block.\n"
    "Use simple node IDs without spaces or special "
    "characters.\n"
    "Put human-readable labels inside quotes.\n"
    "Prefer graph TD or graph LR.\n"
)


# ============================================================
# GEMINI CLIENT
# ============================================================

def get_client():

    api_key = os.getenv(
        "GEMINI_API_KEY"
    )

    if not api_key:

        raise RuntimeError(
            "GEMINI_API_KEY is not configured."
        )

    return genai.Client(
        api_key=api_key
    )


def generate_sync(
    prompt: str,
    max_output_tokens: int
) -> str:

    client = get_client()

    response = client.models.generate_content(

        model=DEFAULT_MODEL,

        contents=prompt,

        config=types.GenerateContentConfig(

            system_instruction=(
                SYSTEM_INSTRUCTION
            ),

            temperature=0.2,

            max_output_tokens=(
                max_output_tokens
            ),
        ),
    )

    text = getattr(
        response,
        "text",
        None
    )

    if not text:

        raise RuntimeError(
            "Gemini returned an empty response."
        )

    return text.strip()


async def infer(
    prompt: str,
    max_output_tokens: int
) -> str:

    prompt = clamp_text(
        prompt,
        MAX_PROMPT_CHARS
    )

    for attempt in range(
        MAX_RETRIES + 1
    ):

        try:

            return await asyncio.to_thread(
                generate_sync,
                prompt,
                max_output_tokens
            )

        except Exception as exc:

            if (
                not is_retryable_error(exc)
                or attempt >= MAX_RETRIES
            ):

                raise

            delay = (
                RETRY_BASE_SECONDS
                * (attempt + 1)
            )

            logger.warning(
                "Temporary Gemini error. "
                "Retrying in %.1fs: %s",
                delay,
                exc,
            )

            await asyncio.sleep(
                delay
            )

    raise RuntimeError(
        "Gemini inference failed."
    )


# ============================================================
# AGENT RUNNER
# ============================================================

async def run_agent(
    name: str,
    prompt: str,
    max_tokens: int = AGENT_MAX_OUTPUT_TOKENS
) -> Dict[str, Any]:

    start = time.perf_counter()

    try:

        output = await infer(
            prompt,
            max_tokens
        )

        return {
            "agent": name,
            "status": "success",
            "output": output,
            "latency_seconds": round(
                time.perf_counter()
                - start,
                3
            ),
        }

    except Exception as exc:

        logger.exception(
            "%s agent failed",
            name
        )

        return {
            "agent": name,
            "status": "error",
            "output": "",
            "message": str(exc),
            "latency_seconds": round(
                time.perf_counter()
                - start,
                3
            ),
        }


# ============================================================
# ROOT
# ============================================================

@app.get("/")
async def root():

    if os.path.exists(
        "index.html"
    ):

        return FileResponse(
            "index.html"
        )

    return {
        "name": "Engineer AI",
        "version": APP_VERSION,
        "status": "online",
    }


# ============================================================
# HEALTH
# ============================================================

@app.get("/api/health")
async def health():

    return {
        "status": "healthy",
        "service": APP_TITLE,
        "version": APP_VERSION,
        "model": DEFAULT_MODEL,
        "provider": DEFAULT_PROVIDER,
        "api_key_configured": bool(
            os.getenv(
                "GEMINI_API_KEY"
            )
        ),
    }


# ============================================================
# CHAT
# ============================================================

@app.post("/api/chat")
async def chat(
    request: ChatRequest
):

    start = time.perf_counter()

    try:

        provider = (
            request.provider
            or DEFAULT_PROVIDER
        ).lower()

        if provider != "gemini":

            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unsupported provider: "
                    f"{provider}"
                ),
            )

        response = await infer(
            request.prompt,
            CHAT_MAX_OUTPUT_TOKENS
        )

        record(
            start,
            True
        )

        return {
            "status": "success",
            "response": response,
            "provider": "gemini",
            "model": DEFAULT_MODEL,
            "latency_seconds": round(
                time.perf_counter()
                - start,
                3
            ),
        }

    except HTTPException:

        record(
            start,
            False
        )

        raise

    except Exception as exc:

        record(
            start,
            False
        )

        logger.exception(
            "Chat failed"
        )

        raise HTTPException(
            status_code=502,
            detail={
                "error": (
                    "AI engine request failed"
                ),
                "message": str(exc),
            },
        )
         # ============================================================
# MULTI-AGENT ENGINEERING WORKFLOW
# ============================================================

@app.post("/api/agent/execute")
async def multi_agent_endpoint(
    request: MultiAgentRequest
):

    start = time.perf_counter()

    analytics_data[
        "agent_tasks_executed"
    ] += 1

    try:

        task = request.get_task()

        project_context = ""

        if request.project_context:

            project_context = (
                "\n\nPROJECT CONTEXT:\n"
                + clamp_text(
                    request.project_context,
                    MAX_PROMPT_CHARS
                )
            )

        # ----------------------------------------------------
        # PLANNER
        # ----------------------------------------------------

        planner = await run_agent(

            "Planner",

            (
                "You are the Planner Agent.\n\n"

                "Deconstruct the engineering task "
                "into a clear implementation plan.\n\n"

                "Include:\n"
                "- requirements\n"
                "- assumptions\n"
                "- architecture\n"
                "- dependencies\n"
                "- implementation steps\n"
                "- risks\n"
                "- validation strategy\n\n"

                f"TASK:\n{task}"

                f"{project_context}"
            )
        )

        # ----------------------------------------------------
        # EXECUTOR
        # ----------------------------------------------------

        executor = await run_agent(

            "Executor",

            (
                "You are the Executor Agent.\n\n"

                "Act as the implementation engineer.\n\n"

                f"TASK:\n{task}\n\n"

                "PLANNER OUTPUT:\n"
                f"{planner['output']}\n\n"

                "Provide the implementation, "
                "code, architecture, technical "
                "design, or engineering solution "
                "required by the task.\n\n"

                "Do not claim that code was executed."
                f"{project_context}"
            )
        )

        # ----------------------------------------------------
        # TESTER
        # ----------------------------------------------------

        if request.include_tests:

            analytics_data[
                "tests_generated"
            ] += 1

            tester_job = run_agent(

                "Tester",

                (
                    "You are the Tester Agent.\n"

                    "Create a rigorous testing strategy "
                    "for the proposed implementation.\n\n"

                    "Include:\n"
                    "- unit tests\n"
                    "- integration tests\n"
                    "- edge cases\n"
                    "- invalid inputs\n"
                    "- regression cases\n"
                    "- expected results\n\n"

                    f"TASK:\n{task}\n\n"

                    "IMPLEMENTATION:\n"
                    f"{executor['output']}"
                )
            )

        else:

            tester_job = asyncio.sleep(

                0,

                result={
                    "agent": "Tester",
                    "status": "skipped",
                    "output": "",
                    "latency_seconds": 0.0,
                }
            )

        # ----------------------------------------------------
        # SECURITY
        # ----------------------------------------------------

        if request.include_security_scan:

            analytics_data[
                "security_scans_completed"
            ] += 1

            security_job = run_agent(

                "Security",

                (
                    "You are the Security Agent.\n"

                    "Perform a static security review "
                    "of the proposed implementation.\n\n"

                    f"TASK:\n{task}\n\n"

                    "IMPLEMENTATION:\n"
                    f"{executor['output']}\n\n"

                    "Look for:\n"
                    "- injection risks\n"
                    "- authentication problems\n"
                    "- authorization problems\n"
                    "- secret exposure\n"
                    "- unsafe input handling\n"
                    "- resource abuse\n"
                    "- insecure configuration\n"
                    "- data leakage\n\n"

                    "Do not claim a live security scan."
                )
            )

        else:

            security_job = asyncio.sleep(

                0,

                result={
                    "agent": "Security",
                    "status": "skipped",
                    "output": "",
                    "latency_seconds": 0.0,
                }
            )

        # Run tester and security concurrently

        tester, security = await asyncio.gather(

            tester_job,
            security_job
        )

        # ----------------------------------------------------
        # DEBUGGER
        # ----------------------------------------------------

        debugger = {

            "agent": "Debugger",

            "status": "skipped",

            "output": "",

            "latency_seconds": 0.0,
        }

        if request.include_debugger:

            analytics_data[
                "debugger_runs"
            ] += 1

            debugger = await run_agent(

                "Debugger",

                (
                    "You are the Debugger Agent.\n\n"

                    "Review the Planner, Executor, "
                    "Tester, and Security outputs.\n\n"

                    f"TASK:\n{task}\n\n"

                    "PLANNER:\n"
                    f"{planner['output']}\n\n"

                    "EXECUTOR:\n"
                    f"{executor['output']}\n\n"

                    "TESTER:\n"
                    f"{tester['output']}\n\n"

                    "SECURITY:\n"
                    f"{security['output']}\n\n"

                    "Find contradictions, bugs, "
                    "missing requirements, test gaps, "
                    "security problems, and incorrect "
                    "assumptions.\n\n"

                    "Give concrete fixes."
                )
            )

        workflow = {

            "task": task,

            "planner": planner,

            "executor": executor,

            "tester": tester,

            "security": security,

            "debugger": debugger,
        }

        record(
            start,
            True
        )

        return {

            "status": "success",

            "workflow": workflow,

            "agent_outputs": {

                "plan": planner,

                "implementation": executor,

                "tests": tester,

                "security_scan": security,

                "debugger": debugger,
            },

            "latency_seconds": round(
                time.perf_counter()
                - start,
                3
            ),
        }

    except HTTPException:

        record(
            start,
            False
        )

        raise

    except Exception as exc:

        record(
            start,
            False
        )

        logger.exception(
            "Multi-agent workflow failed"
        )

        raise HTTPException(
            status_code=502,
            detail={
                "error": (
                    "Multi-agent workflow failed"
                ),
                "message": str(exc),
            },
        )


# ============================================================
# CODE REVIEW
# ============================================================

@app.post("/api/code/review")
async def code_review(
    request: CodeReviewRequest
):

    start = time.perf_counter()

    try:

        language = (
            request.language.strip()
        )

        code = clamp_text(
            request.code_snippet,
            MAX_CODE_CHARS
        )

        prompt = (

            "You are Engineer AI Code Review Agent.\n\n"

            f"LANGUAGE:\n{language}\n\n"

            "Review this code carefully.\n\n"

            f"CODE:\n"
            f"```{language}\n"
            f"{code}\n"
            "```\n\n"

            "Analyze:\n"
            "1. Syntax errors\n"
            "2. Logic bugs\n"
            "3. Runtime errors\n"
            "4. Security problems\n"
            "5. Performance problems\n"
            "6. Maintainability issues\n"
            "7. Edge cases\n\n"

            "For every important problem provide:\n"
            "- problem\n"
            "- root cause\n"
            "- corrected code\n"
            "- explanation\n\n"

            "Do not claim that the code was executed."
        )

        output = await infer(
            prompt,
            REVIEW_MAX_OUTPUT_TOKENS
        )

        record(
            start,
            True
        )

        return {

            "status": "success",

            "review": {

                "agent": "Code Reviewer",

                "status": "success",

                "language": language,

                "output": output,

                "executed": False,
            },

            "output": output,

            "response": output,

            "executed": False,

            "latency_seconds": round(
                time.perf_counter()
                - start,
                3
            ),
        }

    except Exception as exc:

        record(
            start,
            False
        )

        logger.exception(
            "Code review failed"
        )

        raise HTTPException(
            status_code=502,
            detail={
                "error": "Code review failed",
                "message": str(exc),
            },
        )


# ============================================================
# PROJECT FILE VALIDATION
# ============================================================

ALLOWED_PROJECT_SUFFIXES = {

    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".html",
    ".css",
    ".json",
    ".yaml",
    ".yml",
    ".md",
    ".txt",
    ".java",
    ".cpp",
    ".c",
    ".h",
    ".hpp",
    ".go",
    ".rs",
    ".php",
    ".rb",
    ".swift",
    ".kt",
    ".sql",
    ".sh",
    ".xml",
    ".vue",
}


def validate_project_files(
    files: List[ProjectFile]
) -> List[ProjectFile]:

    if not files:

        raise HTTPException(
            status_code=400,
            detail="No project files supplied."
        )

    validated = []

    total_chars = 0

    for file in files:

        path = file.path.strip()

        if not path:

            raise HTTPException(
                status_code=400,
                detail="Project file path is empty."
            )

        if ".." in path:

            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unsafe project path: {path}"
                ),
            )

        extension = os.path.splitext(
            path.lower()
        )[1]

        if (
            extension
            and extension not in ALLOWED_PROJECT_SUFFIXES
        ):

            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unsupported project file type: "
                    f"{path}"
                ),
            )

        if len(file.content) > MAX_FILE_SIZE:

            raise HTTPException(
                status_code=413,
                detail=(
                    f"Project file too large: "
                    f"{path}"
                ),
            )

        total_chars += len(
            file.content
        )

        if total_chars > MAX_PROJECT_CONTEXT_CHARS:

            raise HTTPException(
                status_code=413,
                detail=(
                    "Combined project context is too large."
                ),
            )

        validated.append(
            ProjectFile(
                path=path,
                content=file.content
            )
        )

    return validated


# ============================================================
# BUILD PROJECT CONTEXT
# ============================================================

def build_project_context(
    files: List[ProjectFile]
) -> str:

    sections = []

    total = 0

    for file in files:

        section = (
            "\n"
            "================================================\n"
            f"FILE: {file.path}\n"
            "================================================\n"
            f"{file.content}\n"
        )

        remaining = (
            MAX_PROJECT_CONTEXT_CHARS
            - total
        )

        if remaining <= 0:
            break

        section = section[:remaining]

        sections.append(
            section
        )

        total += len(section)

    return "".join(
        sections
    )


# ============================================================
# PROJECT PROMPT BUILDER
# ============================================================

def make_project_prompt(
    agent_name: str,
    instruction: str,
    context: str
) -> str:

    return (

        f"You are the {agent_name}.\n\n"

        "PROJECT CONTEXT:\n"

        f"{context}\n\n"

        "YOUR TASK:\n"

        f"{instruction}\n\n"

        "IMPORTANT:\n"

        "Analyze the supplied project context only. "

        "Do not claim that code was executed unless "
        "the backend actually executed it."
    )
     # ============================================================
# PROJECT ANALYSIS
# ============================================================

@app.post("/api/project/analyze")
async def project_analyze(
    request: ProjectRequest
):

    start = time.perf_counter()

    try:

        files = validate_project_files(
            request.files
        )

        context = build_project_context(
            files
        )

        analytics_data[
            "project_analysis_runs"
        ] += 1

        prompt = make_project_prompt(

            "Project Analysis Agent",

            (
                "Analyze the complete project.\n\n"

                "Identify:\n"
                "- project purpose\n"
                "- major components\n"
                "- files and responsibilities\n"
                "- architecture\n"
                "- important data flows\n"
                "- APIs\n"
                "- configuration\n"
                "- dependencies\n"
                "- risks\n"
                "- technical debt\n"
                "- improvement opportunities"
            ),

            context
        )

        output = await infer(
            prompt,
            AGENT_MAX_OUTPUT_TOKENS
        )

        record(
            start,
            True
        )

        return {

            "status": "success",

            "project": request.project_name,

            "files_analyzed": len(files),

            "analysis": output,

            "latency_seconds": round(
                time.perf_counter()
                - start,
                3
            ),
        }

    except HTTPException:

        record(
            start,
            False
        )

        raise

    except Exception as exc:

        record(
            start,
            False
        )

        logger.exception(
            "Project analysis failed"
        )

        raise HTTPException(
            status_code=502,
            detail={
                "error": (
                    "Project analysis failed"
                ),
                "message": str(exc),
            },
        )


# ============================================================
# PROJECT ARCHITECTURE
# ============================================================

@app.post("/api/project/architecture")
async def project_architecture(
    request: ProjectRequest
):

    start = time.perf_counter()

    try:

        files = validate_project_files(
            request.files
        )

        context = build_project_context(
            files
        )

        analytics_data[
            "architecture_runs"
        ] += 1

        prompt = make_project_prompt(

            "Architecture Agent",

            (
                "Analyze the project's architecture.\n\n"

                "Describe:\n"
                "- major components\n"
                "- component relationships\n"
                "- data flow\n"
                "- APIs\n"
                "- storage\n"
                "- external services\n"
                "- deployment structure\n"
                "- architectural risks\n"
                "- recommended improvements\n\n"

                "When useful, provide a Mermaid diagram."
            ),

            context
        )

        output = await infer(
            prompt,
            AGENT_MAX_OUTPUT_TOKENS
        )

        record(
            start,
            True
        )

        return {

            "status": "success",

            "project": request.project_name,

            "architecture": output,

            "latency_seconds": round(
                time.perf_counter()
                - start,
                3
            ),
        }

    except HTTPException:

        record(
            start,
            False
        )

        raise

    except Exception as exc:

        record(
            start,
            False
        )

        logger.exception(
            "Architecture analysis failed"
        )

        raise HTTPException(
            status_code=502,
            detail={
                "error": (
                    "Architecture analysis failed"
                ),
                "message": str(exc),
            },
        )


# ============================================================
# PROJECT DEPENDENCIES
# ============================================================

@app.post("/api/project/dependencies")
async def project_dependencies(
    request: ProjectRequest
):

    start = time.perf_counter()

    try:

        files = validate_project_files(
            request.files
        )

        context = build_project_context(
            files
        )

        analytics_data[
            "dependency_analysis_runs"
        ] += 1

        prompt = make_project_prompt(

            "Dependency Analysis Agent",

            (
                "Analyze project dependencies.\n\n"

                "Identify:\n"
                "- direct dependencies\n"
                "- framework dependencies\n"
                "- runtime dependencies\n"
                "- development dependencies\n"
                "- external services\n"
                "- version constraints when visible\n"
                "- dependency risks\n"
                "- potentially unnecessary dependencies\n"
                "- upgrade recommendations\n"
                "- compatibility concerns\n\n"

                "Do not invent versions that are not "
                "visible in the project."
            ),

            context
        )

        output = await infer(
            prompt,
            AGENT_MAX_OUTPUT_TOKENS
        )

        record(
            start,
            True
        )

        return {

            "status": "success",

            "project": request.project_name,

            "dependencies": output,

            "latency_seconds": round(
                time.perf_counter()
                - start,
                3
            ),
        }

    except HTTPException:

        record(
            start,
            False
        )

        raise

    except Exception as exc:

        record(
            start,
            False
        )

        logger.exception(
            "Dependency analysis failed"
        )

        raise HTTPException(
            status_code=502,
            detail={
                "error": (
                    "Dependency analysis failed"
                ),
                "message": str(exc),
            },
        )


# ============================================================
# PROJECT DEBUGGING
# ============================================================

@app.post("/api/project/debug")
async def project_debug(
    request: ProjectRequest
):

    start = time.perf_counter()

    try:

        files = validate_project_files(
            request.files
        )

        context = build_project_context(
            files
        )

        analytics_data[
            "debugger_runs"
        ] += 1

        prompt = make_project_prompt(

            "Project Debugger",

            (
                "Debug this project statically.\n\n"

                "Find:\n"
                "- syntax problems\n"
                "- logic bugs\n"
                "- broken assumptions\n"
                "- incorrect data flow\n"
                "- error handling problems\n"
                "- configuration problems\n"
                "- integration issues\n"
                "- likely runtime failures\n\n"

                "For every important issue explain:\n"
                "- root cause\n"
                "- affected file\n"
                "- concrete fix\n"
                "- possible regression risk"
            ),

            context
        )

        output = await infer(
            prompt,
            AGENT_MAX_OUTPUT_TOKENS
        )

        record(
            start,
            True
        )

        return {

            "status": "success",

            "project": request.project_name,

            "debug": output,

            "executed": False,

            "latency_seconds": round(
                time.perf_counter()
                - start,
                3
            ),
        }

    except HTTPException:

        record(
            start,
            False
        )

        raise

    except Exception as exc:

        record(
            start,
            False
        )

        logger.exception(
            "Project debugging failed"
        )

        raise HTTPException(
            status_code=502,
            detail={
                "error": (
                    "Project debugging failed"
                ),
                "message": str(exc),
            },
        )


# ============================================================
# PROJECT TEST GENERATION
# ============================================================

@app.post("/api/project/tests")
async def project_tests(
    request: ProjectRequest
):

    start = time.perf_counter()

    try:

        files = validate_project_files(
            request.files
        )

        context = build_project_context(
            files
        )

        analytics_data[
            "tests_generated"
        ] += 1

        prompt = make_project_prompt(

            "Project Test Agent",

            (
                "Generate a comprehensive project "
                "testing strategy.\n\n"

                "Create appropriate:\n"
                "- unit tests\n"
                "- integration tests\n"
                "- edge-case tests\n"
                "- invalid-input tests\n"
                "- regression tests\n"
                "- important assertions\n\n"

                "Identify which files each test should target.\n"

                "Provide example test code when practical.\n"

                "Do not claim that tests were executed."
            ),

            context
        )

        output = await infer(
            prompt,
            AGENT_MAX_OUTPUT_TOKENS
        )

        record(
            start,
            True
        )

        return {

            "status": "success",

            "project": request.project_name,

            "tests": output,

            "executed": False,

            "latency_seconds": round(
                time.perf_counter()
                - start,
                3
            ),
        }

    except HTTPException:

        record(
            start,
            False
        )

        raise

    except Exception as exc:

        record(
            start,
            False
        )

        logger.exception(
            "Project test generation failed"
        )

        raise HTTPException(
            status_code=502,
            detail={
                "error": (
                    "Project test generation failed"
                ),
                "message": str(exc),
            },
        )


# ============================================================
# PROJECT SECURITY
# ============================================================

@app.post("/api/project/security")
async def project_security(
    request: ProjectRequest
):

    start = time.perf_counter()

    try:

        files = validate_project_files(
            request.files
        )

        context = build_project_context(
            files
        )

        analytics_data[
            "security_scans_completed"
        ] += 1

        prompt = make_project_prompt(

            "Project Security Agent",

            (
                "Perform a static security review "
                "of the entire project.\n\n"

                "Look for:\n"
                "- authentication weaknesses\n"
                "- authorization issues\n"
                "- injection risks\n"
                "- secrets exposure\n"
                "- unsafe file operations\n"
                "- insecure configuration\n"
                "- dependency risks\n"
                "- data leakage\n"
                "- resource abuse\n"
                "- input validation problems\n"
                "- logging problems\n"
                "- other relevant vulnerabilities\n\n"

                "For each important issue provide:\n"
                "- severity\n"
                "- root cause\n"
                "- affected file\n"
                "- mitigation\n\n"

                "Do not claim that a live security scan occurred."
            ),

            context
        )

        output = await infer(
            prompt,
            AGENT_MAX_OUTPUT_TOKENS
        )

        record(
            start,
            True
        )

        return {

            "status": "success",

            "project": request.project_name,

            "security": output,

            "executed": False,

            "latency_seconds": round(
                time.perf_counter()
                - start,
                3
            ),
        }

    except HTTPException:

        record(
            start,
            False
        )

        raise

    except Exception as exc:

        record(
            start,
            False
        )

        logger.exception(
            "Project security analysis failed"
        )

        raise HTTPException(
            status_code=502,
            detail={
                "error": (
                    "Project security analysis failed"
                ),
                "message": str(exc),
            },
        )
      # ============================================================
# PROJECT PERFORMANCE
# ============================================================

@app.post("/api/project/performance")
async def project_performance(
    request: ProjectRequest
):

    start = time.perf_counter()

    try:

        files = validate_project_files(
            request.files
        )

        context = build_project_context(
            files
        )

        analytics_data[
            "performance_analyses"
        ] += 1

        prompt = make_project_prompt(

            "Project Performance Agent",

            (
                "Perform a static performance analysis "
                "of the entire project.\n\n"

                "Look for:\n"
                "- slow algorithms\n"
                "- unnecessary repeated work\n"
                "- inefficient loops\n"
                "- excessive memory usage\n"
                "- blocking operations\n"
                "- unnecessary network requests\n"
                "- database inefficiencies\n"
                "- caching opportunities\n"
                "- frontend performance issues\n"
                "- scalability bottlenecks\n\n"

                "For each important issue provide:\n"
                "- affected file\n"
                "- likely impact\n"
                "- root cause\n"
                "- recommended optimization\n\n"

                "Do not claim that performance was "
                "benchmarked or measured."
            ),

            context
        )

        output = await infer(
            prompt,
            AGENT_MAX_OUTPUT_TOKENS
        )

        record(
            start,
            True
        )

        return {

            "status": "success",

            "project": request.project_name,

            "performance": output,

            "executed": False,

            "latency_seconds": round(
                time.perf_counter()
                - start,
                3
            ),
        }

    except HTTPException:

        record(
            start,
            False
        )

        raise

    except Exception as exc:

        record(
            start,
            False
        )

        logger.exception(
            "Project performance analysis failed"
        )

        raise HTTPException(
            status_code=502,
            detail={
                "error": (
                    "Project performance analysis failed"
                ),
                "message": str(exc),
            },
        )


# ============================================================
# FULL PROJECT AGENT
# ============================================================

@app.post("/api/project/agent")
async def project_agent(
    request: ProjectAgentRequest
):

    start = time.perf_counter()

    try:

        files = validate_project_files(
            request.files
        )

        context = build_project_context(
            files
        )

        analytics_data[
            "agent_tasks_executed"
        ] += 1

        # ----------------------------------------------------
        # PLANNER
        # ----------------------------------------------------

        planner = await run_agent(

            "Project Planner",

            make_project_prompt(

                "Project Planner",

                (
                    f"USER TASK:\n"
                    f"{request.task}\n\n"

                    "Create a detailed implementation plan.\n\n"

                    "Include:\n"
                    "- requirements\n"
                    "- affected files\n"
                    "- implementation steps\n"
                    "- dependencies\n"
                    "- risks\n"
                    "- validation strategy\n"
                    "- rollback considerations\n\n"

                    "Do not write the entire implementation yet."
                ),

                context
            )
        )

        # ----------------------------------------------------
        # EXECUTOR
        # ----------------------------------------------------

        executor = await run_agent(

            "Project Executor",

            make_project_prompt(

                "Project Executor",

                (
                    f"USER TASK:\n"
                    f"{request.task}\n\n"

                    "PLANNER OUTPUT:\n"
                    f"{planner['output']}\n\n"

                    "Design the implementation required "
                    "to complete the task.\n\n"

                    "Provide:\n"
                    "- files that should change\n"
                    "- exact implementation approach\n"
                    "- important code changes\n"
                    "- configuration changes\n"
                    "- integration considerations\n\n"

                    "Do not claim that files were modified "
                    "unless the backend actually modified them."
                ),

                context
            )
        )

        # ----------------------------------------------------
        # TESTER
        # ----------------------------------------------------

        if request.include_tests:

            analytics_data[
                "tests_generated"
            ] += 1

            tester_job = run_agent(

                "Project Tester",

                make_project_prompt(

                    "Project Tester",

                    (
                        f"USER TASK:\n"
                        f"{request.task}\n\n"

                        "PROPOSED IMPLEMENTATION:\n"
                        f"{executor['output']}\n\n"

                        "Create a comprehensive test strategy.\n\n"

                        "Include:\n"
                        "- unit tests\n"
                        "- integration tests\n"
                        "- edge cases\n"
                        "- invalid inputs\n"
                        "- regression cases\n"
                        "- expected results\n"
                        "- important assertions"
                    ),

                    context
                )
            )

        else:

            tester_job = asyncio.sleep(

                0,

                result={
                    "agent": "Project Tester",
                    "status": "skipped",
                    "output": "",
                    "latency_seconds": 0.0,
                }
            )

        # ----------------------------------------------------
        # SECURITY
        # ----------------------------------------------------

        if request.include_security:

            analytics_data[
                "security_scans_completed"
            ] += 1

            security_job = run_agent(

                "Project Security Agent",

                make_project_prompt(

                    "Project Security Agent",

                    (
                        f"USER TASK:\n"
                        f"{request.task}\n\n"

                        "PROPOSED IMPLEMENTATION:\n"
                        f"{executor['output']}\n\n"

                        "Perform a static security review.\n\n"

                        "Look for:\n"
                        "- injection risks\n"
                        "- authentication weaknesses\n"
                        "- authorization problems\n"
                        "- secret exposure\n"
                        "- unsafe input handling\n"
                        "- insecure configuration\n"
                        "- data leakage\n"
                        "- resource abuse\n\n"

                        "Do not claim a live security scan."
                    ),

                    context
                )
            )

        else:

            security_job = asyncio.sleep(

                0,

                result={
                    "agent": "Project Security Agent",
                    "status": "skipped",
                    "output": "",
                    "latency_seconds": 0.0,
                }
            )

        # ----------------------------------------------------
        # PERFORMANCE
        # ----------------------------------------------------

        if request.include_performance:

            analytics_data[
                "performance_analyses"
            ] += 1

            performance_job = run_agent(

                "Project Performance Agent",

                make_project_prompt(

                    "Project Performance Agent",

                    (
                        f"USER TASK:\n"
                        f"{request.task}\n\n"

                        "PROPOSED IMPLEMENTATION:\n"
                        f"{executor['output']}\n\n"

                        "Perform a static performance review.\n\n"

                        "Look for:\n"
                        "- inefficient algorithms\n"
                        "- repeated work\n"
                        "- memory issues\n"
                        "- blocking operations\n"
                        "- unnecessary network calls\n"
                        "- scalability problems\n"
                        "- caching opportunities"
                    ),

                    context
                )
            )

        else:

            performance_job = asyncio.sleep(

                0,

                result={
                    "agent": "Project Performance Agent",
                    "status": "skipped",
                    "output": "",
                    "latency_seconds": 0.0,
                }
            )

        # Run analysis agents concurrently

        tester, security, performance = (
            await asyncio.gather(

                tester_job,
                security_job,
                performance_job
            )
        )

        # ----------------------------------------------------
        # DEBUGGER
        # ----------------------------------------------------

        analytics_data[
            "debugger_runs"
        ] += 1

        debugger = await run_agent(

            "Project Debugger",

            make_project_prompt(

                "Project Debugger",

                (
                    f"USER TASK:\n"
                    f"{request.task}\n\n"

                    "PLANNER:\n"
                    f"{planner['output']}\n\n"

                    "EXECUTOR:\n"
                    f"{executor['output']}\n\n"

                    "TESTER:\n"
                    f"{tester['output']}\n\n"

                    "SECURITY:\n"
                    f"{security['output']}\n\n"

                    "PERFORMANCE:\n"
                    f"{performance['output']}\n\n"

                    "Review the complete proposed workflow.\n\n"

                    "Identify:\n"
                    "- contradictions\n"
                    "- implementation bugs\n"
                    "- missing requirements\n"
                    "- test gaps\n"
                    "- security problems\n"
                    "- performance problems\n"
                    "- incorrect assumptions\n\n"

                    "Give concrete fixes and a final "
                    "implementation checklist."
                ),

                context
            )
        )

        workflow = {

            "task": request.task,

            "project": request.project_name,

            "planner": planner,

            "executor": executor,

            "tester": tester,

            "security": security,

            "performance": performance,

            "debugger": debugger,
        }

        record(
            start,
            True
        )

        return {

            "status": "success",

            "project": request.project_name,

            "workflow": workflow,

            "agent_outputs": {

                "plan": planner,

                "implementation": executor,

                "tests": tester,

                "security_scan": security,

                "performance": performance,

                "debugger": debugger,
            },

            "execution": {

                "status": "not_executed",

                "message": (
                    "The project was analyzed statically. "
                    "No project code was executed."
                ),
            },

            "latency_seconds": round(
                time.perf_counter()
                - start,
                3
            ),
        }

    except HTTPException:

        record(
            start,
            False
        )

        raise

    except Exception as exc:

        record(
            start,
            False
        )

        logger.exception(
            "Full project agent failed"
        )

        raise HTTPException(
            status_code=502,
            detail={
                "error": (
                    "Full project agent failed"
                ),
                "message": str(exc),
            },
        )


# ============================================================
# BENCHMARK HELPERS
# ============================================================

def normalize_benchmark_output(
    text: str
) -> str:

    return (
        text or ""
    ).strip().lower()


def evaluate_benchmark_case(
    case: BenchmarkCase,
    output: str
) -> Dict[str, Any]:

    normalized = normalize_benchmark_output(
        output
    )

    found = []

    missed = []

    for issue in case.expected_issues:

        issue_normalized = (
            issue or ""
        ).strip().lower()

        if not issue_normalized:
            continue

        if issue_normalized in normalized:

            found.append(
                issue
            )

        else:

            missed.append(
                issue
            )

    total = len(
        case.expected_issues
    )

    if total > 0:

        score = round(

            (
                len(found)
                / total
            ) * 100,

            2
        )

    else:

        score = (
            100.0
            if output.strip()
            else 0.0
        )

    return {

        "name": case.name,

        "expected_issues":
            case.expected_issues,

        "issues_found":
            found,

        "issues_missed":
            missed,

        "score_percent":
            score,
    }


# ============================================================
# BENCHMARK RUNNER
# ============================================================

@app.post("/api/benchmark/run")
async def benchmark_run(
    request: BenchmarkRequest
):

    start = time.perf_counter()

    try:

        results = []

        total_found = 0

        total_missed = 0

        for case in request.cases:

            case_start = (
                time.perf_counter()
            )

            analytics_data[
                "benchmark_runs"
            ] += 1

            prompt = (

                "You are the Engineer AI "
                "Benchmark Agent.\n\n"

                "Analyze the following intentionally "
                "broken code.\n\n"

                f"LANGUAGE:\n"
                f"{case.language}\n\n"

                "CODE:\n"

                f"```{case.language}\n"
                f"{case.broken_code}\n"
                "```\n\n"

                "Identify bugs and explain their "
                "root causes.\n\n"

                "Provide corrected code and tests "
                "where useful.\n\n"

                "Also discuss security and "
                "reliability concerns.\n\n"

                "Do not execute the supplied code."
            )

            output = await infer(

                prompt,

                REVIEW_MAX_OUTPUT_TOKENS
            )

            evaluation = (
                evaluate_benchmark_case(
                    case,
                    output
                )
            )

            found_count = len(
                evaluation[
                    "issues_found"
                ]
            )

            missed_count = len(
                evaluation[
                    "issues_missed"
                ]
            )

            total_found += (
                found_count
            )

            total_missed += (
                missed_count
            )

            analytics_data[
                "benchmark_cases"
            ] += 1

            analytics_data[
                "benchmark_bugs_found"
            ] += found_count

            analytics_data[
                "benchmark_bugs_missed"
            ] += missed_count

            results.append({

                "name": case.name,

                "language": case.language,

                "output": output,

                "evaluation": evaluation,

                "latency_seconds": round(

                    time.perf_counter()
                    - case_start,

                    3
                ),
            })

        total_expected = (
            total_found
            + total_missed
        )

        overall_score = (

            round(

                (
                    total_found
                    / total_expected
                ) * 100,

                2
            )

            if total_expected > 0

            else 0.0
        )

        record(
            start,
            True
        )

        return {

            "status": "success",

            "results": results,

            "summary": {

                "cases": len(
                    request.cases
                ),

                "bugs_found":
                    total_found,

                "bugs_missed":
                    total_missed,

                "overall_score_percent":
                    overall_score,
            },

            "tests_requested":
                request.run_tests,

            "tests_executed":
                False,

            "latency_seconds": round(

                time.perf_counter()
                - start,

                3
            ),
        }

    except HTTPException:

        record(
            start,
            False
        )

        raise

    except Exception as exc:

        record(
            start,
            False
        )

        logger.exception(
            "Benchmark run failed"
        )

        raise HTTPException(

            status_code=502,

            detail={

                "error":
                    "Benchmark run failed",

                "message":
                    str(exc),
            },
        )


# ============================================================
# OPERATIONS ANALYTICS
# ============================================================

@app.get("/api/ops/analytics")
async def operations_analytics():

    total = analytics_data[
        "total_requests"
    ]

    successful = analytics_data[
        "successful_responses"
    ]

    if total > 0:

        success_rate = round(

            (
                successful
                / total
            ) * 100,

            2
        )

        average_latency = round(

            analytics_data[
                "total_latency_seconds"
            ] / total,

            3
        )

    else:

        success_rate = 100.0

        average_latency = 0.0

    metrics = dict(
        analytics_data
    )

    metrics[
        "success_rate_percent"
    ] = success_rate

    metrics[
        "average_latency_seconds"
    ] = average_latency

    return {

        **metrics,

        "status": "success",

        "system_status":
            "Operational",

        # Compatibility for UIs
        "metrics":
            metrics,

        "total_requests":
            total,

        "successful_responses":
            successful,

        "failed_requests":
            analytics_data[
                "failed_requests"
            ],

        "success_rate_percent":
            success_rate,

        "average_latency_seconds":
                    average_latency,

        "agent_tasks_executed":
            analytics_data[
                "agent_tasks_executed"
            ],

        "project_analysis_runs":
            analytics_data[
                "project_analysis_runs"
            ],

        "architecture_runs":
            analytics_data[
                "architecture_runs"
            ],

        "dependency_analysis_runs":
            analytics_data[
                "dependency_analysis_runs"
            ],

        "debugger_runs":
            analytics_data[
                "debugger_runs"
            ],

        "tests_generated":
            analytics_data[
                "tests_generated"
            ],

        "security_scans_completed":
            analytics_data[
                "security_scans_completed"
            ],

        "performance_analyses":
            analytics_data[
                "performance_analyses"
            ],

        "benchmark_runs":
            analytics_data[
                "benchmark_runs"
            ],

        "benchmark_cases":
            analytics_data[
                "benchmark_cases"
            ],

        "benchmark_bugs_found":
            analytics_data[
                "benchmark_bugs_found"
            ],

        "benchmark_bugs_missed":
            analytics_data[
                "benchmark_bugs_missed"
            ],
    }


# ============================================================
# APPLICATION STARTUP
# ============================================================

@app.on_event("startup")
async def startup_event():

    logger.info(
        "%s v%s starting",
        APP_TITLE,
        APP_VERSION
    )

    logger.info(
        "AI provider: %s",
        DEFAULT_PROVIDER
    )

    logger.info(
        "AI model: %s",
        DEFAULT_MODEL
    )


# ============================================================
# APPLICATION SHUTDOWN
# ============================================================

@app.on_event("shutdown")
async def shutdown_event():

    logger.info(
        "%s shutting down",
        APP_TITLE
    )


# ============================================================
# LOCAL SERVER
# ============================================================

if __name__ == "__main__":

    import uvicorn

    host = os.getenv(
        "HOST",
        "0.0.0.0"
    )

    port = int(
        os.getenv(
            "PORT",
            "8000"
        )
    )

    uvicorn.run(
        app,
        host=host,
        port=port,
)
