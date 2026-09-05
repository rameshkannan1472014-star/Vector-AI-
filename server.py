import os
import time
import asyncio
import logging
from typing import Optional, Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from google import genai
from google.genai import types


# ============================================================
# ENGINEER AI BACKEND
# Stable + Faster Backend
# UI remains unchanged
# ============================================================

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("engineer-ai")


# ============================================================
# CONFIGURATION
# ============================================================

APP_TITLE = os.getenv(
    "APP_TITLE",
    "Engineer AI Engine"
)

DEFAULT_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-2.5-flash"
)

DEFAULT_PROVIDER = "gemini"

MAX_RETRIES = int(
    os.getenv("GEMINI_MAX_RETRIES", "1")
)

RETRY_BASE_SECONDS = float(
    os.getenv("GEMINI_RETRY_BASE_SECONDS", "0.8")
)

CHAT_MAX_OUTPUT_TOKENS = int(
    os.getenv("CHAT_MAX_OUTPUT_TOKENS", "1536")
)

AGENT_MAX_OUTPUT_TOKENS = int(
    os.getenv("AGENT_MAX_OUTPUT_TOKENS", "1400")
)

REVIEW_MAX_OUTPUT_TOKENS = int(
    os.getenv("REVIEW_MAX_OUTPUT_TOKENS", "1200")
)

MAX_PROMPT_CHARS = int(
    os.getenv("MAX_PROMPT_CHARS", "24000")
)

MAX_CODE_CHARS = int(
    os.getenv("MAX_CODE_CHARS", "30000")
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
    "security_scans_completed": 0,
    "tests_generated": 0,

    "debugger_runs": 0,
    "verifier_runs": 0,
}


# ============================================================
# HELPERS
# ============================================================

def clamp_text(value: str, limit: int) -> str:
    """
    Prevent accidentally huge prompts from slowing down
    the AI backend.
    """

    value = value or ""

    if len(value) <= limit:
        return value

    return (
        value[:limit]
        + "\n\n[Input truncated by Engineer AI backend.]"
    )


def get_cors_origins() -> list[str]:
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


# ============================================================
# FASTAPI APP
# ============================================================

origins = get_cors_origins()

app = FastAPI(
    title=APP_TITLE,
    version="2.0.0",
    description="Engineer AI engineering assistant backend",
)


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
        max_length=MAX_PROMPT_CHARS
    )

    provider: Optional[str] = Field(
        default=DEFAULT_PROVIDER,
        max_length=50
    )


class MultiAgentRequest(BaseModel):

    # New frontend field
    task: Optional[str] = Field(
        default=None,
        max_length=MAX_PROMPT_CHARS
    )

    # Old frontend field compatibility
    task_description: Optional[str] = Field(
        default=None,
        max_length=MAX_PROMPT_CHARS
    )

    include_tests: bool = True

    include_security: Optional[bool] = None

    include_security_scan: Optional[bool] = None

    include_debugger: bool = True

    include_verifier: bool = True

    project_context: Optional[str] = Field(
        default=None,
        max_length=MAX_PROMPT_CHARS
    )

    def get_task(self) -> str:

        task = (
            self.task
            or self.task_description
        )

        if not task:
            raise ValueError(
                "task or task_description is required"
            )

        return clamp_text(
            task,
            MAX_PROMPT_CHARS
        )

    def get_security_enabled(self) -> bool:

        if self.include_security is not None:
            return self.include_security

        if self.include_security_scan is not None:
            return self.include_security_scan

        return True


class CodeReviewRequest(BaseModel):

    code_snippet: str = Field(
        ...,
        min_length=1,
        max_length=MAX_CODE_CHARS
    )

    language: str = Field(
        default="text",
        min_length=1,
        max_length=50
    )


# ============================================================
# ENGINEER AI SYSTEM INSTRUCTION
# ============================================================

SYSTEM_INSTRUCTION = """
You are Engineer AI, an engineering-focused AI assistant.

Your responsibilities include:

- Engineering
- Software development
- Architecture
- Electronics
- Hardware
- Debugging
- Technical analysis
- System design
- Code review
- Testing strategy
- Security analysis

Rules:

1. Give technically useful and clear answers.

2. Separate known facts from assumptions.

3. Show important formulas, units, constraints,
   and calculations when relevant.

4. Never claim that code was executed unless
   the backend actually executed it.

5. Never claim that hardware was physically tested
   unless physical testing actually occurred.

6. Never claim that tests passed unless they were
   actually executed.

7. Never claim that a security scan was performed
   by a real security scanner unless one was actually used.

8. When asked for code, provide complete runnable
   code whenever practical.

9. For engineering designs, consider important
   electrical, thermal, mechanical, reliability,
   and safety constraints.

10. When the user requests a diagram, use Mermaid
    when appropriate.

11. Do not invent measurements or test results.

12. Keep answers focused and useful.

13. If information is uncertain, clearly say so.
"""


# ============================================================
# GEMINI CLIENT
# ============================================================

def get_gemini_client() -> genai.Client:

    api_key = os.getenv(
        "GEMINI_API_KEY"
    )

    if not api_key:

        raise RuntimeError(
            "GEMINI_API_KEY is not configured on the server."
        )

    return genai.Client(
        api_key=api_key
    )


# ============================================================
# SYNCHRONOUS GEMINI CALL
# ============================================================

def generate_gemini_sync(
    prompt: str,
    max_output_tokens: int,
) -> str:

    client = get_gemini_client()

    response = client.models.generate_content(
        model=DEFAULT_MODEL,

        contents=prompt,

        config=types.GenerateContentConfig(

            system_instruction=SYSTEM_INSTRUCTION,

            temperature=0.2,

            max_output_tokens=max_output_tokens,
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


# ============================================================
# ASYNC GEMINI INFERENCE
# ============================================================

async def execute_model_inference(
    prompt: str,
    max_output_tokens: int,
) -> str:

    """
    Google GenAI's generate_content call is synchronous.

    We run it in a worker thread so it does NOT block
    FastAPI's event loop.

    This is one of the major speed/stability fixes.
    """

    prompt = clamp_text(
        prompt,
        MAX_PROMPT_CHARS
    )

    last_error: Optional[Exception] = None

    for attempt in range(
        MAX_RETRIES + 1
    ):

        try:

            return await asyncio.to_thread(
                generate_gemini_sync,
                prompt,
                max_output_tokens,
            )

        except Exception as exc:

            last_error = exc

            error_text = str(
                exc
            ).upper()

            retryable = any(
                marker in error_text
                for marker in (
                    "503",
                    "UNAVAILABLE",
                    "429",
                    "RESOURCE_EXHAUSTED",
                    "DEADLINE",
                    "TIMEOUT",
                )
            )

            if (
                not retryable
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

    raise (
        last_error
        or RuntimeError(
            "Gemini inference failed."
        )
    )


# ============================================================
# TELEMETRY
# ============================================================

def record_request(
    start_time: float,
    success: bool,
) -> None:

    analytics_data[
        "total_requests"
    ] += 1

    analytics_data[
        "total_latency_seconds"
    ] += (
        time.perf_counter()
        - start_time
    )

    if success:

        analytics_data[
            "successful_responses"
        ] += 1

    else:

        analytics_data[
            "failed_requests"
        ] += 1


# ============================================================
# AGENT RUNNER
# ============================================================

async def run_agent(
    agent_name: str,
    prompt: str,
    max_output_tokens: int = AGENT_MAX_OUTPUT_TOKENS,
) -> dict[str, Any]:

    start = time.perf_counter()

    try:

        output = await execute_model_inference(
            prompt,
            max_output_tokens=max_output_tokens,
        )

        return {

            "agent": agent_name,

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
            "%s failed",
            agent_name
        )

        return {

            "agent": agent_name,

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

        "status": "online",

        "message": (
            "Backend is running. "
            "index.html was not found."
        ),
    }


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/api/health")
async def health():

    return {

        "status": "healthy",

        "service": "Engineer AI",

        "model": DEFAULT_MODEL,

        "provider": DEFAULT_PROVIDER,

        "api_key_configured": bool(
            os.getenv(
                "GEMINI_API_KEY"
            )
        ),
    }


# ============================================================
# NORMAL CHAT
# ============================================================

@app.post("/api/chat")
async def chat(
    request: ChatRequest
):

    start = time.perf_counter()

    try:

        if (
            request.provider
            and request.provider.lower()
            != "gemini"
        ):

            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unsupported provider: "
                    f"{request.provider}"
                ),
            )

        response = await execute_model_inference(

            request.prompt,

            max_output_tokens=(
                CHAT_MAX_OUTPUT_TOKENS
            ),
        )

        record_request(
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

        record_request(
            start,
            False
        )

        raise

    except Exception as exc:

        record_request(
            start,
            False
        )

        logger.exception(
            "Chat request failed"
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
# MULTI-AGENT ENGINE
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

        security_enabled = (
            request.get_security_enabled()
        )

        # ----------------------------------------------------
        # PROJECT CONTEXT
        # ----------------------------------------------------

        context = ""

        if request.project_context:

            context = (
                "\n\nProject context:\n"
                + clamp_text(
                    request.project_context,
                    MAX_PROMPT_CHARS
                )
            )

        # ----------------------------------------------------
        # 1. PLANNER
        # ----------------------------------------------------

        planner = await run_agent(

            "Planner",

            f"""
Deconstruct this engineering task into
a concise implementation plan.

Identify:

- Requirements
- Assumptions
- Architecture
- Dependencies
- Risks
- Validation steps

Engineering task:

{task}

{context}
""",
        )

        # ----------------------------------------------------
        # 2. EXECUTOR
        # ----------------------------------------------------

        executor = await run_agent(

            "Executor",

            f"""
Act as the implementation engineer.

Engineering task:

{task}

Planner output:

{planner["output"]}

Provide the implementation, code,
architecture, or technical design needed
to solve the task.

Do not claim that anything was executed.

{context}
""",
        )

        # ----------------------------------------------------
        # 3 + 4. TESTER AND SECURITY
        # RUN IN PARALLEL
        # ----------------------------------------------------

        parallel_jobs = []

        # TESTER

        if request.include_tests:

            analytics_data[
                "tests_generated"
            ] += 1

            parallel_jobs.append(

                run_agent(

                    "Tester",

                    f"""
Generate tests, assertions,
edge cases, and validation procedures
for the proposed implementation.

Engineering task:

{task}

Implementation:

{executor["output"]}
""",
                )
            )

        else:

            parallel_jobs.append(

                asyncio.sleep(

                    0,

                    result={

                        "agent": "Tester",

                        "status": "skipped",

                        "output": "",

                        "latency_seconds": 0.0,
                    },
                )
            )

        # SECURITY

        if security_enabled:

            analytics_data[
                "security_scans_completed"
            ] += 1

            parallel_jobs.append(

                run_agent(

                    "Security",

                    f"""
Perform a static security and
engineering-safety review.

Look for:

- Software vulnerabilities
- Unsafe assumptions
- Input validation issues
- Secrets exposure
- Resource abuse
- Electrical concerns
- Thermal concerns
- Hardware safety concerns

Do NOT claim that a real runtime
security scanner was executed.

Engineering task:

{task}

Implementation:

{executor["output"]}
""",
                )
            )

        else:

            parallel_jobs.append(

                asyncio.sleep(

                    0,

                    result={

                        "agent": "Security",

                        "status": "skipped",

                        "output": "",

                        "latency_seconds": 0.0,
                    },
                )
            )

        tester, security = await asyncio.gather(
            *parallel_jobs
        )

        # ----------------------------------------------------
        # 5. DEBUGGER
        # ----------------------------------------------------

        if request.include_debugger:

            analytics_data[
                "debugger_runs"
            ] += 1

            debugger = await run_agent(

                "Debugger",

                f"""
Analyze the implementation and
the Tester/Security findings.

Identify:

- Likely bugs
- Contradictions
- Missing cases
- Incorrect assumptions
- Concrete fixes

Engineering task:

{task}

Implementation:

{executor["output"]}

Tester:

{tester["output"]}

Security:

{security["output"]}
""",
            )

        else:

            debugger = {

                "agent": "Debugger",

                "status": "skipped",

                "output": "",

                "latency_seconds": 0.0,
            }

        # ----------------------------------------------------
        # 6. VERIFIER
        # ----------------------------------------------------

        if request.include_verifier:

            analytics_data[
                "verifier_runs"
            ] += 1

            verifier = await run_agent(

                "Verifier",

                f"""
Verify the engineering response.

Check:

- Requirement coverage
- Consistency
- Technical correctness
- Important missing cases
- Test quality
- Security findings
- Debugging fixes

Important:

This is static reasoning only.

Do not claim that code or hardware
was actually executed or physically tested.

Engineering task:

{task}

Plan:

{planner["output"]}

Implementation:

{executor["output"]}

Tester:

{tester["output"]}

Security:

{security["output"]}

Debugger:

{debugger["output"]}
""",
            )

        else:

            verifier = {

                "agent": "Verifier",

                "status": "skipped",

                "output": "",

                "latency_seconds": 0.0,
            }

               # ----------------------------------------------------
        # WORKFLOW
        # ----------------------------------------------------

        workflow = {
            "planner": planner,
            "executor": executor,
            "tester": tester,
            "security": security,
            "debugger": debugger,
            "verifier": verifier,
        }

        latency = round(
            time.perf_counter() - start,
            3
        )

        record_request(
            start,
            True
        )

        return {
            "status": "success",

            "workflow": workflow,

            # Compatibility with the existing UI
            "agent_outputs": {
                "plan": planner,
                "implementation": executor,
                "tests": tester,
                "security_scan": security,
                "debugger": debugger,
                "verifier": verifier,
            },

            # No arbitrary code execution on the server
            "execution": {
                "status": "not_executed",
                "message": (
                    "Engineer AI generated and statically "
                    "reviewed the solution. No arbitrary "
                    "code was executed on the server."
                ),
            },

            "latency_seconds": latency,
        }

    except HTTPException:
        record_request(
            start,
            False
        )
        raise

    except Exception as exc:
        record_request(
            start,
            False
        )

        logger.exception(
            "Multi-agent workflow failed"
        )

        raise HTTPException(
            status_code=502,
            detail={
                "error": "Multi-agent workflow failed",
                "message": str(exc),
            },
        )


# ============================================================
# CODE REVIEW
# ============================================================

@app.post("/api/code/review")
async def code_review_endpoint(
    request: CodeReviewRequest
):

    start = time.perf_counter()

    code = clamp_text(
        request.code_snippet,
        MAX_CODE_CHARS
    )

    language = request.language.strip()

    prompt = f"""
Review this {language} code as a senior
software and engineering reviewer.

Return a concise review with these sections:

1. Critical bugs
2. Security issues
3. Performance issues
4. Engineering/safety concerns, if relevant
5. Concrete fixes
6. Overall verdict

Do not claim that the code was executed.

Do not claim that tests passed.

If something cannot be determined
statically, say so.

CODE:

```{language}
{code}