import os
import time
import asyncio
import logging
from typing import Dict, Any, Optional, List

from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from google import genai
from google.genai import types


# ============================================================
# ENGINEER AI
# MILESTONE 2 - PROJECT INTELLIGENCE ENGINE
# ============================================================

APP_TITLE = "Engineer AI Engine"
APP_VERSION = "2.2.0"

DEFAULT_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-2.5-flash"
)

DEFAULT_PROVIDER = os.getenv(
    "AI_PROVIDER",
    "gemini"
)

MAX_PROJECT_FILES = int(
    os.getenv("MAX_PROJECT_FILES", "100")
)

MAX_FILE_SIZE = int(
    os.getenv("MAX_FILE_SIZE", str(300 * 1024))
)

MAX_PROJECT_CONTEXT_CHARS = int(
    os.getenv("MAX_PROJECT_CONTEXT_CHARS", "120000")
)

MAX_RETRIES = int(
    os.getenv("GEMINI_MAX_RETRIES", "3")
)


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)

logger = logging.getLogger("EngineerAI")


# ============================================================
# ANALYTICS
# ============================================================

analytics_data: Dict[str, Any] = {
    "total_requests": 0,
    "successful_responses": 0,
    "failed_requests": 0,
    "total_latency_seconds": 0.0,

    "agent_tasks_executed": 0,

    "planner_tasks": 0,
    "executor_tasks": 0,
    "tester_tasks": 0,
    "security_tasks": 0,
    "debugger_tasks": 0,
    "performance_tasks": 0,

    "project_analyses": 0,
    "architecture_analyses": 0,
    "dependency_analyses": 0,
    "debug_sessions": 0,
    "tests_generated": 0,
    "security_scans_completed": 0,
    "performance_analyses": 0,
}


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title=APP_TITLE,
    version=APP_VERSION,
    description=(
        "Engineer AI Project Intelligence and Multi-Agent "
        "Engineering Backend"
    )
)


# ============================================================
# CORS
# ============================================================

cors_origins = os.getenv(
    "CORS_ORIGINS",
    "*"
)

if cors_origins.strip() == "*":
    allowed_origins = ["*"]
    allow_credentials = False
else:
    allowed_origins = [
        origin.strip()
        for origin in cors_origins.split(",")
        if origin.strip()
    ]
    allow_credentials = True


app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=allow_credentials,
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
        max_length=50000
    )

    provider: Optional[str] = DEFAULT_PROVIDER


class MultiAgentRequest(BaseModel):
    """
    Supports BOTH:

        task

    and:

        task_description

    This keeps compatibility with older and newer dashboards.
    """

    task_description: Optional[str] = None

    task: Optional[str] = None

    include_tests: bool = True

    include_security_scan: bool = True

    def get_task(self) -> str:
        value = self.task_description or self.task

        if not value or not value.strip():
            raise ValueError(
                "A task is required. Provide 'task' or "
                "'task_description'."
            )

        return value.strip()


class CodeReviewRequest(BaseModel):
    code_snippet: str = Field(
        ...,
        min_length=1,
        max_length=100000
    )

    language: str = Field(
        ...,
        min_length=1,
        max_length=100
    )


class ProjectFile(BaseModel):
    path: str = Field(
        ...,
        min_length=1,
        max_length=500
    )

    content: str = Field(
        ...,
        max_length=MAX_FILE_SIZE
    )


class ProjectRequest(BaseModel):
    project_name: Optional[str] = "Engineer AI Project"

    files: List[ProjectFile] = Field(
        ...,
        min_length=1,
        max_length=MAX_PROJECT_FILES
    )


class ProjectAgentRequest(ProjectRequest):
    task: str = Field(
        ...,
        min_length=1,
        max_length=50000
    )

    include_tests: bool = True

    include_security: bool = True

    include_performance: bool = True


# ============================================================
# ROOT / UI
# ============================================================

@app.get("/")
async def serve_ui():

    if os.path.exists("index.html"):
        return FileResponse("index.html")

    return {
        "status": "ok",
        "message": "Engineer AI backend is running.",
        "version": APP_VERSION
    }


# ============================================================
# SYSTEM INSTRUCTION
#
# IMPORTANT:
# No triple-quoted Python string is used here.
#
# This prevents the exact deployment error you encountered.
# ============================================================

SYSTEM_INSTRUCTION = (
    "You are Engineer AI, an advanced engineering and software "
    "engineering assistant.\n\n"

    "Your job is to help users understand, design, build, analyze, "
    "debug, test, secure, optimize, and document engineering "
    "software and systems.\n\n"

    "CORE RULES:\n"
    "1. Answer the user's exact request.\n"
    "2. Be technically accurate.\n"
    "3. Never invent facts, measurements, specifications, test "
    "results, benchmark results, or sources.\n"
    "4. Clearly distinguish confirmed facts, assumptions, "
    "recommendations, and inferred conclusions.\n"
    "5. If information is missing, state what is missing.\n"
    "6. For calculations, show formula, known values, calculation, "
    "result, and units.\n"
    "7. Check calculations before giving the final answer.\n"
    "8. Explain important engineering trade-offs.\n"
    "9. Do not claim code was executed unless it actually was.\n"
    "10. Do not claim tests passed unless they actually ran.\n"
    "11. Treat security analysis as defensive engineering.\n"
    "12. Never expose secrets found in project files.\n"
    "13. Prefer modular, maintainable, testable designs.\n"
    "14. Consider correctness, reliability, security, performance, "
    "testing, scalability, and maintainability.\n\n"

    "PROJECT INTELLIGENCE:\n"
    "When project files are supplied, reason about the project as "
    "a whole.\n"
    "Consider file structure, modules, imports, APIs, classes, "
    "functions, configuration, dependencies, data flow, control "
    "flow, architecture, error handling, testing, security, "
    "performance, scalability, and maintainability.\n"
    "Never assume a file exists if it was not provided.\n\n"

    "MERMAID RULES:\n"
    "1. Always output Mermaid inside a fenced mermaid code block.\n"
    "2. Use simple node IDs.\n"
    "3. Do not put spaces or special characters in raw node IDs.\n"
    "4. Put human-readable labels inside double quotes.\n"
    "5. Prefer graph TD or graph LR.\n"
    "6. Keep diagrams syntactically simple.\n\n"

    "Example Mermaid:\n"
    "```mermaid\n"
    "graph TD\n"
    "    A[\"User\"] --> B[\"Engineer AI\"]\n"
    "    B --> C[\"Planner\"]\n"
    "    C --> D[\"Executor\"]\n"
    "    D --> E[\"Tester\"]\n"
    "    D --> F[\"Security\"]\n"
    "    E --> G[\"Debugger\"]\n"
    "    F --> G[\"Debugger\"]\n"
    "```\n"
)


# ============================================================
# TELEMETRY
# ============================================================

def record_telemetry(
    start_time: float,
    success: bool
) -> None:

    latency = time.time() - start_time

    analytics_data["total_requests"] += 1

    analytics_data[
        "total_latency_seconds"
    ] += latency

    if success:
        analytics_data[
            "successful_responses"
        ] += 1
    else:
        analytics_data[
            "failed_requests"
        ] += 1


# ============================================================
# ERROR CLASSIFICATION
# ============================================================

def is_temporary_error(
    error_text: str
) -> bool:

    text = error_text.upper()

    temporary_markers = (
        "503",
        "UNAVAILABLE",
        "429",
        "RESOURCE_EXHAUSTED",
        "TIMEOUT",
        "TIMED OUT",
    )

    return any(
        marker in text
        for marker in temporary_markers
    )


# ============================================================
# GEMINI INFERENCE
# ============================================================

async def execute_model_inference_with_retry(
    prompt: str,
    provider: str = DEFAULT_PROVIDER,
    custom_system_prompt: Optional[str] = None,
    max_retries: int = MAX_RETRIES,
) -> str:

    if provider == "local_cuda":

        return (
            "Local CUDA/TensorRT inference is not configured on "
            "this deployment. Gemini is the active provider."
        )

    if provider != "gemini":

        raise ValueError(
            f"Unknown provider: {provider}"
        )

    api_key = os.getenv(
        "GEMINI_API_KEY"
    )

    if not api_key:

        raise ValueError(
            "GEMINI_API_KEY environment variable is missing."
        )

    client = genai.Client(
        api_key=api_key
    )

    instruction = (
        custom_system_prompt
        if custom_system_prompt
        else SYSTEM_INSTRUCTION
    )

    def call_model() -> str:

        response = client.models.generate_content(
            model=DEFAULT_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=instruction
            ),
        )

        text = getattr(
            response,
            "text",
            None
        )

        if text:

            return text.strip()

        return (
            "Empty response received from model."
        )

    last_error: Optional[Exception] = None

    for attempt in range(
        1,
        max_retries + 1
    ):

        try:

            # Run the synchronous SDK call in a thread.
            # This prevents blocking FastAPI.
            return await asyncio.to_thread(
                call_model
            )

        except Exception as exc:

            last_error = exc

            error_text = str(exc)

            if (
                is_temporary_error(error_text)
                and attempt < max_retries
            ):

                delay = min(
                    2 ** (attempt - 1),
                    6
                )

                logger.warning(
                    "Temporary Gemini error on attempt "
                    "%s/%s: %s. Retrying in %ss.",
                    attempt,
                    max_retries,
                    error_text,
                    delay,
                )

                await asyncio.sleep(
                    delay
                )

                continue

            raise

    raise last_error or RuntimeError(
        "Model inference failed."
    )


# ============================================================
# PROJECT FILE VALIDATION
# ============================================================

def validate_project_files(
    files: List[ProjectFile]
) -> List[ProjectFile]:

    allowed_suffixes = {
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".html",
        ".css",
        ".json",
        ".yaml",
        ".yml",
        ".md",
        ".txt",
        ".java",
        ".kt",
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".cs",
        ".go",
        ".rs",
        ".swift",
        ".sql",
        ".sh",
        ".bat",
        ".ps1",
        ".xml",
        ".toml",
        ".ini",
        ".cfg",
        ".env.example",
    }

    validated: List[ProjectFile] = []

    for item in files:

        normalized = (
            item.path
            .replace("\\", "/")
            .strip()
        )

        if (
            not normalized
            or normalized.startswith("/")
            or normalized.startswith("../")
            or "/../" in normalized
            or normalized == ".."
        ):

            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unsafe project path: "
                    f"{item.path}"
                ),
            )

        lower = normalized.lower()

        extension_allowed = any(
            lower.endswith(suffix)
            for suffix in allowed_suffixes
        )

        if not extension_allowed:

            logger.info(
                "Skipping unsupported project file: %s",
                normalized,
            )

            continue

        validated.append(
            ProjectFile(
                path=normalized,
                content=item.content,
            )
        )

    if not validated:

        raise HTTPException(
            status_code=400,
            detail=(
                "No supported text/code project "
                "files were supplied."
            ),
        )

    return validated


# ============================================================
# PROJECT CONTEXT BUILDER
# ============================================================

def build_project_context(
    files: List[ProjectFile]
) -> str:

    validated = validate_project_files(
        files
    )

    chunks: List[str] = []

    used_chars = 0

    for item in validated:

        block = (
            "\n"
            "================ FILE: "
            + item.path
            + " ================\n"
            + item.content
            + "\n"
        )

        if (
            used_chars
            + len(block)
            > MAX_PROJECT_CONTEXT_CHARS
        ):

            remaining = (
                MAX_PROJECT_CONTEXT_CHARS
                - used_chars
            )

            if remaining > 500:

                chunks.append(
                    block[:remaining]
                    + "\n"
                    "[PROJECT CONTEXT TRUNCATED]\n"
                )

            break

        chunks.append(
            block
        )

        used_chars += len(block)

    return "".join(
        chunks
    )


# ============================================================
# AGENT RUNNER
# ============================================================

async def run_agent(
    agent_name: str,
    prompt: str,
    counter_key: str,
    provider: str = DEFAULT_PROVIDER,
) -> str:

    logger.info(
        "Running %s agent.",
        agent_name
    )

    result = await (
        execute_model_inference_with_retry(
            prompt,
            provider=provider,
        )
    )

    analytics_data[
        counter_key
    ] += 1

    return result


# ============================================================
# HEALTH
# ============================================================

@app.get("/api/health")
async def health():

    return {
        "status": "healthy",
        "service": APP_TITLE,
        "version": APP_VERSION,
        "provider": DEFAULT_PROVIDER,
        "model": DEFAULT_MODEL,
    }


# ============================================================
# CHAT
# ============================================================

@app.post("/api/chat")
async def chat_endpoint(
    request: ChatRequest,
    background_tasks: BackgroundTasks,
):

    start_time = time.time()

    try:

        response_text = (
            await execute_model_inference_with_retry(
                request.prompt,
                provider=(
                    request.provider
                    or DEFAULT_PROVIDER
                ),
            )
        )

        background_tasks.add_task(
            record_telemetry,
            start_time,
            True,
        )

        return {
            "status": "success",
            "response": response_text,
            "provider": (
                request.provider
                or DEFAULT_PROVIDER
            ),
            "model": DEFAULT_MODEL,
        }

    except Exception as exc:

        background_tasks.add_task(
            record_telemetry,
            start_time,
            False,
        )

        logger.exception(
            "Chat inference failed."
        )

        return {
            "status": "error",
            "response": (
                f"Engine Error: {str(exc)}"
            ),
        }


# ============================================================
# STANDARD MULTI-AGENT WORKFLOW
#
# Planner
#    ↓
# Executor
#    ↓
# Tester + Security   <-- concurrent
#    ↓
# Debugger
#
# No Verifier.
# ============================================================

@app.post("/api/agent/execute")
async def multi_agent_endpoint(
    request: MultiAgentRequest,
    background_tasks: BackgroundTasks,
):

    start_time = time.time()

    try:

        task = request.get_task()

        analytics_data[
            "agent_tasks_executed"
        ] += 1

        # ----------------------------------------------------
        # PLANNER
        # ----------------------------------------------------

        planner_prompt = (
            "You are the Planner Agent.\n\n"
            "Break the engineering task into a precise "
            "implementation plan.\n\n"
            "TASK:\n"
            + task
        )

        plan = await run_agent(
            "Planner",
            planner_prompt,
            "planner_tasks",
        )

        # ----------------------------------------------------
        # EXECUTOR
        # ----------------------------------------------------

        executor_prompt = (
            "You are the Executor Agent.\n\n"
            "Create the best practical implementation, "
            "code, architecture, or technical solution "
            "for the task.\n\n"
            "TASK:\n"
            + task
            + "\n\nPLAN:\n"
            + plan
        )

        implementation = await run_agent(
            "Executor",
            executor_prompt,
            "executor_tasks",
        )

        # ----------------------------------------------------
        # TESTER
        # ----------------------------------------------------

        tester_task = None

        if request.include_tests:

            analytics_data[
                "tests_generated"
            ] += 1

            tester_prompt = (
                "You are the Tester Agent.\n\n"
                "Design validation and tests for the "
                "proposed implementation.\n"
                "Do not claim that tests actually ran.\n\n"
                "TASK:\n"
                + task
                + "\n\nIMPLEMENTATION:\n"
                + implementation
            )

            tester_task = run_agent(
                "Tester",
                tester_prompt,
                "tester_tasks",
            )

          # ----------------------------------------------------
        # SECURITY
        # ----------------------------------------------------

        security_task = None

        if request.include_security_scan:

            analytics_data[
                "security_scans_completed"
            ] += 1

            security_prompt = (
                "You are the Security Agent.\n\n"
                "Perform a defensive security review of "
                "the proposed implementation.\n\n"
                "TASK:\n"
                + task
                + "\n\nIMPLEMENTATION:\n"
                + implementation
                + "\n\n"
                "Check:\n"
                "- input validation\n"
                "- authentication and authorization\n"
                "- secrets and credential exposure\n"
                "- unsafe data handling\n"
                "- injection risks\n"
                "- insecure defaults\n"
                "- dependency risks\n"
                "- privacy risks\n"
                "- error handling\n"
                "- resource exhaustion risks\n"
                "- engineering safety concerns\n\n"
                "For every finding, explain severity, reason, "
                "and a practical defensive mitigation.\n"
                "Do not claim that a vulnerability was exploited "
                "or that a security test actually ran."
            )

            security_task = run_agent(
                "Security",
                security_prompt,
                "security_tasks",
            )

        # ----------------------------------------------------
        # RUN TESTER + SECURITY CONCURRENTLY
        # ----------------------------------------------------

        parallel_results = await asyncio.gather(
            tester_task if tester_task is not None
            else asyncio.sleep(0, result=None),

            security_task if security_task is not None
            else asyncio.sleep(0, result=None),

            return_exceptions=True,
        )

        tests_result = parallel_results[0]
        security_result = parallel_results[1]

        if isinstance(tests_result, Exception):
            tests_result = (
                "Tester Agent failed: "
                + str(tests_result)
            )

        if isinstance(security_result, Exception):
            security_result = (
                "Security Agent failed: "
                + str(security_result)
            )

        # ----------------------------------------------------
        # DEBUGGER
        # ----------------------------------------------------

        debugger_prompt = (
            "You are the Debugger Agent.\n\n"
            "Review the task, implementation, testing analysis, "
            "and security analysis.\n\n"
            "Identify likely defects, inconsistencies, missing "
            "requirements, reliability problems, and improvements.\n\n"
            "TASK:\n"
            + task
            + "\n\nIMPLEMENTATION:\n"
            + implementation
            + "\n\nTEST ANALYSIS:\n"
            + str(tests_result)
            + "\n\nSECURITY ANALYSIS:\n"
            + str(security_result)
            + "\n\n"
            "Return:\n"
            "1. Issues found\n"
            "2. Why they matter\n"
            "3. Recommended fixes\n"
            "4. Improved implementation guidance\n"
            "5. Remaining assumptions or risks\n"
        )

        debugger_result = await run_agent(
            "Debugger",
            debugger_prompt,
            "debugger_tasks",
        )

        background_tasks.add_task(
            record_telemetry,
            start_time,
            True,
        )

        return {
            "status": "success",

            # Dashboard-compatible workflow object
            "workflow": {
                "task": task,
                "planner": plan,
                "executor": implementation,
                "tester": tests_result,
                "security": security_result,
                "debugger": debugger_result,
            },

            # Backward compatibility
            "agent_outputs": {
                "plan": plan,
                "implementation": implementation,
                "tests": tests_result,
                "security_scan": security_result,
                "debugger": debugger_result,
            },
        }

    except Exception as exc:

        background_tasks.add_task(
            record_telemetry,
            start_time,
            False,
        )

        logger.exception(
            "Multi-agent workflow failed."
        )

        return {
            "status": "error",
            "message": (
                "Multi-Agent Execution Error: "
                + str(exc)
            ),
            "workflow": None,
        }


# ============================================================
# CODE REVIEW
# ============================================================

@app.post("/api/code/review")
async def code_review_endpoint(
    request: CodeReviewRequest
):

    start_time = time.time()

    prompt = (
        "You are Engineer AI Code Review Agent.\n\n"
        "Review the following source code.\n\n"
        "LANGUAGE:\n"
        + request.language
        + "\n\nCODE:\n"
        + request.code_snippet
        + "\n\n"
        "Analyze:\n"
        "1. Syntax problems\n"
        "2. Logic bugs\n"
        "3. Runtime risks\n"
        "4. Security problems\n"
        "5. Performance problems\n"
        "6. Maintainability\n"
        "7. Code quality\n"
        "8. Engineering concerns where relevant\n\n"
        "For each issue explain:\n"
        "- severity\n"
        "- problem\n"
        "- why it matters\n"
        "- recommended fix\n\n"
        "If useful, provide corrected code.\n"
        "Do not claim that the code was executed."
    )

    try:

        review_result = (
            await execute_model_inference_with_retry(
                prompt
            )
        )

        record_telemetry(
            start_time,
            True
        )

        return {
            "status": "success",
            "review": {
                "output": review_result
            },
            "output": review_result,
            "response": review_result,
        }

    except Exception as exc:

        record_telemetry(
            start_time,
            False
        )

        logger.exception(
            "Code review failed."
        )

        return {
            "status": "error",
            "message": str(exc),
            "review": {
                "output": (
                    "Code review failed: "
                    + str(exc)
                )
            },
        }


# ============================================================
# PROJECT CONTEXT HELPER
# ============================================================

def make_project_prompt(
    title: str,
    project_name: str,
    project_context: str,
    instructions: str,
) -> str:

    return (
        "You are Engineer AI Project Intelligence.\n\n"
        "PROJECT:\n"
        + project_name
        + "\n\n"
        "MODE:\n"
        + title
        + "\n\n"
        "PROJECT FILES:\n"
        + project_context
        + "\n\n"
        "INSTRUCTIONS:\n"
        + instructions
        + "\n\n"
        "IMPORTANT:\n"
        "Only use information present in the supplied project "
        "context plus clearly identified engineering knowledge.\n"
        "Do not invent files, functions, dependencies, test "
        "results, benchmarks, or execution results.\n"
    )


# ============================================================
# PROJECT ANALYSIS
# ============================================================

@app.post("/api/project/analyze")
async def project_analyze_endpoint(
    request: ProjectRequest
):

    start_time = time.time()

    try:

        files = validate_project_files(
            request.files
        )

        context = build_project_context(
            files
        )

        prompt = make_project_prompt(
            "Project Analysis",
            request.project_name or "Engineer AI Project",
            context,
            (
                "Perform a complete project-level analysis.\n\n"
                "Analyze:\n"
                "- project structure\n"
                "- major modules\n"
                "- responsibilities\n"
                "- important functions/classes\n"
                "- APIs\n"
                "- configuration\n"
                "- data flow\n"
                "- control flow\n"
                "- error handling\n"
                "- testing approach\n"
                "- security posture\n"
                "- performance considerations\n"
                "- scalability\n"
                "- maintainability\n\n"
                "Finish with:\n"
                "1. Strengths\n"
                "2. Problems\n"
                "3. Risks\n"
                "4. Recommended improvements\n"
                "5. Priority roadmap"
            ),
        )

        result = await execute_model_inference_with_retry(
            prompt
        )

        analytics_data[
            "project_analyses"
        ] += 1

        record_telemetry(
            start_time,
            True
        )

        return {
            "status": "success",
            "analysis": result,
            "files_analyzed": len(files),
        }

    except Exception as exc:

        record_telemetry(
            start_time,
            False
        )

        logger.exception(
            "Project analysis failed."
        )

        raise HTTPException(
            status_code=500,
            detail=str(exc)
        )


# ============================================================
# PROJECT ARCHITECTURE
# ============================================================

@app.post("/api/project/architecture")
async def project_architecture_endpoint(
    request: ProjectRequest
):

    start_time = time.time()

    try:

        files = validate_project_files(
            request.files
        )

        context = build_project_context(
            files
        )

        prompt = make_project_prompt(
            "Architecture Analysis",
            request.project_name or "Engineer AI Project",
            context,
            (
                "Reverse-engineer the likely software architecture "
                "from the supplied files.\n\n"
                "Describe:\n"
                "- architectural style\n"
                "- components\n"
                "- module boundaries\n"
                "- API boundaries\n"
                "- data flow\n"
                "- dependencies between components\n"
                "- coupling and cohesion\n"
                "- scalability concerns\n"
                "- reliability concerns\n"
                "- architecture weaknesses\n"
                "- recommended architecture improvements\n\n"
                "Also provide a Mermaid architecture diagram."
            ),
        )

        result = await execute_model_inference_with_retry(
            prompt
        )

        analytics_data[
            "architecture_analyses"
        ] += 1

        record_telemetry(
            start_time,
            True
        )

        return {
            "status": "success",
            "architecture": result,
        }

    except Exception as exc:

        record_telemetry(
            start_time,
            False
        )

        logger.exception(
            "Architecture analysis failed."
        )

        raise HTTPException(
            status_code=500,
            detail=str(exc)
        )


# ============================================================
# DEPENDENCY ANALYSIS
# ============================================================

@app.post("/api/project/dependencies")
async def project_dependencies_endpoint(
    request: ProjectRequest
):

    start_time = time.time()

    try:

        files = validate_project_files(
            request.files
        )

        context = build_project_context(
            files
        )

        prompt = make_project_prompt(
            "Dependency Analysis",
            request.project_name or "Engineer AI Project",
            context,
            (
                "Analyze project dependencies.\n\n"
                "Identify:\n"
                "- direct dependencies visible in files\n"
                "- internal module dependencies\n"
                "- external services\n"
                "- frameworks\n"
                "- SDKs\n"
                "- configuration dependencies\n"
                "- likely dependency risks\n"
                "- version-management concerns\n"
                "- unused or suspicious dependencies when evidence exists\n\n"
                "Do not invent package versions."
            ),
        )

        result = await execute_model_inference_with_retry(
            prompt
        )

        analytics_data[
            "dependency_analyses"
        ] += 1

        record_telemetry(
            start_time,
            True
        )

        return {
            "status": "success",
            "dependencies": result,
        }

    except Exception as exc:

        record_telemetry(
            start_time,
            False
        )

        logger.exception(
            "Dependency analysis failed."
        )

        raise HTTPException(
            status_code=500,
            detail=str(exc)
        )


# ============================================================
# PROJECT DEBUGGING
# ============================================================

@app.post("/api/project/debug")
async def project_debug_endpoint(
    request: ProjectRequest
):

    start_time = time.time()

    try:

        files = validate_project_files(
            request.files
        )

        context = build_project_context(
            files
        )

        prompt = make_project_prompt(
            "Project Debugging",
            request.project_name or "Engineer AI Project",
            context,
            (
                "Perform a deep static debugging analysis.\n\n"
                "Look for:\n"
                "- syntax risks\n"
                "- incorrect control flow\n"
                "- logic bugs\n"
                "- null/None problems\n"
                "- error handling problems\n"
                "- race conditions\n"
                "- resource leaks\n"
                "- incorrect API usage\n"
                "- configuration mistakes\n"
                "- inconsistent assumptions\n"
                "- integration risks\n\n"
                "For every finding provide:\n"
                "file/path if known\n"
                "problem\n"
                "reason\n"
                "severity\n"
                "recommended fix\n\n"
                "Do not claim that code was executed."
            ),
        )

        result = await execute_model_inference_with_retry(
            prompt
        )

        analytics_data[
            "debug_sessions"
        ] += 1

        record_telemetry(
            start_time,
            True
        )

        return {
            "status": "success",
            "debug": result,
        }

    except Exception as exc:

        record_telemetry(
            start_time,
            False
        )

        logger.exception(
            "Project debugging failed."
        )

        raise HTTPException(
            status_code=500,
            detail=str(exc)
        )


# ============================================================
# PROJECT TEST GENERATION
# ============================================================

@app.post("/api/project/tests")
async def project_tests_endpoint(
    request: ProjectRequest
):

    start_time = time.time()

    try:

        files = validate_project_files(
            request.files
        )

        context = build_project_context(
            files
        )

        prompt = make_project_prompt(
            "Test Generation",
            request.project_name or "Engineer AI Project",
            context,
            (
                "Generate a practical testing strategy.\n\n"
                "Include:\n"
                "- unit tests\n"
                "- integration tests\n"
                "- API tests where relevant\n"
                "- edge cases\n"
                "- failure cases\n"
                "- validation cases\n"
                "- security-related tests\n"
                "- performance test ideas\n\n"
                "Generate example test code where the language "
                "and framework can be identified.\n\n"
                "Do not claim any generated test actually ran."
            ),
        )

        result = await execute_model_inference_with_retry(
            prompt
        )

        analytics_data[
            "tests_generated"
        ] += 1

        record_telemetry(
            start_time,
            True
        )

        return {
            "status": "success",
            "tests": result,
        }

    except Exception as exc:

        record_telemetry(
            start_time,
            False
        )

        logger.exception(
            "Project test generation failed."
        )

        raise HTTPException(
            status_code=500,
            detail=str(exc)
        )


# ============================================================
# PROJECT SECURITY
# ============================================================

@app.post("/api/project/security")
async def project_security_endpoint(
    request: ProjectRequest
):

    start_time = time.time()

    try:

        files = validate_project_files(
            request.files
        )

        context = build_project_context(
            files
        )

        prompt = make_project_prompt(
            "Project Security Audit",
            request.project_name or "Engineer AI Project",
            context,
            (
                "Perform a defensive security audit.\n\n"
                "Review:\n"
                "- authentication\n"
                "- authorization\n"
                "- input validation\n"
                "- injection risks\n"
                "- secret handling\n"
                "- sensitive data exposure\n"
                "- insecure configuration\n"
                "- dependency risks\n"
                "- file/path handling\n"
                "- API security\n"
                "- logging/privacy\n"
                "- denial-of-service risks\n"
                "- unsafe defaults\n\n"
                "Rank findings by severity and give practical "
                "defensive remediation.\n"
                "Never expose actual secrets from the supplied files."
            ),
        )

        result = await execute_model_inference_with_retry(
            prompt
        )

        analytics_data[
            "security_scans_completed"
        ] += 1

        record_telemetry(
            start_time,
            True
        )

        return {
            "status": "success",
            "security": result,
        }

    except Exception as exc:

        record_telemetry(
            start_time,
            False
        )

        logger.exception(
            "Project security audit failed."
        )

        raise HTTPException(
            status_code=500,
            detail=str(exc)
        )


# ============================================================
# PROJECT PERFORMANCE
# ============================================================

@app.post("/api/project/performance")
async def project_performance_endpoint(
    request: ProjectRequest
):

    start_time = time.time()

    try:

        files = validate_project_files(
            request.files
        )

        context = build_project_context(
            files
        )

        prompt = make_project_prompt(
            "Performance Analysis",
            request.project_name or "Engineer AI Project",
            context,
            (
                "Perform static performance analysis.\n\n"
                "Look for:\n"
                "- unnecessary work\n"
                "- inefficient loops\n"
                "- expensive operations\n"
                                "- blocking operations\n"
                "- unnecessary network calls\n"
                "- excessive memory usage\n"
                "- large data processing\n"
                "- inefficient serialization\n"
                "- database/query inefficiencies\n"
                "- concurrency opportunities\n"
                "- caching opportunities\n"
                "- startup-time costs\n"
                "- scalability bottlenecks\n\n"
                "For every important finding provide:\n"
                "- affected file/path if known\n"
                "- performance issue\n"
                "- why it matters\n"
                "- likely impact\n"
                "- recommended optimization\n\n"
                "Do not invent benchmark numbers.\n"
                "Do not claim that performance was measured unless "
                "actual measurements were supplied."
            ),
        )

        result = await execute_model_inference_with_retry(
            prompt
        )

        analytics_data[
            "performance_analyses"
        ] += 1

        analytics_data[
            "performance_tasks"
        ] += 1

        record_telemetry(
            start_time,
            True
        )

        return {
            "status": "success",
            "performance": result,
        }

    except Exception as exc:

        record_telemetry(
            start_time,
            False
        )

        logger.exception(
            "Project performance analysis failed."
        )

        raise HTTPException(
            status_code=500,
            detail=str(exc)
        )


# ============================================================
# FULL PROJECT MULTI-AGENT WORKFLOW
#
# Planner
#    ↓
# Executor
#    ↓
# ┌──────────────┬──────────────┬────────────────┐
# Tester       Security       Performance
# └──────────────┴──────────────┴────────────────┘
#                    ↓
#                 Debugger
#
# No Verifier.
# ============================================================

@app.post("/api/project/agent")
async def project_agent_endpoint(
    request: ProjectAgentRequest,
    background_tasks: BackgroundTasks,
):

    start_time = time.time()

    try:

        files = validate_project_files(
            request.files
        )

        project_context = build_project_context(
            files
        )

        project_name = (
            request.project_name
            or "Engineer AI Project"
        )

        analytics_data[
            "agent_tasks_executed"
        ] += 1

        # ----------------------------------------------------
        # PLANNER
        # ----------------------------------------------------

        planner_prompt = make_project_prompt(
            "Project Planner Agent",
            project_name,
            project_context,
            (
                "You are the Planner Agent.\n\n"
                "Analyze the supplied project and the requested "
                "engineering task.\n\n"
                "Create a precise implementation plan.\n\n"
                "Include:\n"
                "1. Existing architecture relevant to the task\n"
                "2. Files that should be changed\n"
                "3. New files that may be needed\n"
                "4. Functions/classes/modules involved\n"
                "5. Dependencies involved\n"
                "6. Data flow\n"
                "7. Implementation steps\n"
                "8. Testing strategy\n"
                "9. Security considerations\n"
                "10. Performance considerations\n"
                "11. Risks and assumptions\n\n"
                "USER TASK:\n"
                + request.task
            ),
        )

        plan = await run_agent(
            "Project Planner",
            planner_prompt,
            "planner_tasks",
        )

        # ----------------------------------------------------
        # EXECUTOR
        # ----------------------------------------------------

        executor_prompt = make_project_prompt(
            "Project Executor Agent",
            project_name,
            project_context,
            (
                "You are the Executor Agent.\n\n"
                "Implement the requested engineering task using "
                "the supplied project context and planner output.\n\n"
                "Prefer minimal, maintainable changes that fit the "
                "existing architecture.\n\n"
                "Clearly identify:\n"
                "- files to modify\n"
                "- files to create\n"
                "- important code changes\n"
                "- configuration changes\n"
                "- dependency changes\n"
                "- migration considerations\n\n"
                "Provide practical code where appropriate.\n\n"
                "USER TASK:\n"
                + request.task
                + "\n\n"
                "PLANNER OUTPUT:\n"
                + plan
            ),
        )

        implementation = await run_agent(
            "Project Executor",
            executor_prompt,
            "executor_tasks",
        )

        # ----------------------------------------------------
        # TESTER
        # ----------------------------------------------------

        tester_task = None

        if request.include_tests:

            analytics_data[
                "tests_generated"
            ] += 1

            tester_prompt = make_project_prompt(
                "Project Tester Agent",
                project_name,
                project_context,
                (
                    "You are the Tester Agent.\n\n"
                    "Design tests for the proposed project "
                    "implementation.\n\n"
                    "Include:\n"
                    "- unit tests\n"
                    "- integration tests\n"
                    "- API tests where relevant\n"
                    "- edge cases\n"
                    "- failure cases\n"
                    "- regression tests\n"
                    "- validation tests\n"
                    "- security-related test cases\n"
                    "- performance test ideas\n\n"
                    "Generate example test code when the project's "
                    "language and framework can be identified.\n\n"
                    "Do not claim that any test actually ran.\n\n"
                    "USER TASK:\n"
                    + request.task
                    + "\n\n"
                    "PLAN:\n"
                    + plan
                    + "\n\n"
                    "PROPOSED IMPLEMENTATION:\n"
                    + implementation
                ),
            )

            tester_task = run_agent(
                "Project Tester",
                tester_prompt,
                "tester_tasks",
            )

        # ----------------------------------------------------
        # SECURITY
        # ----------------------------------------------------

        security_task = None

        if request.include_security:

            analytics_data[
                "security_scans_completed"
            ] += 1

            security_prompt = make_project_prompt(
                "Project Security Agent",
                project_name,
                project_context,
                (
                    "You are the Security Agent.\n\n"
                    "Perform a defensive security analysis of "
                    "the proposed implementation.\n\n"
                    "Review:\n"
                    "- authentication\n"
                    "- authorization\n"
                    "- input validation\n"
                    "- injection risks\n"
                    "- path traversal\n"
                    "- unsafe file handling\n"
                    "- secrets and credentials\n"
                    "- sensitive data exposure\n"
                    "- dependency risks\n"
                    "- insecure configuration\n"
                    "- API security\n"
                    "- logging and privacy\n"
                    "- denial-of-service risks\n"
                    "- resource exhaustion\n"
                    "- unsafe defaults\n"
                    "- error handling\n\n"
                    "For every important finding provide:\n"
                    "- severity\n"
                    "- affected file/path if known\n"
                    "- explanation\n"
                    "- defensive mitigation\n\n"
                    "Never expose actual secrets.\n"
                    "Do not claim that a vulnerability was exploited.\n"
                    "Do not claim that security tests actually ran.\n\n"
                    "USER TASK:\n"
                    + request.task
                    + "\n\n"
                    "PROPOSED IMPLEMENTATION:\n"
                    + implementation
                ),
            )

            security_task = run_agent(
                "Project Security",
                security_prompt,
                "security_tasks",
            )

        # ----------------------------------------------------
        # PERFORMANCE
        # ----------------------------------------------------

        performance_task = None

        if request.include_performance:

            analytics_data[
                "performance_analyses"
            ] += 1

            analytics_data[
                "performance_tasks"
            ] += 1

            performance_prompt = make_project_prompt(
                "Project Performance Agent",
                project_name,
                project_context,
                (
                    "You are the Performance Agent.\n\n"
                    "Perform a static performance review of the "
                    "proposed implementation.\n\n"
                    "Look for:\n"
                    "- unnecessary computation\n"
                    "- inefficient algorithms\n"
                    "- inefficient loops\n"
                    "- blocking operations\n"
                    "- excessive memory usage\n"
                    "- unnecessary network calls\n"
                    "- database/query inefficiencies\n"
                    "- serialization overhead\n"
                    "- concurrency opportunities\n"
                    "- caching opportunities\n"
                    "- startup costs\n"
                    "- scalability bottlenecks\n\n"
                    "Do not invent benchmark results.\n"
                    "Do not claim performance was measured.\n\n"
                    "USER TASK:\n"
                    + request.task
                    + "\n\n"
                    "PROPOSED IMPLEMENTATION:\n"
                    + implementation
                ),
            )

            performance_task = run_agent(
                "Project Performance",
                performance_prompt,
                "performance_tasks",
            )

        # ----------------------------------------------------
        # RUN TESTER + SECURITY + PERFORMANCE CONCURRENTLY
        # ----------------------------------------------------

        parallel_results = await asyncio.gather(
            tester_task
            if tester_task is not None
            else asyncio.sleep(
                0,
                result=None
            ),

            security_task
            if security_task is not None
            else asyncio.sleep(
                0,
                result=None
            ),

            performance_task
            if performance_task is not None
            else asyncio.sleep(
                0,
                result=None
            ),

            return_exceptions=True,
        )

        tests_result = parallel_results[0]
        security_result = parallel_results[1]
        performance_result = parallel_results[2]

        if isinstance(
            tests_result,
            Exception
        ):

            tests_result = (
                "Tester Agent failed: "
                + str(tests_result)
            )

        if isinstance(
            security_result,
            Exception
        ):

            security_result = (
                "Security Agent failed: "
                + str(security_result)
            )

        if isinstance(
            performance_result,
            Exception
        ):

            performance_result = (
                "Performance Agent failed: "
                + str(performance_result)
            )

        # ----------------------------------------------------
        # DEBUGGER
        # ----------------------------------------------------

        debugger_prompt = make_project_prompt(
            "Project Debugger Agent",
            project_name,
            project_context,
            (
                "You are the Debugger Agent.\n\n"
                "Review the entire proposed change and the "
                "outputs from the other engineering agents.\n\n"
                "Identify:\n"
                "- likely bugs\n"
                "- incorrect assumptions\n"
                "- implementation inconsistencies\n"
                "- missing requirements\n"
                "- reliability problems\n"
                "- testing gaps\n"
                "- security concerns\n"
                "- performance concerns\n"
                "- architecture problems\n"
                "- integration risks\n\n"
                "For every important issue provide:\n"
                "1. Severity\n"
                "2. Affected file/path if known\n"
                "3. Problem\n"
                "4. Why it matters\n"
                "5. Recommended fix\n\n"
                "Finish with:\n"
                "- corrected implementation guidance\n"
                "- remaining assumptions\n"
                "- remaining risks\n\n"
                "Do not claim that code was executed.\n"
                "Do not claim tests passed unless actual test "
                "results were supplied.\n\n"
                "USER TASK:\n"
                + request.task
                + "\n\n"
                "PLANNER:\n"
                + plan
                + "\n\n"
                "EXECUTOR:\n"
                + implementation
                + "\n\n"
                "TESTER:\n"
                + str(tests_result)
                + "\n\n"
                "SECURITY:\n"
                + str(security_result)
                + "\n\n"
                "PERFORMANCE:\n"
                + str(performance_result)
            ),
        )

        debugger_result = await run_agent(
            "Project Debugger",
            debugger_prompt,
            "debugger_tasks",
        )

        # ----------------------------------------------------
        # TELEMETRY
        # ----------------------------------------------------

        background_tasks.add_task(
            record_telemetry,
            start_time,
            True,
        )

        # ----------------------------------------------------
        # RESPONSE
        # ----------------------------------------------------

        return {
            "status": "success",

            "project": {
                "name": project_name,
                "files_analyzed": len(files),
            },

            "workflow": {
                "task": request.task,
                "planner": plan,
                "executor": implementation,
                "tester": tests_result,
                "security": security_result,
                "performance": performance_result,
                "debugger": debugger_result,
            },

            # Backward-compatible structure
            "agent_outputs": {
                "plan": plan,
                "implementation": implementation,
                "tests": tests_result,
                "security_scan": security_result,
                "performance": performance_result,
                "debugger": debugger_result,
            },
        }

    except Exception as exc:

        background_tasks.add_task(
            record_telemetry,
            start_time,
            False,
        )

        logger.exception(
            "Project multi-agent workflow failed."
        )

        return {
            "status": "error",
            "message": (
                "Project Multi-Agent Execution Error: "
                + str(exc)
            ),
            "workflow": None,
        }


# ============================================================
# OPERATIONS / TELEMETRY
# ============================================================

@app.get("/api/ops/analytics")
async def get_analytics():

    total_requests = analytics_data[
        "total_requests"
    ]

    average_latency = (
        analytics_data[
            "total_latency_seconds"
        ]
        / total_requests
        if total_requests > 0
        else 0.0
    )

    success_rate = (
        analytics_data[
            "successful_responses"
        ]
        / total_requests
        * 100
        if total_requests > 0
        else 0.0
    )

    return {
        # Existing dashboard compatibility
        "total_requests": total_requests,

        "successful_responses": analytics_data[
            "successful_responses"
        ],

        "failed_requests": analytics_data[
            "failed_requests"
        ],

        "success_rate_percent": round(
            success_rate,
            2
        ),

        "average_latency_seconds": round(
            average_latency,
            3
        ),

        "agent_tasks_executed": analytics_data[
            "agent_tasks_executed"
        ],

        # Full metrics
        "metrics": analytics_data,

        "system_status": "Operational",

        "service": APP_TITLE,

        "version": APP_VERSION,

        "provider": DEFAULT_PROVIDER,

        "model": DEFAULT_MODEL,
    }


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup_event():

    logger.info(
        "=================================================="
    )

    logger.info(
        "Engineer AI Engine starting..."
    )

    logger.info(
        "Version: %s",
        APP_VERSION
    )

    logger.info(
        "Provider: %s",
        DEFAULT_PROVIDER
    )

    logger.info(
        "Model: %s",
        DEFAULT_MODEL
    )

    logger.info(
        "Max project files: %s",
        MAX_PROJECT_FILES
    )

    logger.info(
        "Max project context: %s characters",
        MAX_PROJECT_CONTEXT_CHARS
    )

    if DEFAULT_PROVIDER == "gemini":

        if os.getenv("GEMINI_API_KEY"):

            logger.info(
                "Gemini API key detected."
            )

        else:

            logger.warning(
                "GEMINI_API_KEY is not configured."
            )

    elif DEFAULT_PROVIDER == "local_cuda":

        logger.warning(
            "Local CUDA provider selected, but the "
            "local inference engine is not configured."
        )


# ============================================================
# LOCAL DEVELOPMENT
# ============================================================

if __name__ == "__main__":

    import uvicorn

    port = int(
        os.getenv(
            "PORT",
            "8000"
        )
    )

    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=port,
        reload=False,
    )
   