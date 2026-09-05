import os
import time
import asyncio
import logging
from typing import Any, Dict, Optional

from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from google import genai
from google.genai import types


# ============================================================
# ENGINEER AI — BACKEND ENGINE
# ============================================================

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("EngineerAI")


# ============================================================
# CONFIGURATION
# ============================================================

APP_TITLE = os.getenv("APP_TITLE", "Engineer AI Engine")
DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
DEFAULT_PROVIDER = os.getenv("DEFAULT_PROVIDER", "gemini")

MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", "2048"))
TEMPERATURE = float(os.getenv("MODEL_TEMPERATURE", "0.2"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "2"))

CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*")

if CORS_ORIGINS.strip() == "*":
    allowed_origins = ["*"]
    allow_credentials = False
else:
    allowed_origins = [
        origin.strip()
        for origin in CORS_ORIGINS.split(",")
        if origin.strip()
    ]
    allow_credentials = True


# ============================================================
# ANALYTICS
# ============================================================

analytics_data: Dict[str, Any] = {
    "total_requests": 0,
    "successful_responses": 0,
    "failed_requests": 0,
    "total_latency_seconds": 0.0,

    "agent_tasks_executed": 0,

    "planner_runs": 0,
    "executor_runs": 0,
    "tester_runs": 0,
    "security_runs": 0,
    "debugger_runs": 0,
    "verifier_runs": 0,

    "tests_generated": 0,
    "security_scans_completed": 0,
    "debugger_runs_completed": 0,
    "verification_runs_completed": 0,
}


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title=APP_TITLE,
    version="2.0.0",
    description="Engineer AI multi-agent engineering backend",
)

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
    prompt: str = Field(..., min_length=1, max_length=30000)
    provider: Optional[str] = DEFAULT_PROVIDER
    project_context: Optional[str] = Field(
        default=None,
        max_length=50000
    )


class MultiAgentRequest(BaseModel):
    # "task" is accepted for compatibility with the current UI.
    task: Optional[str] = Field(
        default=None,
        max_length=30000
    )

    # Older clients can still use task_description.
    task_description: Optional[str] = Field(
        default=None,
        max_length=30000
    )

    include_tests: bool = True

    # Current UI uses include_security.
    include_security: Optional[bool] = None

    # Older clients use include_security_scan.
    include_security_scan: Optional[bool] = None

    include_debugger: bool = True
    include_verifier: bool = True

    project_context: Optional[str] = Field(
        default=None,
        max_length=50000
    )


class CodeReviewRequest(BaseModel):
    code_snippet: str = Field(
        ...,
        min_length=1,
        max_length=60000
    )

    language: str = Field(
        ...,
        min_length=1,
        max_length=100
    )

    project_context: Optional[str] = Field(
        default=None,
        max_length=50000
    )


# ============================================================
# ENGINEER AI SYSTEM INSTRUCTION
# ============================================================

SYSTEM_INSTRUCTION = """
You are Engineer AI, an advanced engineering and software engineering assistant.

Your job is to help users understand, design, calculate, troubleshoot,
review, test, document, and implement engineering and software systems.

CORE RULES:

1. Answer the user's exact request.
2. Be technically accurate and practical.
3. Clearly explain important reasoning and trade-offs.
4. Never invent specifications, measurements, test results, sources, or
   execution results.
5. If information is missing, state assumptions clearly.
6. For calculations, provide:
   - formula
   - known values
   - calculation
   - result
   - units
7. Check calculations before presenting them.
8. Distinguish confirmed facts from assumptions.
9. Never claim that code was executed unless an actual execution system
   executed it.
10. Never claim that tests passed unless they were actually run.
11. Never claim that a security scan was completed unless an actual scan
    was performed.
12. When reviewing code, identify bugs, risks, performance issues,
    maintainability problems, and concrete improvements.
13. Prefer simple, robust solutions over unnecessary complexity.

CODE GENERATION:

When producing code:
- Provide complete, usable code when appropriate.
- Preserve the requested programming language.
- Explain important configuration requirements.
- Do not pretend that generated code has been executed.

ENGINEERING SAFETY:

For engineering systems involving electricity, heat, machinery,
chemicals, structures, vehicles, or other physical systems:
- identify important assumptions
- mention relevant safety considerations
- avoid presenting uncertain values as guaranteed safe
- recommend appropriate real-world validation

MERMAID DIAGRAMS:

When generating Mermaid diagrams:
- Always use a fenced ```mermaid code block.
- Use simple node IDs.
- Do not put spaces or special characters in raw node IDs.
- Put human-readable labels inside quotes.
- Prefer graph TD or graph LR.
- Keep Mermaid syntax simple and standard.

Example:

```mermaid
graph TD
    A["Power Source"] --> B["Controller"]
    B --> C["Load"]
    C --> D["Ground"]