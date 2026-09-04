import os
import time
import asyncio
import logging
import threading
from typing import Optional, Dict, Any, List

from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from google import genai
from google.genai import types


# ============================================================
# ENGINEER AI — MILESTONE 1 CORE
# ============================================================

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("EngineerAI")


# ============================================================
# CONFIGURATION
# ============================================================

APP_TITLE = "Engineer AI Engine"

DEFAULT_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-2.5-flash"
)

DEFAULT_PROVIDER = os.getenv(
    "DEFAULT_PROVIDER",
    "gemini"
)

cors_origins_raw = os.getenv(
    "CORS_ORIGINS",
    "*"
)

CORS_ORIGINS = [
    origin.strip()
    for origin in cors_origins_raw.split(",")
    if origin.strip()
]

if not CORS_ORIGINS:
    CORS_ORIGINS = ["*"]


# ============================================================
# ANALYTICS
# ============================================================

analytics_data: Dict[str, Any] = {
    "total_requests": 0,
    "successful_responses": 0,
    "failed_requests": 0,
    "total_latency_seconds": 0.0,

    "agent_tasks_executed": 0,
    "security_scans_completed": 0,
    "tests_generated": 0,
    "verifications_completed": 0,
    "debugger_runs": 0,

    "agent_runs": {
        "planner": 0,
        "executor": 0,
        "tester": 0,
        "security": 0,
        "debugger": 0,
        "verifier": 0,
    }
}

analytics_lock = threading.Lock()


def increment_metric(
    name: str,
    amount: int = 1
) -> None:

    with analytics_lock:
        analytics_data[name] = (
            analytics_data.get(name, 0) + amount
        )


def increment_agent_metric(
    agent_name: str
) -> None:

    with analytics_lock:
        analytics_data["agent_runs"][agent_name] = (
            analytics_data["agent_runs"].get(agent_name, 0) + 1
        )


def log_telemetry(
    start_time: float,
    success: bool
) -> None:

    latency = time.time() - start_time

    with analytics_lock:

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
# FASTAPI
# ============================================================

app = FastAPI(
    title=APP_TITLE,
    version="1.1.0",
    description=(
        "Engineer AI engineering assistant "
        "and multi-agent orchestration engine."
    )
)


app.add_middleware(
    CORSMiddleware,

    allow_origins=CORS_ORIGINS,

    allow_credentials=(
        False
        if "*" in CORS_ORIGINS
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
        max_length=20000
    )

    provider: Optional[str] = DEFAULT_PROVIDER


class MultiAgentRequest(BaseModel):

    task_description: str = Field(
        ...,
        min_length=1,
        max_length=30000
    )

    include_tests: bool = True

    include_security_scan: bool = True

    include_debugger: bool = True

    provider: Optional[str] = DEFAULT_PROVIDER

    project_context: Optional[str] = Field(
        default=None,
        max_length=50000
    )


class CodeReviewRequest(BaseModel):

    code_snippet: str = Field(
        ...,
        min_length=1,
        max_length=50000
    )

    language: str = Field(
        ...,
        min_length=1,
        max_length=100
    )

    provider: Optional[str] = DEFAULT_PROVIDER


# ============================================================
# ENGINEER AI SYSTEM INSTRUCTION
# ============================================================

SYSTEM_INSTRUCTION = (

    "You are Engineer AI, an advanced engineering assistant.\n\n"

    "PURPOSE\n"
    "Help users understand, design, calculate, troubleshoot, "
    "simulate, review, and implement engineering systems.\n\n"

    "CORE RULES\n"

    "1. Answer the user's exact request.\n"

    "2. Be technically accurate and explicit about uncertainty.\n"

    "3. Explain concepts clearly and logically.\n"

    "4. Never invent facts, specifications, measurements, "
    "test results, or sources.\n"

    "5. If information is missing, state assumptions "
    "or request it.\n"

    "6. For calculations, show formula, known values, "
    "calculation, final result, and units.\n"

    "7. Check calculations before giving the final answer.\n"

    "8. Clearly distinguish assumptions from confirmed facts.\n"

    "9. Explain important engineering trade-offs.\n"

    "10. Never claim that code, hardware, simulations, "
    "tests, or measurements were executed or verified "
    "unless the system actually performed that work.\n"

    "11. If execution is unavailable, say so explicitly.\n\n"

    "MULTI-AGENT RULES\n"

    "Planner creates the plan.\n"

    "Executor creates the proposed implementation.\n"

    "Tester creates tests and validation logic.\n"

    "Security Agent audits security and engineering safety.\n"

    "Debugger analyzes known or likely failure points.\n"

    "Verifier checks consistency between workflow artifacts.\n\n"

    "IMPORTANT:\n"

    "Do not claim tests actually ran.\n"
    "Do not claim a live security scan occurred.\n"
    "Do not invent runtime stack traces.\n"
    "Do not claim hardware was physically tested.\n\n"

    "DIAGRAM GENERATION\n"

    "When generating Mermaid diagrams:\n"

    "1. ALWAYS use a fenced ```mermaid code block.\n"

    "2. Use clean node IDs without spaces or special characters.\n"

    "3. Put human-readable labels in double quotes.\n"

    "4. Avoid parentheses, slashes, and plus/minus characters "
    "inside raw node IDs.\n"

    "5. Use simple graph TD or graph LR syntax.\n\n"

    "EXAMPLE:\n"

    "```mermaid\n"
    "graph TD\n"
    "    A[\"Power Source\"] --> B[\"Resistor\"]\n"
    "    B --> C[\"LED Anode\"]\n"
    "    C --> D[\"GND\"]\n"
    "```\n"
)


# ============================================================
# GEMINI PROVIDER
# ============================================================

def get_gemini_client() -> genai.Client:

    api_key = os.getenv(
        "GEMINI_API_KEY"
    )

    if not api_key:

        raise RuntimeError(
            "GEMINI_API_KEY environment variable is missing."
        )

    return genai.Client(
        api_key=api_key
    )


async def execute_model_inference_with_retry(
    prompt: str,
    provider: str = DEFAULT_PROVIDER,
    custom_system_prompt: Optional[str] = None,
    max_retries: int = 3
) -> str:

    provider = (
        provider or DEFAULT_PROVIDER
    ).strip().lower()


    # --------------------------------------------------------
    # GEMINI
    # --------------------------------------------------------

    if provider == "gemini":

        client = get_gemini_client()

        instruction = (
            custom_system_prompt
            or SYSTEM_INSTRUCTION
        )

        for attempt in range(
            1,
            max_retries + 1
        ):

            try:

                def call_model():

                    return client.models.generate_content(

                        model=DEFAULT_MODEL,

                        contents=prompt,

                        config=types.GenerateContentConfig(

                            system_instruction=instruction

                        )
                    )


                # Prevent blocking FastAPI's event loop.
                response = await asyncio.to_thread(
                    call_model
                )


                text = getattr(
                    response,
                    "text",
                    None
                )


                if text and text.strip():

                    return text.strip()


                return (
                    "Empty response received from model."
                )


            except Exception as exc:

                error_str = str(exc)

                retryable = (

                    "503" in error_str

                    or
                    "UNAVAILABLE"
                    in error_str.upper()

                    or
                    "429" in error_str

                    or
                    "RESOURCE_EXHAUSTED"
                    in error_str.upper()
                )


                if (
                    retryable
                    and attempt < max_retries
                ):

                    delay = 2 * attempt

                    logger.warning(
                        "Temporary model failure. "
                        f"Retrying attempt "
                        f"{attempt}/{max_retries} "
                        f"in {delay}s."
                    )

                    await asyncio.sleep(
                        delay
                    )

                    continue


                raise


    # --------------------------------------------------------
    # LOCAL CUDA
    # --------------------------------------------------------

    elif provider == "local_cuda":

        return (
            "LOCAL_CUDA_NOT_CONFIGURED: "
            "CUDA/TensorRT local inference is not "
            "configured on this host. "
            "No local GPU inference was performed."
        )


    else:

        raise ValueError(
            f"Unknown provider: {provider}"
        )


# ============================================================
# AGENT RUNNER
# ============================================================

async def run_agent(
    agent_name: str,
    prompt: str,
    provider: str,
    system_prompt: Optional[str] = None
) -> Dict[str, Any]:

    increment_agent_metric(
        agent_name
    )

    started = time.time()

    try:

        output = (
            await execute_model_inference_with_retry(
                prompt=prompt,
                provider=provider,
                custom_system_prompt=system_prompt
            )
        )


        return {

            "agent": agent_name,

            "status": "success",

            "output": output,

            "latency_seconds": round(
                time.time() - started,
                3
            )
        }


    except Exception as exc:

        logger.exception(
            "%s agent failed",
            agent_name
        )

        return {

            "agent": agent_name,

            "status": "error",

            "output": None,

            "error": str(exc),

            "latency_seconds": round(
                time.time() - started,
                3
            )
        }


# ============================================================
# PROJECT CONTEXT
# ============================================================

def build_context_block(
    project_context: Optional[str]
) -> str:

    if not project_context:

        return (
            "PROJECT CONTEXT:\n"
            "No repository/project context was supplied.\n"
            "Do not pretend to know files that were not provided.\n"
        )


    return (
        "PROJECT CONTEXT:\n"
        f"{project_context}\n"
    )


# ============================================================
# HEALTH
# ============================================================

@app.get("/api/health")
async def health_check():

    return {

        "status": "ok",

        "service": APP_TITLE,

        "version": "1.1.0",

        "model": DEFAULT_MODEL,

        "default_provider": DEFAULT_PROVIDER,

        "execution_engine": {

            "status": "not_enabled",

            "message": (
                "Milestone 1 does not execute arbitrary "
                "project code. Sandboxed execution "
                "will be added separately."
            )
        }
    }


# ============================================================
# FRONTEND
# ============================================================

@app.get("/")
async def serve_ui():

    if os.path.exists(
        "index.html"
    ):

        return FileResponse(
            "index.html"
        )


    raise HTTPException(
        status_code=404,
        detail="index.html not found"
    )


# ============================================================
# NORMAL CHAT
# ============================================================

@app.post("/api/chat")
async def chat_endpoint(
    request: ChatRequest,
    background_tasks: BackgroundTasks
):

    start_time = time.time()

    try:

        response_text = (
            await execute_model_inference_with_retry(
                request.prompt,
                request.provider
                or DEFAULT_PROVIDER
            )
        )


        background_tasks.add_task(
            log_telemetry,
            start_time,
            True
        )


        return {

            "status": "success",

            "response": response_text,

            "provider": (
                request.provider
                or DEFAULT_PROVIDER
            ),

            "model": DEFAULT_MODEL
        }


    except Exception as exc:

        background_tasks.add_task(
            log_telemetry,
            start_time,
            False
        )

        logger.exception(
            "Chat inference error"
        )


        raise HTTPException(

            status_code=502,

            detail={

                "code":
                    "MODEL_INFERENCE_ERROR",

                "message":
                    str(exc)
            }
        )


# ============================================================
# MULTI-AGENT ENGINE
# ============================================================

@app.post("/api/agent/execute")
async def multi_agent_endpoint(
    request: MultiAgentRequest,
    background_tasks: BackgroundTasks
):

    start_time = time.time()

    increment_metric(
        "agent_tasks_executed"
    )

    provider = (
        request.provider
        or DEFAULT_PROVIDER
    )

    context = build_context_block(
        request.project_context
    )


    try:

        # ====================================================
        # 1. PLANNER
        # ====================================================

        planner_prompt = f"""

You are the Planner Agent.

Break the engineering task into a practical sequence.

Identify:

- Inputs
- Missing information
- Requirements
- Constraints
- Assumptions
- Implementation steps
- Validation requirements
- Likely failure points

Do not claim anything was executed.

{context}

USER TASK:

{request.task_description}

"""


        planner_result = await run_agent(

            agent_name="planner",

            prompt=planner_prompt,

            provider=provider
        )


        if (
            planner_result["status"]
            != "success"
        ):

            raise RuntimeError(
                "Planner failed: "
                + str(
                    planner_result.get(
                        "error",
                        "unknown error"
                    )
                )
            )


        plan = planner_result[
            "output"
        ]


        # ====================================================
        # 2. EXECUTOR
        # ====================================================

        executor_prompt = f"""

You are the Executor Agent.

Create the proposed technical implementation.

Produce concrete:

- Code
- Architecture
- Schematics
- Calculations
- Configuration
- Implementation steps

as appropriate.

Preserve all constraints from the plan.

State assumptions.

Include validation considerations.

Never claim that the result was actually executed.

{context}

USER TASK:

{request.task_description}

PLANNER OUTPUT:

{plan}

"""


        executor_result = await run_agent(

            agent_name="executor",

            prompt=executor_prompt,

            provider=provider
        )


        if (
            executor_result["status"]
            != "success"
        ):

            raise RuntimeError(
                "Executor failed: "
                + str(
                    executor_result.get(
                        "error",
                        "unknown error"
                    )
                )
            )


        implementation = (
            executor_result["output"]
        )


        # ====================================================
        # 3. TESTER + SECURITY IN PARALLEL
        # ====================================================

        parallel_tasks = []


        tests_enabled = (
            request.include_tests
        )

        security_enabled = (
            request.include_security_scan
        )


        if tests_enabled:

            increment_metric(
                "tests_generated"
            )


            test_prompt = f"""

You are the Test Agent.

Design validation for this implementation.

Return:

1. Unit tests
2. Integration tests
3. Assertions
4. Expected results
5. Edge cases
6. What requires actual execution or hardware

IMPORTANT:

Generate tests only.

Do NOT claim that tests actually ran.

USER TASK:

{request.task_description}

PLAN:

{plan}

IMPLEMENTATION:

{implementation}

"""


            parallel_tasks.append(

                run_agent(

                    agent_name="tester",

                    prompt=test_prompt,

                    provider=provider
                )
            )


        if security_enabled:

            increment_metric(
                "security_scans_completed"
            )


            security_prompt = f"""

You are the Security & Safety Agent.

Audit the proposed implementation.

Check:

- Software security
- Unsafe assumptions
- Input validation
- Authentication/authorization
- Secret exposure
- Dependency risks
- Engineering safety
- Electrical/current limits
- Thermal concerns
- Hardware risks

IMPORTANT:

This is a model-based review.

It is NOT a live security scan.

Do not claim that tools, scanners,
networks, or hardware were actually scanned.

USER TASK:

{request.task_description}

IMPLEMENTATION:

{implementation}

"""


            parallel_tasks.append(

                run_agent(

                    agent_name="security",

                    prompt=security_prompt,

                    provider=provider
                )
            )


        if parallel_tasks:

            parallel_results = (
                await asyncio.gather(
                    *parallel_tasks
                )
            )

        else:

            parallel_results = []


        tests_result = None
        security_result = None


        for item in parallel_results:

            if item["agent"] == "tester":

                tests_result = item


            elif item["agent"] == "security":

                security_result = item


        # ====================================================
        # 4. DEBUGGER
        # ====================================================

        debugger_result = None


        if request.include_debugger:

            increment_metric(
                "debugger_runs"
            )


            debugger_prompt = f"""

You are the Debugger Agent.

Analyze the implementation for likely failures.

IMPORTANT:

There is no real runtime stack trace unless one
was explicitly supplied.

Do not invent stack traces.

Perform static/reasoning-based debugging.

Return:

1. Likely bugs
2. Root causes
3. Evidence from the supplied implementation
4. Severity
5. Recommended fixes
6. What must be tested to confirm the diagnosis

USER TASK:

{request.task_description}

PLAN:

{plan}

IMPLEMENTATION:

{implementation}

TEST RESULTS:

{tests_result}

SECURITY REVIEW:

{security_result}

"""

            debugger_result = await run_agent(

                agent_name="debugger",

                prompt=debugger_prompt,

                provider=provider
            )


        # ====================================================
        # 5. VERIFIER
        # ====================================================

        increment_metric(
            "verifications_completed"
        )

        verifier_prompt = f"""

You are the Verifier Agent.

Review the complete workflow for consistency.

Check whether:

- The implementation follows the plan
- Tests match the implementation
- Security findings are relevant
- Debugging conclusions are supported
- Assumptions are clearly identified
- Important requirements are missing

Do not claim that execution or testing occurred.

USER TASK:

{request.task_description}

PLAN:

{plan}

IMPLEMENTATION:

{implementation}

TESTS:

{tests_result}

SECURITY:

{security_result}

DEBUGGER:

{debugger_result}

Return:

1. Verification status
2. Confirmed consistencies
3. Inconsistencies
4. Missing validation
5. Recommended next actions

"""

        verifier_result = await run_agent(
            agent_name="verifier",
            prompt=verifier_prompt,
            provider=provider
        )

        workflow_success = (
            verifier_result["status"] == "success"
        )

        background_tasks.add_task(
            log_telemetry,
            start_time,
            workflow_success
        )

        return {
            "status": "success" if workflow_success else "partial",

            "execution": {
                "status": "not_executed",
                "message": (
                    "No project code was executed. "
                    "Milestone 1 performs planning, "
                    "implementation generation, testing design, "
                    "security reasoning, debugging analysis, "
                    "and verification only."
                )
            },

            "plan": planner_result,

            "implementation": executor_result,

            "tests": tests_result,

            "security": security_result,

            "debugger": debugger_result,

            "verifier": verifier_result,

            "agents": {
                "planner": planner_result["status"],
                "executor": executor_result["status"],
                "tester": (
                    tests_result["status"]
                    if tests_result
                    else "disabled"
                ),
                "security": (
                    security_result["status"]
                    if security_result
                    else "disabled"
                ),
                "debugger": (
                    debugger_result["status"]
                    if debugger_result
                    else "disabled"
                ),
                "verifier": verifier_result["status"]
            }
        }

    except Exception as exc:

        background_tasks.add_task(
            log_telemetry,
            start_time,
            False
        )

        logger.exception(
            "Multi-agent workflow failed"
        )

        raise HTTPException(
            status_code=502,
            detail={
                "code": "MULTI_AGENT_WORKFLOW_ERROR",
                "message": str(exc)
            }
        )


# ============================================================
# CODE REVIEW
# ============================================================

@app.post("/api/code/review")
async def code_review_endpoint(
    request: CodeReviewRequest,
    background_tasks: BackgroundTasks
):

    start_time = time.time()

    review_prompt = f"""

You are Engineer AI Code Review Agent.

Review the supplied code.

Language:

{request.language}

Code:

{request.code_snippet}

Return:

1. Summary
2. Bugs
3. Reliability issues
4. Security issues
5. Performance concerns
6. Maintainability issues
7. Recommended fixes
8. Improved code where useful

IMPORTANT:

Do not claim that the code was executed.

Do not claim that tests passed.

Do not invent runtime errors.

"""

    try:

        review_result = await run_agent(
            agent_name="executor",
            prompt=review_prompt,
            provider=(
                request.provider
                or DEFAULT_PROVIDER
            )
        )

        success = (
            review_result["status"]
            == "success"
        )

        background_tasks.add_task(
            log_telemetry,
            start_time,
            success
        )

        return {
            "status": (
                "success"
                if success
                else "error"
            ),
            "review": review_result,
            "executed": False
        }

    except Exception as exc:

        background_tasks.add_task(
            log_telemetry,
            start_time,
            False
        )

        raise HTTPException(
            status_code=502,
            detail={
                "code": "CODE_REVIEW_ERROR",
                "message": str(exc)
            }
        )


# ============================================================
# ANALYTICS
# ============================================================

@app.get("/api/ops/analytics")
async def analytics_endpoint():

    with analytics_lock:

        data = dict(
            analytics_data
        )

        data["agent_runs"] = dict(
            analytics_data["agent_runs"]
        )

    total_requests = data[
        "total_requests"
    ]

    if total_requests > 0:

        average_latency = (
            data[
                "total_latency_seconds"
            ]
            / total_requests
        )

        success_rate = (
            data[
                "successful_responses"
            ]
            / total_requests
        ) * 100

    else:

        average_latency = 0.0

        success_rate = 0.0

    data["average_latency_seconds"] = round(
        average_latency,
        3
    )

    data["success_rate_percent"] = round(
        success_rate,
        2
    )

    return data
# ============================================================
# SERVER STARTUP
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
        reload=False
    )