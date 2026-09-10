import os
import time
import asyncio
import sqlite3
import logging
from datetime import datetime, timezone
from typing import Optional, Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from google import genai
from google.genai import types


# ============================================================
# ENGINEER AI BACKEND
# PART 1 — FOUNDATION + CONFIGURATION
# ============================================================

APP_TITLE = "Engineer AI"
APP_VERSION = "3.0.0"

DEFAULT_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-2.5-flash"
)

PORT = int(
    os.getenv("PORT", "8000")
)

MAX_PROMPT_CHARS = 24000
MAX_CODE_CHARS = 30000


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("engineer-ai")


# ============================================================
# FASTAPI APP
# ============================================================

app = FastAPI(
    title=APP_TITLE,
    version=APP_VERSION,
    description="Engineering AI backend"
)


# ============================================================
# CORS
# ============================================================

cors_origins = os.getenv(
    "CORS_ORIGINS",
    "*"
)

if cors_origins == "*":
    allowed_origins = ["*"]
    allow_credentials = False
else:
    allowed_origins = [
        item.strip()
        for item in cors_origins.split(",")
        if item.strip()
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
# DATABASE
# ============================================================

DB_PATH = os.getenv(
    "ENGINEER_AI_DB",
    "engineer_ai.db"
)


def get_db():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_database():

    connection = get_db()

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            language TEXT NOT NULL,
            progress INTEGER DEFAULT 0,
            description TEXT DEFAULT '',
            code TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )

    connection.commit()
    connection.close()


init_database()


# ============================================================
# ANALYTICS
# ============================================================

analytics = {
    "total_requests": 0,
    "successful_requests": 0,
    "failed_requests": 0,
    "total_latency": 0.0,

    "chat_requests": 0,
    "agent_requests": 0,
    "review_requests": 0,
    "debug_requests": 0,
    "security_requests": 0,
    "performance_requests": 0,
    "project_requests": 0,
}


# ============================================================
# REQUEST MODELS
# ============================================================

class ChatRequest(BaseModel):

    prompt: str = Field(
        ...,
        min_length=1,
        max_length=MAX_PROMPT_CHARS
    )

    provider: Optional[str] = "gemini"


class CodeReviewRequest(BaseModel):

    code: Optional[str] = Field(
        default=None,
        max_length=MAX_CODE_CHARS
    )

    code_snippet: Optional[str] = Field(
        default=None,
        max_length=MAX_CODE_CHARS
    )

    language: str = Field(
        default="text",
        max_length=50
    )

    file_name: Optional[str] = Field(
        default="code",
        max_length=200
    )

    mode: Optional[str] = Field(
        default="review",
        max_length=50
    )

    def get_code(self):

        code = (
            self.code_snippet
            or self.code
            or ""
        )

        if not code.strip():
            raise ValueError(
                "Code is required."
            )

        return code[:MAX_CODE_CHARS]


class AgentRequest(BaseModel):

    task: Optional[str] = Field(
        default=None,
        max_length=MAX_PROMPT_CHARS
    )

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

    def get_task(self):

        task = (
            self.task
            or self.task_description
            or ""
        )

        if not task.strip():
            raise ValueError(
                "Task is required."
            )

        return task[:MAX_PROMPT_CHARS]

    def security_enabled(self):

        if self.include_security is not None:
            return self.include_security

        if self.include_security_scan is not None:
            return self.include_security_scan

        return True


class ExplainRequest(BaseModel):

    prompt: str = Field(
        ...,
        min_length=1,
        max_length=MAX_PROMPT_CHARS
    )

    code: Optional[str] = Field(
        default="",
        max_length=MAX_CODE_CHARS
    )

    language: Optional[str] = "text"

    action: Optional[str] = "explain"


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def record_request(
    start_time: float,
    success: bool
):

    elapsed = (
        time.perf_counter()
        - start_time
    )

    analytics["total_requests"] += 1

    analytics["total_latency"] += elapsed

    if success:
        analytics[
            "successful_requests"
        ] += 1
    else:
        analytics[
            "failed_requests"
        ] += 1


def get_gemini_client():

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


SYSTEM_INSTRUCTION = """
You are Engineer AI.

You are an engineering-focused AI assistant.

Be accurate, practical and clear.

For calculations:
- show the formula
- show the values
- calculate the result
- include units

For programming:
- provide useful code
- explain important changes
- do not claim code was executed unless it actually was

For engineering:
- consider safety, reliability and practical constraints when relevant

For security:
- provide defensive and educational analysis

Never pretend that a physical device, server or program was tested
when it was not actually tested.
"""


def generate_ai_response(
    prompt: str,
    max_tokens: int = 1500
):

    client = get_gemini_client()

    response = client.models.generate_content(

        model=DEFAULT_MODEL,

        contents=prompt,

        config=types.GenerateContentConfig(

            system_instruction=
                SYSTEM_INSTRUCTION,

            temperature=0.2,

            max_output_tokens=max_tokens
        )
    )

    text = getattr(
        response,
        "text",
        None
    )

    if not text:

        raise RuntimeError(
            "AI returned an empty response."
        )

    return text.strip()


async def ask_ai(
    prompt: str,
    max_tokens: int = 1500
):

    return await asyncio.to_thread(
        generate_ai_response,
        prompt,
        max_tokens
    )


# ============================================================
# BASIC ROUTES
# ============================================================

@app.get("/")
async def home():

    if os.path.exists(
        "index.html"
    ):

        return FileResponse(
            "index.html"
        )

    return {
        "name": APP_TITLE,
        "status": "online",
        "message": "index.html not found"
    }


@app.get("/api/health")
async def health():

    return {
        "status": "healthy",
        "service": APP_TITLE,
        "version": APP_VERSION,
        "model": DEFAULT_MODEL,
        "gemini_configured":
            bool(
                os.getenv(
                    "GEMINI_API_KEY"
                )
            )
    }


@app.get("/api/status")
async def status():

    return {
        "status": "operational",
        "service": APP_TITLE,
        "version": APP_VERSION,
        "model": DEFAULT_MODEL,
        "database":
            os.path.exists(DB_PATH),
        "gemini_configured":
            bool(
                os.getenv(
                    "GEMINI_API_KEY"
                )
            )
    }
# ============================================================
# PART 2 — AI CHAT + CODE REVIEW + CODING ASSISTANT
# ============================================================


# ============================================================
# 🤖 AI CHAT
# ============================================================

@app.post("/api/chat")
async def chat(request: ChatRequest):

    start_time = time.perf_counter()

    try:

        analytics["chat_requests"] += 1

        prompt = f"""
User message:

{request.prompt}

Answer as Engineer AI.

Give a useful engineering-focused answer.
If the user asks for code, provide practical code.
If the user asks for calculations, show the calculation clearly.
If the user asks for a diagram, provide a simple diagram when useful.
"""

        answer = await ask_ai(
            prompt,
            max_tokens=1600
        )

        record_request(
            start_time,
            True
        )

        return {
            "status": "success",
            "response": answer,
            "output": answer,
            "message": answer,
            "provider": "gemini",
            "model": DEFAULT_MODEL
        }

    except Exception as error:

        record_request(
            start_time,
            False
        )

        logger.exception(
            "AI Chat failed"
        )

        raise HTTPException(
            status_code=502,
            detail={
                "error": "AI Chat failed",
                "message": str(error)
            }
        )


# ============================================================
# 💻 AI CODING ASSISTANT
# ============================================================

@app.post("/api/ai/explain")
async def coding_assistant(
    request: ExplainRequest
):

    start_time = time.perf_counter()

    try:

        prompt = f"""
You are Engineer AI's coding assistant.

ACTION:
{request.action}

USER REQUEST:
{request.prompt}

PROGRAMMING LANGUAGE:
{request.language}

CODE:
```{request.language}
{request.code}
Perform the requested coding task.

Important:
- Explain what you found.
- Give corrected or improved code when appropriate.
- Be specific.
- Do not claim the code was executed.
"""

        answer = await ask_ai(
            prompt,
            max_tokens=1800
        )

        record_request(
            start_time,
            True
        )

        return {
            "status": "success",
            "response": answer,
            "output": answer,
            "action": request.action
        }

    except Exception as error:

        record_request(
            start_time,
            False
        )

        logger.exception(
            "Coding assistant failed"
        )

        raise HTTPException(
            status_code=502,
            detail={
                "error": "Coding assistant failed",
                "message": str(error)
            }
        )


# ============================================================
# 🔍 CODE REVIEW
# ============================================================

@app.post("/api/code/review")
async def code_review(
    request: CodeReviewRequest
):

    start_time = time.perf_counter()

    try:

        analytics["review_requests"] += 1

        code = request.get_code()

        mode = (
            request.mode or "review"
        ).lower()

        prompt = f"""
You are Engineer AI's expert code reviewer.

LANGUAGE:
{request.language}

FILE:
{request.file_name}

REVIEW MODE:
{mode}

Analyze this code carefully:

```{request.language}
{code}
Return a structured review with:

1. Bugs
2. Security issues
3. Performance issues
4. Code quality and maintainability
5. Recommended fixes
6. Overall assessment

Rules:
- Perform static analysis.
- Do not claim that you executed the code.
- Do not claim that a real security scanner was run.
- Clearly distinguish likely problems from confirmed problems.
"""

        answer = await ask_ai(
            prompt,
            max_tokens=1800
        )

        record_request(
            start_time,
            True
        )

        return {
            "status": "success",
            "response": answer,
            "output": answer,
            "review": {
                "output": answer,
                "mode": mode,
                "executed": False,
                "status": "static_review_complete"
            },
            "executed": False
        }

    except ValueError as error:

        record_request(
            start_time,
            False
        )

        raise HTTPException(
            status_code=400,
            detail={
                "error": "Invalid code",
                "message": str(error)
            }
        )

    except Exception as error:

        record_request(
            start_time,
            False
        )

        logger.exception(
            "Code review failed"
        )

        raise HTTPException(
            status_code=502,
            detail={
                "error": "Code review failed",
                "message": str(error)
            }
        )


# ============================================================
# 🛡️ SECURITY SCAN
# ============================================================

@app.post("/api/security/scan")
async def security_scan(
    request: CodeReviewRequest
):

    request.mode = "security"

    result = await code_review(
        request
    )

    return {
        **result,
        "scan_type":
            "AI static security analysis"
    }


# ============================================================
# 🛡️ SECURITY REVIEW
# ============================================================

@app.post("/api/security/review")
async def security_review(
    request: CodeReviewRequest
):

    request.mode = "security"

    result = await code_review(
        request
    )

    return {
        **result,
        "scan_type":
            "AI static security review"
    }


# ============================================================
# ⚡ PERFORMANCE ANALYSIS
# ============================================================

@app.post("/api/performance/analyze")
async def performance_analyze(
    request: CodeReviewRequest
):

    request.mode = "performance"

    result = await code_review(
        request
    )

    return {
        **result,
        "analysis_type":
            "AI static performance analysis"
    }


# ============================================================
# ⚡ PERFORMANCE REVIEW
# ============================================================

@app.post("/api/performance/review")
async def performance_review(
    request: CodeReviewRequest
):

    request.mode = "performance"

    result = await code_review(
        request
    )

    return {
        **result,
        "analysis_type":
            "AI static performance review"
    }


# ============================================================
# 🐞 DEBUGGER
# ============================================================

@app.post("/api/debug")
async def debugger(
    request: ExplainRequest
):

    start_time = time.perf_counter()

    analytics["debug_requests"] += 1

    code = request.code or ""

    prompt = f"""
You are Engineer AI's debugging specialist.

PROGRAMMING LANGUAGE:
{request.language}

ERROR:
{request.prompt}

CODE:
```{request.language}
{code}

with:

```python
Find the likely cause of the error.

Return:

1. Problem
2. Why it happens
3. Fix
4. Corrected code if useful
5. Tests that should be performed

Do not claim that you executed the program.
"""

    try:
        answer = await ask_ai(
            prompt,
            max_tokens=1800
        )

        record_request(
            start_time,
            True
        )

        return {
            "status": "success",
            "response": answer,
            "output": answer,
            "debug": {
                "executed": False,
                "analysis": answer
            }
        }

    except Exception as error:
        record_request(
            start_time,
            False
        )

        logger.exception(
            "Debugger failed"
        )

        raise HTTPException(
            status_code=502,
            detail={
                "error": "Debugger failed",
                "message": str(error)
            }
        )


# ============================================================
# 🧪 TEST GENERATOR
# ============================================================

@app.post("/api/test/run")
async def test_code(
    request: CodeReviewRequest
):

    try:
        code = request.get_code()

        prompt = f"""
You are Engineer AI's testing specialist.

LANGUAGE:
{request.language}

CODE:
```{request.language}
{code}
