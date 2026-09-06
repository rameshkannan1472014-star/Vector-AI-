import os
import time
import asyncio
import logging
from typing import Dict, Any, Optional, List

from fastapi import FastAPI, BackgroundTasks
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
APP_VERSION = "2.1.0"

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
    description="Engineer AI Project Intelligence and Multi-Agent Engineering Backend"
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

    This keeps compatibility with the existing dashboard.
    """

    task_description: Optional[str] = None

    task: Optional[str] = None

    include_tests: bool = True

    include_security_scan: bool = True

    def get_task(self) -> str:
        value = self.task_description or self.task

        if not value:
            raise ValueError(
                "A task is required. Provide 'task' or 'task_description'."
            )

        return value


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
# ============================================================

SYSTEM_INSTRUCTION = """
You are Engineer AI, an advanced engineering and software engineering
assistant.

Your job is to help users understand, design, build, analyze, debug,
test, secure, optimize, and document engineering software and systems.

CORE RULES:

1. Answer the user's exact request.
2. Be technically accurate.
3. Never invent facts, measurements, specifications, test results,
   benchmark results, or sources.
4. Clearly distinguish:
   - confirmed facts
   - assumptions
   - recommendations
   - inferred conclusions
5. If information is missing, state what is missing.
6. For calculations:
   - show the formula
   - show known values
   - calculate the result
   - provide units
   - check the result
7. Explain important engineering trade-offs.
8. Do not claim code was executed unless it was actually executed.
9. Do not claim tests passed unless they were actually executed.
10. Treat security analysis as defensive engineering.
11. Never expose secrets found in project files.
12. Prefer maintainable, modular, testable designs.
13. Consider correctness, reliability, security,
    performance, testing, scalability, and maintainability.

PROJECT INTELLIGENCE:

When project files are supplied, reason about the project as a whole.

Consider:

- file structure
- modules
- imports
- APIs
- classes
- functions
- configuration
- dependencies
- data flow
- control flow
- architecture
- error handling
- testing
- security
- performance
- scalability
- maintainability

Never assume that a file exists if it was not provided.

MERMAID RULES:

When generating diagrams:

1. Always use a fenced mermaid block.
2. Use simple node IDs.
3. Do not put spaces or special characters in raw node IDs.
4. Put human-readable labels inside quotes.
5. Prefer graph TD or graph LR.
6. Keep diagrams syntactically simple.

Example:

```mermaid
graph TD
    A["User"] --> B["Engineer AI"]
    B --> C["Planner"]
    C --> D["Executor"]
    D --> E["Tester"]
    D --> F["Security"]
    E --> G["Debugger"]
    F --> G["Debugger"]