import os
import time
import asyncio
import logging
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from google import genai
from google.genai import types


# ============================================================
# ENGINEER AI - BACKEND SERVER
# ============================================================

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("EngineerAI")


APP_TITLE = os.getenv("APP_TITLE", "Engineer AI Engine")
DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
DEFAULT_PROVIDER = os.getenv("DEFAULT_PROVIDER", "gemini")

MAX_RETRIES = int(os.getenv("MAX_RETRIES", "2"))
MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", "2048"))


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
# FASTAPI
# ============================================================

app = FastAPI(
    title=APP_TITLE,
    version="1.0.0",
)


# ============================================================
# CORS
# ============================================================

cors_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "*").split(",")
    if origin.strip()
]

allow_all_origins = "*" in cors_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if allow_all_origins else cors_origins,
    allow_credentials=False if allow_all_origins else True,
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
        max_length=20000,
    )

    provider: Optional[str] = DEFAULT_PROVIDER


class MultiAgentRequest(BaseModel):
    # Current frontend uses "task"
    task: Optional[str] = Field(
        default=None,
        max_length=20000,
    )

    # Older frontend/API uses "task_description"
    task_description: Optional[str] = Field(
        default=None,
        max_length=20000,
    )

    include_tests: bool = True

    # Current frontend compatibility
    include_security: Optional[bool] = None

    # Older frontend compatibility
    include_security_scan: Optional[bool] = None

    include_debugger: bool = True

    project_context: Optional[str] = Field(
        default=None,
        max_length=30000,
    )


class CodeReviewRequest(BaseModel):
    code_snippet: str = Field(
        ...,
        min_length=1,
        max_length=50000,
    )

    language: str = Field(
        ...,
        min_length=1,
        max_length=50,
    )

    project_context: Optional[str] = Field(
        default=None,
        max_length=20000,
    )


# ============================================================
# ENGINEER AI SYSTEM INSTRUCTION
# ============================================================

SYSTEM_INSTRUCTION = """
You are Engineer AI, an advanced engineering assistant.

Your job is to help users understand, design, calculate, troubleshoot,
review, test, document, and implement engineering and software systems.

CORE RULES

1. Answer the user's exact request.

2. Be technically accurate and practical.

3. Explain important reasoning clearly without pretending certainty.

4. Never invent specifications, measurements, test results, sources,
   execution results, or benchmarks.

5. If information is missing, clearly state assumptions.

6. For calculations, show:
   - Formula
   - Known values
   - Calculation
   - Final result
   - Units

7. Check calculations before presenting the final answer.

8. Clearly distinguish confirmed facts from assumptions.

9. When multiple engineering solutions exist, explain important
   trade-offs.

10. Never claim code was executed, tested, compiled, benchmarked,
    or verified unless the system actually performed that operation.

11. Treat security and safety seriously.

12. For code, prefer complete and practical examples when appropriate.

13. Keep answers structured and easy to understand.


DIAGRAM RULES

When generating Mermaid diagrams:

- Put Mermaid inside a fenced ```mermaid code block.
- Use simple graph TD or graph LR syntax.
- Use clean node IDs without spaces or special characters.
- Put human-readable labels in double quotes.
- Avoid unnecessarily complicated Mermaid syntax.


Example:

```mermaid
graph TD
    A["Power Source"] --> B["Controller"]
    B --> C["Motor"]
    C --> D["Ground"]