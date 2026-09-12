import os
import time
import asyncio
import sqlite3
import logging
from datetime import datetime, timezone
from typing import Optional, Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from google import genai
from google.genai import types

from openai import OpenAI as OpenAICompatibleClient


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

# ============================================================
# MULTI-PROVIDER AI CONFIG
# Gemini uses Google's SDK. DeepSeek and Kimi (Moonshot) both
# expose OpenAI-compatible chat completion APIs, so they share
# one client implementation (see generate_openai_compatible_response).
# ============================================================

PROVIDER_CONFIGS = {
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "api_key_env": "DEEPSEEK_API_KEY",
        "model_env": "DEEPSEEK_MODEL",
        "default_model": "deepseek-v4-flash",
    },
    "kimi": {
        "label": "Kimi (Moonshot AI)",
        "base_url": "https://api.moonshot.ai/v1",
        "api_key_env": "KIMI_API_KEY",
        "model_env": "KIMI_MODEL",
        "default_model": "kimi-k2.6",
    },
}

SUPPORTED_PROVIDERS = ["gemini", "deepseek", "kimi"]

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

    provider: Optional[str] = "gemini"

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

    provider: Optional[str] = "gemini"

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

    provider: Optional[str] = "gemini"
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


def generate_openai_compatible_response(
    provider: str,
    prompt: str,
    max_tokens: int = 1500
):

    config = PROVIDER_CONFIGS[provider]

    api_key = os.getenv(
        config["api_key_env"]
    )

    if not api_key:

        raise RuntimeError(
            f"{config['api_key_env']} is not configured."
        )

    client = OpenAICompatibleClient(
        api_key=api_key,
        base_url=config["base_url"]
    )

    model = os.getenv(
        config["model_env"],
        config["default_model"]
    )

    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": SYSTEM_INSTRUCTION
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        max_tokens=max_tokens,
        temperature=0.2
    )

    text = response.choices[0].message.content

    if not text:

        raise RuntimeError(
            f"{config['label']} returned an empty response."
        )

    return text.strip()


def normalize_provider(provider: Optional[str]) -> str:

    provider = (provider or "gemini").strip().lower()

    if provider not in SUPPORTED_PROVIDERS:

        raise RuntimeError(
            f"Unknown AI provider: {provider}. "
            f"Supported providers: {', '.join(SUPPORTED_PROVIDERS)}."
        )

    return provider


async def ask_ai(
    prompt: str,
    max_tokens: int = 1500,
    provider: Optional[str] = "gemini"
):

    provider = normalize_provider(provider)

    if provider == "gemini":

        return await asyncio.to_thread(
            generate_ai_response,
            prompt,
            max_tokens
        )

    return await asyncio.to_thread(
        generate_openai_compatible_response,
        provider,
        prompt,
        max_tokens
    )


# ============================================================
# BASIC ROUTES
# ============================================================

def provider_status():

    status = {
        "gemini": bool(os.getenv("GEMINI_API_KEY"))
    }

    for key, config in PROVIDER_CONFIGS.items():
        status[key] = bool(os.getenv(config["api_key_env"]))

    return status


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
            ),
        "providers": provider_status()
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
            ),
        "providers": provider_status()
    }


@app.get("/api/providers")
async def list_providers():
    """
    Reports which AI providers have an API key configured on
    the server, without ever exposing the keys themselves.
    The frontend's provider picker uses this to grey out
    providers that aren't set up yet.
    """

    configured = provider_status()

    providers = [
        {
            "id": "gemini",
            "label": "Gemini",
            "model": DEFAULT_MODEL,
            "configured": configured["gemini"]
        }
    ]

    for key, config in PROVIDER_CONFIGS.items():
        providers.append({
            "id": key,
            "label": config["label"],
            "model": os.getenv(
                config["model_env"],
                config["default_model"]
            ),
            "configured": configured[key]
        })

    return {
        "status": "success",
        "providers": providers
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

        provider = normalize_provider(
            request.provider
        )

        answer = await ask_ai(
            prompt,
            max_tokens=1600,
            provider=provider
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
            "provider": provider,
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
            max_tokens=1800,
            provider=request.provider
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
            max_tokens=1800,
            provider=request.provider
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

    analytics["security_requests"] += 1

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

    analytics["security_requests"] += 1

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

    analytics["performance_requests"] += 1

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

    analytics["performance_requests"] += 1

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
```

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
            max_tokens=1800,
            provider=request.provider
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
```

Create a useful test plan.

Include:

- Normal cases
- Edge cases
- Invalid input cases
- Expected results
- Example tests when possible

Do not claim that the tests were actually executed.
"""

        answer = await ask_ai(
            prompt,
            max_tokens=1600,
            provider=request.provider
        )

        return {
            "status": "success",
            "response": answer,
            "output": answer,
            "executed": False,
            "test_status": "generated"
        }

    except ValueError as error:

        raise HTTPException(
            status_code=400,
            detail={
                "error": "Invalid code",
                "message": str(error)
            }
        )

    except Exception as error:

        logger.exception(
            "Test generation failed"
        )

        raise HTTPException(
            status_code=502,
            detail={
                "error": "Test generation failed",
                "message": str(error)
            }
        )
        
            # ============================================================
# 🤖 MULTI-AGENT ENGINE
# ============================================================

@app.post("/api/agent/execute")
async def execute_agent(
    request: AgentRequest
):

    start_time = time.perf_counter()

    try:

        task = request.get_task()

        analytics["agent_requests"] += 1

        security_enabled = request.security_enabled()

        context = request.project_context or "No additional project context provided."

        prompt = f"""
You are Engineer AI's multi-agent engineering system.

USER TASK:
{task}

PROJECT CONTEXT:
{context}

Build a structured engineering solution.

Use these specialist roles internally:

1. PLANNER
Break the task into clear engineering steps.

2. ARCHITECT
Choose a practical architecture, components, technologies,
interfaces, and data flow.

3. CODER
Create implementation code when appropriate.

4. DEBUGGER
Identify likely bugs, failure points, and fixes.

5. TESTER
Design tests for normal, edge, and invalid cases.

6. SECURITY ENGINEER
Identify security risks and safer implementation practices.

7. PERFORMANCE ENGINEER
Identify possible performance bottlenecks and improvements.

8. VERIFIER
Check whether the proposed solution is internally consistent.

SETTINGS:

Include tests: {request.include_tests}

Include security analysis: {security_enabled}

Include debugger analysis: {request.include_debugger}

Include verifier analysis: {request.include_verifier}

Return the result using this structure:

# Engineering Plan

# Architecture

# Implementation

# Debug Analysis

# Security Analysis

# Performance Analysis

# Testing Plan

# Verification

# Final Recommendation

Important rules:

- Do not claim that code was executed unless execution is actually
  performed by a tool.
- Do not claim that hardware was physically tested.
- Clearly identify assumptions.
- Prefer practical and maintainable solutions.
- If code is requested, provide complete useful code where possible.
"""

        answer = await ask_ai(
            prompt,
            max_tokens=5000,
            provider=request.provider
        )

        record_request(
            start_time,
            True
        )

        return {
            "status": "success",
            "response": answer,
            "output": answer,
            "agent": "multi-agent-engineer",
            "task": task,
            "agents": [
                "planner",
                "architect",
                "coder",
                "debugger",
                "tester",
                "security",
                "performance",
                "verifier"
            ]
        }

    except ValueError as error:

        record_request(
            start_time,
            False
        )

        raise HTTPException(
            status_code=400,
            detail={
                "error": "Invalid agent request",
                "message": str(error)
            }
        )

    except Exception as error:

        record_request(
            start_time,
            False
        )

        logger.exception(
            "Agent execution failed"
        )

        raise HTTPException(
            status_code=502,
            detail={
                "error": "Agent execution failed",
                "message": str(error)
            }
        )


# ============================================================
# 📁 PROJECT MANAGEMENT
# ============================================================

class ProjectCreateRequest(BaseModel):

    name: str = Field(
        ...,
        min_length=1,
        max_length=120
    )

    language: str = Field(
        default="text",
        max_length=50
    )

    description: str = Field(
        default="",
        max_length=5000
    )

    code: str = Field(
        default="",
        max_length=MAX_CODE_CHARS
    )


class ProjectUpdateRequest(BaseModel):

    name: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=120
    )

    language: Optional[str] = Field(
        default=None,
        max_length=50
    )

    progress: Optional[int] = Field(
        default=None,
        ge=0,
        le=100
    )

    description: Optional[str] = Field(
        default=None,
        max_length=5000
    )

    code: Optional[str] = Field(
        default=None,
        max_length=MAX_CODE_CHARS
    )


def make_project_id():

    return (
        datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        + "-"
        + str(int(time.time() * 1000))[-6:]
    )


@app.get("/api/projects")
async def list_projects():

    start_time = time.perf_counter()

    try:

        analytics["project_requests"] += 1

        connection = get_db()

        rows = connection.execute(
            """
            SELECT
                id,
                name,
                language,
                progress,
                description,
                code,
                created_at,
                updated_at
            FROM projects
            ORDER BY updated_at DESC
            """
        ).fetchall()

        connection.close()

        projects = [
            dict(row)
            for row in rows
        ]

        record_request(
            start_time,
            True
        )

        return {
            "status": "success",
            "projects": projects,
            "count": len(projects)
        }

    except Exception as error:

        record_request(
            start_time,
            False
        )

        logger.exception(
            "Project listing failed"
        )

        raise HTTPException(
            status_code=500,
            detail={
                "error": "Project listing failed",
                "message": str(error)
            }
        )


@app.post("/api/projects")
async def create_project(
    request: ProjectCreateRequest
):

    start_time = time.perf_counter()

    project_id = make_project_id()

    now = datetime.now(
        timezone.utc
    ).isoformat()

    try:

        analytics["project_requests"] += 1

        connection = get_db()

        connection.execute(
            """
            INSERT INTO projects (
                id,
                name,
                language,
                progress,
                description,
                code,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                request.name,
                request.language,
                0,
                request.description,
                request.code,
                now,
                now
            )
        )

        connection.commit()
        connection.close()

        record_request(
            start_time,
            True
        )

        return {
            "status": "success",
            "project": {
                "id": project_id,
                "name": request.name,
                "language": request.language,
                "progress": 0,
                "description": request.description,
                "code": request.code,
                "created_at": now,
                "updated_at": now
            }
        }

    except Exception as error:

        record_request(
            start_time,
            False
        )

        logger.exception(
            "Project creation failed"
        )

        raise HTTPException(
            status_code=500,
            detail={
                "error": "Project creation failed",
                "message": str(error)
            }
        )


@app.get("/api/projects/{project_id}")
async def get_project(
    project_id: str
):

    start_time = time.perf_counter()

    try:

        analytics["project_requests"] += 1

        connection = get_db()

        row = connection.execute(
            """
            SELECT
                id,
                name,
                language,
                progress,
                description,
                code,
                created_at,
                updated_at
            FROM projects
            WHERE id = ?
            """,
            (project_id,)
        ).fetchone()

        connection.close()

        if row is None:

            raise HTTPException(
                status_code=404,
                detail={
                    "error": "Project not found"
                }
            )

        record_request(
            start_time,
            True
        )

        return {
            "status": "success",
            "project": dict(row)
        }

    except HTTPException:
        record_request(
            start_time,
            False
        )
        raise

    except Exception as error:

        record_request(
            start_time,
            False
        )

        logger.exception(
            "Project retrieval failed"
        )

        raise HTTPException(
            status_code=500,
            detail={
                "error": "Project retrieval failed",
                "message": str(error)
            }
        )


@app.put("/api/projects/{project_id}")
async def update_project(
    project_id: str,
    request: ProjectUpdateRequest
):

    start_time = time.perf_counter()

    try:

        analytics["project_requests"] += 1

        connection = get_db()

        existing = connection.execute(
            """
            SELECT *
            FROM projects
            WHERE id = ?
            """,
            (project_id,)
        ).fetchone()

        if existing is None:

            connection.close()

            raise HTTPException(
                status_code=404,
                detail={
                    "error": "Project not found"
                }
            )

        current = dict(existing)

        name = (
            request.name
            if request.name is not None
            else current["name"]
        )

        language = (
            request.language
            if request.language is not None
            else current["language"]
        )

        progress = (
            request.progress
            if request.progress is not None
            else current["progress"]
        )

        description = (
            request.description
            if request.description is not None
            else current["description"]
        )

        code = (
            request.code
            if request.code is not None
            else current["code"]
        )

        updated_at = datetime.now(
            timezone.utc
        ).isoformat()

        connection.execute(
            """
            UPDATE projects
            SET
                name = ?,
                language = ?,
                progress = ?,
                description = ?,
                code = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                name,
                language,
                progress,
                description,
                code,
                updated_at,
                project_id
            )
        )

        connection.commit()

        row = connection.execute(
            """
            SELECT *
            FROM projects
            WHERE id = ?
            """,
            (project_id,)
        ).fetchone()

        connection.close()

        record_request(
            start_time,
            True
        )

        return {
            "status": "success",
            "project": dict(row)
        }

    except HTTPException:
        record_request(
            start_time,
            False
        )
        raise

    except Exception as error:

        record_request(
            start_time,
            False
        )

        logger.exception(
            "Project update failed"
        )

        raise HTTPException(
            status_code=500,
            detail={
                "error": "Project update failed",
                "message": str(error)
            }
        )


@app.delete("/api/projects/{project_id}")
async def delete_project(
    project_id: str
):

    start_time = time.perf_counter()

    try:

        analytics["project_requests"] += 1

        connection = get_db()

        cursor = connection.execute(
            """
            DELETE FROM projects
            WHERE id = ?
            """,
            (project_id,)
        )

        connection.commit()
        connection.close()

        if cursor.rowcount == 0:

            raise HTTPException(
                status_code=404,
                detail={
                    "error": "Project not found"
                }
            )

        record_request(
            start_time,
            True
        )

        return {
            "status": "success",
            "deleted": project_id
        }

    except HTTPException:
        record_request(
            start_time,
            False
        )
        raise

    except Exception as error:

        record_request(
            start_time,
            False
        )

        logger.exception(
            "Project deletion failed"
        )

        raise HTTPException(
            status_code=500,
            detail={
                "error": "Project deletion failed",
                "message": str(error)
            }
        )

# ============================================================
# 🧠 PROJECT AI ASSISTANT
# ============================================================

class ProjectAIRequest(BaseModel):

    prompt: str = Field(
        ...,
        min_length=1,
        max_length=MAX_PROMPT_CHARS
    )

    project_id: Optional[str] = None

    language: Optional[str] = "text"

    code: Optional[str] = Field(
        default="",
        max_length=MAX_CODE_CHARS
    )

    provider: Optional[str] = "gemini"


@app.post("/api/project/ask")
async def project_ai(
    request: ProjectAIRequest
):

    start_time = time.perf_counter()

    try:

        analytics["project_requests"] += 1

        project_context = ""

        if request.project_id:

            connection = get_db()

            row = connection.execute(
                """
                SELECT *
                FROM projects
                WHERE id = ?
                """,
                (request.project_id,)
            ).fetchone()

            connection.close()

            if row:

                project = dict(row)

                project_context = f"""
PROJECT NAME:
{project["name"]}

PROJECT LANGUAGE:
{project["language"]}

PROJECT DESCRIPTION:
{project["description"]}

PROJECT PROGRESS:
{project["progress"]}%

PROJECT CODE:
{project["code"]}
"""

        prompt = f"""
You are Engineer AI's project assistant.

USER REQUEST:
{request.prompt}

LANGUAGE:
{request.language}

CURRENT CODE:
{request.code}

{project_context}

Help the user improve, understand, debug, design,
or extend the engineering project.

Return:

1. Understanding
2. Recommended approach
3. Implementation
4. Testing
5. Security considerations
6. Performance considerations

Do not claim that code was executed.
"""

        answer = await ask_ai(
            prompt,
            max_tokens=3000,
            provider=request.provider
        )

        record_request(
            start_time,
            True
        )

        return {
            "status": "success",
            "response": answer,
            "output": answer
        }

    except Exception as error:

        record_request(
            start_time,
            False
        )

        logger.exception(
            "Project AI failed"
        )

        raise HTTPException(
            status_code=502,
            detail={
                "error": "Project AI failed",
                "message": str(error)
            }
        )


class ProjectAnalyzeRequest(BaseModel):

    project_id: str

    provider: Optional[str] = "gemini"


@app.post("/api/project/analyze")
async def analyze_project(
    request: ProjectAnalyzeRequest
):
    """
    Gives an AI-generated overview of an entire saved project:
    architecture, risks, and suggested next steps.
    """

    start_time = time.perf_counter()

    try:

        analytics["project_requests"] += 1

        connection = get_db()

        row = connection.execute(
            """
            SELECT *
            FROM projects
            WHERE id = ?
            """,
            (request.project_id,)
        ).fetchone()

        connection.close()

        if row is None:

            raise HTTPException(
                status_code=404,
                detail={
                    "error": "Project not found"
                }
            )

        project = dict(row)

        prompt = f"""
You are Engineer AI's project assistant, doing a full
project review.

PROJECT NAME:
{project["name"]}

PROJECT LANGUAGE:
{project["language"]}

PROJECT DESCRIPTION:
{project["description"]}

PROJECT PROGRESS:
{project["progress"]}%

PROJECT CODE:
{project["code"] or "No code has been saved for this project yet."}

Give a structured analysis covering:

1. What this project currently does
2. Architecture / structure observations
3. Potential bugs or risks
4. Missing pieces for the stated progress level
5. Recommended next steps

Do not claim that the code was executed.
"""

        answer = await ask_ai(
            prompt,
            max_tokens=2500,
            provider=request.provider
        )

        record_request(
            start_time,
            True
        )

        return {
            "status": "success",
            "response": answer,
            "output": answer
        }

    except HTTPException:
        record_request(
            start_time,
            False
        )
        raise

    except Exception as error:

        record_request(
            start_time,
            False
        )

        logger.exception(
            "Project analysis failed"
        )

        raise HTTPException(
            status_code=502,
            detail={
                "error": "Project analysis failed",
                "message": str(error)
            }
        )


@app.get("/api/project/stats")
async def project_stats(
    project_id: str
):
    """
    Lightweight, non-AI stats about a saved project: line count,
    character count, and the metadata already stored for it.
    """

    start_time = time.perf_counter()

    try:

        analytics["project_requests"] += 1

        connection = get_db()

        row = connection.execute(
            """
            SELECT *
            FROM projects
            WHERE id = ?
            """,
            (project_id,)
        ).fetchone()

        connection.close()

        if row is None:

            raise HTTPException(
                status_code=404,
                detail={
                    "error": "Project not found"
                }
            )

        project = dict(row)
        code = project.get("code") or ""

        lines = code.split("\n") if code else []
        non_empty_lines = [
            line for line in lines if line.strip()
        ]

        record_request(
            start_time,
            True
        )

        return {
            "status": "success",
            "project_id": project["id"],
            "name": project["name"],
            "language": project["language"],
            "progress": project["progress"],
            "created_at": project["created_at"],
            "updated_at": project["updated_at"],
            "stats": {
                "total_lines": len(lines),
                "non_empty_lines": len(non_empty_lines),
                "characters": len(code)
            }
        }

    except HTTPException:
        record_request(
            start_time,
            False
        )
        raise

    except Exception as error:

        record_request(
            start_time,
            False
        )

        logger.exception(
            "Project stats failed"
        )

        raise HTTPException(
            status_code=502,
            detail={
                "error": "Project stats failed",
                "message": str(error)
            }
        )


# ============================================================
# 🧪 SAFE TERMINAL
# ============================================================

class TerminalRequest(BaseModel):

    command: str = Field(
        ...,
        min_length=1,
        max_length=4000
    )

    language: Optional[str] = "text"


@app.post("/api/terminal/execute")
async def terminal_execute(
    request: TerminalRequest
):

    start_time = time.perf_counter()

    dangerous_commands = [
        "rm -rf",
        "mkfs",
        "shutdown",
        "reboot",
        "format",
        "del /f",
        "diskpart",
        ":(){",
        "fork bomb"
    ]

    command_lower = request.command.lower()

    if any(
        dangerous in command_lower
        for dangerous in dangerous_commands
    ):

        record_request(
            start_time,
            False
        )

        raise HTTPException(
            status_code=400,
            detail={
                "error": "Unsafe command blocked",
                "message": "This command is not allowed."
            }
        )

    try:

        prompt = f"""
You are Engineer AI's terminal assistant.

The user entered this command:

{request.command}

Language/environment:
{request.language}

Analyze the command safely.

Return:

1. What the command means
2. What it would normally do
3. Expected output
4. Possible errors
5. Safer alternatives if relevant

IMPORTANT:

Do not claim that the command was actually executed.
Do not provide destructive system instructions.
"""

        answer = await ask_ai(
            prompt,
            max_tokens=1400
        )

        record_request(
            start_time,
            True
        )

        return {
            "status": "success",
            "response": answer,
            "output": answer,
            "executed": False
        }

    except Exception as error:

        record_request(
            start_time,
            False
        )

        logger.exception(
            "Terminal analysis failed"
        )

        raise HTTPException(
            status_code=502,
            detail={
                "error": "Terminal analysis failed",
                "message": str(error)
            }
        )


# ============================================================
# 📊 ANALYTICS
# ============================================================

@app.get("/api/ops/analytics")
async def get_analytics():

    total = analytics["total_requests"]

    average_latency = (
        analytics["total_latency"] / total
        if total > 0
        else 0
    )

    return {
        "status": "success",
        "analytics": {
            **analytics,
            "average_latency": round(
                average_latency,
                4
            )
        }
    }


# ============================================================
# 🧹 RESET ANALYTICS
# ============================================================

@app.post("/api/ops/analytics/reset")
async def reset_analytics():

    for key in analytics:

        analytics[key] = 0.0 if (
            key == "total_latency"
        ) else 0

    return {
        "status": "success",
        "message": "Analytics reset"
    }


# ============================================================
# 🔍 API INFORMATION
# ============================================================

@app.get("/api")
async def api_information():

    return {
        "name": APP_TITLE,
        "version": APP_VERSION,
        "status": "online",
        "endpoints": [
            "/api/health",
            "/api/status",
            "/api/chat",
            "/api/ai/explain",
            "/api/code/review",
            "/api/security/scan",
            "/api/security/review",
            "/api/performance/analyze",
            "/api/performance/review",
            "/api/debug",
            "/api/test/run",
            "/api/agent/execute",
            "/api/projects",
            "/api/project/ask",
            "/api/terminal/execute",
            "/api/ops/analytics"
        ]
    }


# ============================================================
# ❌ GLOBAL ERROR HANDLER
# ============================================================

@app.exception_handler(Exception)
async def global_exception_handler(
    request,
    exc
):

    logger.exception(
        "Unhandled server error"
    )

    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "error": "Internal server error",
            "message": str(exc)
        }
    )


# ============================================================
# 🚀 SERVER START
# ============================================================

if __name__ == "__main__":

    import uvicorn

    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=PORT,
        reload=False
    )
