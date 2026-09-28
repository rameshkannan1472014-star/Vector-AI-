import os
import json
import ast
import math
import re
import time
import asyncio
import sqlite3
import smtplib
from email.message import EmailMessage
import logging
from datetime import datetime, timezone
from typing import Optional, Any
import base64
import binascii
import tempfile
import uuid
import threading

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
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

FEEDBACK_RECIPIENT_DEFAULT = "ramesh.kannan14.7.2014@gmail.com"

DEFAULT_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-2.5-flash"
)

PORT = int(
    os.getenv("PORT", "8000")
)

# ============================================================
# MULTI-PROVIDER AI CONFIG
# Gemini uses Google's SDK with primary/backup project keys. DeepSeek and Kimi
# (Moonshot) expose OpenAI-compatible APIs and share one client.
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

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            rating INTEGER NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    connection.commit()
    connection.close()


init_database()


# ============================================================
# ANALYTICS
# ============================================================

media_generation_jobs: dict[str, dict[str, Any]] = {}

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

    history: list[dict[str, str]] = Field(
        default_factory=list,
        max_length=80
    )

    images: list[dict[str, str]] = Field(
        default_factory=list,
        max_length=4
    )

    memory: dict[str, str] = Field(default_factory=dict)

    workspace_context: str = Field(default="", max_length=24000)


class MediaGenerationRequest(BaseModel):

    prompt: str = Field(..., min_length=3, max_length=3000)


class FeedbackRequest(BaseModel):

    category: str = Field(default="General", min_length=1, max_length=40)

    rating: int = Field(..., ge=1, le=5)

    message: str = Field(..., min_length=3, max_length=4000)


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


_gemini_key_lock = threading.Lock()
_gemini_active_key_index = 0


def get_gemini_api_keys() -> list[str]:
    """Read Gemini project keys from server environment variables only."""
    candidates = [os.getenv("GEMINI_API_KEY", "")]
    candidates.extend(os.getenv(f"GEMINI_API_KEY_{index}", "") for index in range(2, 5))
    # Optional list format for hosts that make grouped secrets easier to manage.
    candidates.extend(re.split(r"[,;\s]+", os.getenv("GEMINI_API_KEYS", "").strip()))
    keys = []
    for candidate in candidates:
        key = candidate.strip()
        if key and key not in keys:
            keys.append(key)
    return keys


def is_gemini_key_or_capacity_error(error: Exception) -> bool:
    """Only fail over for credentials, quota/rate, and transient service errors."""
    status = getattr(error, "code", None) or getattr(error, "status_code", None)
    response = getattr(error, "response", None)
    status = status or getattr(response, "status_code", None)
    try:
        if int(status) in {401, 403, 408, 429, 500, 502, 503, 504}:
            return True
    except (TypeError, ValueError):
        pass
    detail = str(error).lower()
    return any(marker in detail for marker in (
        "api_key_invalid", "api key not valid", "invalid api key",
        "permission_denied", "resource_exhausted", "quota_exceeded",
        "rate_limit_exceeded", "too many requests", "429", "503 service unavailable",
    ))


def run_with_gemini_failover(operation):
    """Run one Gemini request, advancing to a configured project key on eligible errors."""
    global _gemini_active_key_index
    keys = get_gemini_api_keys()
    if not keys:
        raise RuntimeError("No Gemini keys are configured. Set GEMINI_API_KEY on the server.")
    with _gemini_key_lock:
        start_index = _gemini_active_key_index % len(keys)
    last_error = None
    for offset in range(len(keys)):
        index = (start_index + offset) % len(keys)
        try:
            client = genai.Client(api_key=keys[index])
            result = operation(client)
            with _gemini_key_lock:
                _gemini_active_key_index = index
            if offset:
                logger.info("Gemini request recovered using configured backup key slot %s", index + 1)
            return result
        except Exception as error:
            last_error = error
            if offset == len(keys) - 1 or not is_gemini_key_or_capacity_error(error):
                raise
            logger.warning("Gemini key slot %s failed with a retryable key/quota/service error; trying the next configured project key", index + 1)
    raise last_error or RuntimeError("No Gemini key could complete the request.")


def get_gemini_client():
    """Return the currently preferred client for operations that need a long-lived job."""
    keys = get_gemini_api_keys()
    if not keys:
        raise RuntimeError("No Gemini keys are configured. Set GEMINI_API_KEY on the server.")
    with _gemini_key_lock:
        index = _gemini_active_key_index % len(keys)
    return genai.Client(api_key=keys[index])


SYSTEM_INSTRUCTION = """
You are Engineer AI.

You are an engineering-focused AI assistant.

Be accurate, practical and clear.

For calculations:
- show the formula
- show the values
- calculate the result
- include units
- do not use currency symbols unless the user asks about money
- write equations as plain text; do not use dollar signs as math delimiters
- recalculate arithmetic and make sure the final result agrees with every step

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

PRODUCT KNOWLEDGE:
- Engineer AI was founded by Ramesh Kannan. If asked who founded it, say Ramesh Kannan.
- The current developer name is the name the user entered in their own profile; do not invent it.
- Available app features include AI chat with saved conversations, file/image attachments,
  project management and project-specific chat, a local saved-file list, Code Studio,
  browser-safe HTML preview, a learning chat, a safe terminal explainer (it does not run
  arbitrary terminal commands), profile memory, and feedback.
- Be honest about tool access. You can only inspect files/projects explicitly included in the
  current request context. You cannot claim to edit a file, run a compiler, generate an image/video,
  or execute a terminal command unless that operation was actually performed by an available tool.
- User memory is supplied as a small editable profile. Use it only when relevant and never infer
  or add personal facts to memory without the user's request.
"""


def generate_ai_response(
    prompt: str,
    max_tokens: int = 1500,
    images: Optional[list[dict[str, str]]] = None
):
    contents: Any = prompt
    if images:
        parts = [types.Part.from_text(text=prompt)]
        for image in images:
            mime_type = image.get("mime_type", "image/jpeg")
            if mime_type not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
                raise ValueError("Attached images must be JPEG, PNG, WebP, or GIF.")
            try:
                image_bytes = base64.b64decode(image["data"], validate=True)
            except (KeyError, binascii.Error, ValueError) as error:
                raise ValueError("An attached image is invalid.") from error
            if len(image_bytes) > 5 * 1024 * 1024:
                raise ValueError("Each image must be 5 MB or smaller.")
            parts.append(types.Part.from_bytes(data=image_bytes, mime_type=mime_type))
        contents = [types.Content(role="user", parts=parts)]

    def generate(client):
        response = client.models.generate_content(
            model=DEFAULT_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                temperature=0.2,
                max_output_tokens=max_tokens,
            ),
        )
        text = getattr(response, "text", None)
        if not text:
            raise RuntimeError("AI returned an empty response.")
        return text.strip()

    return run_with_gemini_failover(generate)


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


def solve_simple_arithmetic(prompt: str) -> Optional[str]:
    """Safely solve a directly stated numeric expression in a chat prompt."""
    match = re.search(
        r"\b(?:what is|calculate|compute|evaluate)\s+([^?\n]+)",
        prompt,
        flags=re.IGNORECASE,
    )
    if not match or len(match.group(1)) > 160:
        return None

    candidate = match.group(1)
    expression_match = re.match(r"\s*([0-9\s()+\-*/%.^×÷−–]+)", candidate)
    if not expression_match:
        return None

    expression = expression_match.group(1).strip().rstrip(".").strip()
    if not expression or not re.search(r"[+*/%×÷−–^-]", expression):
        return None

    normalized = (
        expression.replace("×", "*")
        .replace("÷", "/")
        .replace("−", "-")
        .replace("–", "-")
        .replace("^", "**")
    )

    try:
        tree = ast.parse(normalized, mode="eval")
    except (SyntaxError, ValueError):
        return None

    if sum(1 for _ in ast.walk(tree)) > 40:
        return None

    steps: list[str] = []
    binary_ops = {
        ast.Add: (lambda a, b: a + b, "+"),
        ast.Sub: (lambda a, b: a - b, "−"),
        ast.Mult: (lambda a, b: a * b, "×"),
        ast.Div: (lambda a, b: a / b, "÷"),
        ast.Mod: (lambda a, b: a % b, "%"),
        ast.Pow: (lambda a, b: a ** b, "^"),
    }

    def display_number(value: float | int) -> str:
        if isinstance(value, int) or float(value).is_integer():
            return str(int(value))
        return f"{value:.12g}"

    def evaluate(node: ast.AST) -> float | int:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = evaluate(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp) and type(node.op) in binary_ops:
            left = evaluate(node.left)
            right = evaluate(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 8:
                raise ValueError("Exponent is too large")
            operation, symbol = binary_ops[type(node.op)]
            value = operation(left, right)
            if not math.isfinite(float(value)):
                raise ValueError("Result is not finite")
            steps.append(f"{display_number(left)} {symbol} {display_number(right)} = {display_number(value)}")
            return value
        raise ValueError("Unsupported expression")

    try:
        result = evaluate(tree.body)
    except (ArithmeticError, OverflowError, ValueError):
        return None

    answer = display_number(result)
    request_text = prompt.lower()
    if "number only" in request_text or "answer only" in request_text:
        return answer
    if "show the steps" in request_text or "show steps" in request_text or "step by step" in request_text:
        return "Steps:\n" + "\n".join(steps) + "\nResult: " + answer
    return answer


async def ask_ai(
    prompt: str,
    max_tokens: int = 1500,
    provider: Optional[str] = "gemini",
    images: Optional[list[dict[str, str]]] = None
):

    provider = normalize_provider(provider)

    if provider == "gemini":

        return await asyncio.to_thread(
            generate_ai_response,
            prompt,
            max_tokens,
            images
        )

    if images:
        raise ValueError("Image questions currently require Gemini. Choose Gemini in Settings.")

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
        "gemini": bool(get_gemini_api_keys())
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
            bool(get_gemini_api_keys()),
        "providers": provider_status()
    }


@app.post("/api/feedback")
async def submit_feedback(request: FeedbackRequest):

    created_at = datetime.now(timezone.utc).isoformat()
    connection = get_db()
    cursor = connection.execute(
        "INSERT INTO feedback (category, rating, message, created_at) VALUES (?, ?, ?, ?)",
        (request.category.strip(), request.rating, request.message.strip(), created_at),
    )
    feedback_id = cursor.lastrowid
    connection.commit()
    connection.close()

    delivery = "saved"
    mail_host = os.getenv("SMTP_HOST", "smtp.gmail.com").strip()
    mail_to = os.getenv("FEEDBACK_TO_EMAIL", FEEDBACK_RECIPIENT_DEFAULT).strip()
    if mail_host and mail_to and os.getenv("SMTP_PASSWORD", "").strip():
        try:
            await asyncio.to_thread(send_feedback_email, request, feedback_id, created_at)
            delivery = "emailed"
        except Exception:
            logger.exception("Feedback email delivery failed; feedback remains saved")
            delivery = "saved_email_failed"

    return {
        "status": "success",
        "feedback_id": feedback_id,
        "delivery": delivery,
        "message": (
            "Thanks. Your feedback was sent by email."
            if delivery == "emailed"
            else "Thanks. Your feedback was saved. To send email, configure SMTP_PASSWORD on the server; override SMTP_HOST, SMTP_PORT, or SMTP_USERNAME if your mail provider requires different settings."
        ),
    }


def send_feedback_email(request: FeedbackRequest, feedback_id: int, created_at: str) -> None:
    host = os.environ["SMTP_HOST"]
    port = int(os.getenv("SMTP_PORT", "587"))
    username = os.getenv("SMTP_USERNAME", FEEDBACK_RECIPIENT_DEFAULT)
    password = os.getenv("SMTP_PASSWORD", "")
    sender = os.getenv("SMTP_FROM_EMAIL", username or os.getenv("FEEDBACK_TO_EMAIL", FEEDBACK_RECIPIENT_DEFAULT))
    message = EmailMessage()
    message["Subject"] = f"Engineer AI feedback #{feedback_id}: {request.category}"
    message["From"] = sender
    message["To"] = os.getenv("FEEDBACK_TO_EMAIL", FEEDBACK_RECIPIENT_DEFAULT)
    message.set_content(
        f"Category: {request.category}\nRating: {request.rating}/5\n"
        f"Received: {created_at}\n\n{request.message}"
    )
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=20) as client:
            if username:
                client.login(username, password)
            client.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=20) as client:
            client.starttls()
            if username:
                client.login(username, password)
            client.send_message(message)


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
            bool(get_gemini_api_keys()),
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

        history_lines = []
        for item in request.history[-60:]:
            role = item.get("role", "")
            content = item.get("content", "")
            if role in ("user", "assistant") and content:
                history_lines.append(f"{role.title()}: {content[:2500]}")
        history_context = "\n".join(history_lines)

        memory_context = "\n".join(
            f"{key}: {str(value)[:500]}"
            for key, value in request.memory.items()
            if key in {"name", "age", "preferences", "about"} and str(value).strip()
        )

        prompt = f"""
User message:

{request.prompt}

Answer as Engineer AI.

{SYSTEM_INSTRUCTION}

USER MEMORY (user-editable; use only when relevant):
{memory_context or "(No saved user memory.)"}

APP PROJECTS AND SAVED FILES SHARED FOR THIS TURN:
{request.workspace_context or "(No project/file context was shared.)"}

Use the conversation context below to resolve references to earlier questions and answers.
If the current question asks about a previous message, answer using that context.

Conversation context:
{history_context or "(This is the start of the conversation.)"}

Give a useful engineering-focused answer.
If the user asks for code, provide practical code.
If the user asks for calculations, show the calculation clearly.
If the user asks for a diagram, provide a simple diagram when useful.
"""

        provider = normalize_provider(
            request.provider
        )

        answer = None
        if not request.images:
            answer = solve_simple_arithmetic(request.prompt)

        if answer is None:
            answer = await ask_ai(
                prompt,
                max_tokens=1600,
                provider=provider,
                images=request.images
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

    history: list[dict[str, str]] = Field(
        default_factory=list,
        max_length=80
    )


class ProjectPlanRequest(BaseModel):
    history: list[dict[str, str]] = Field(default_factory=list, max_length=80)
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

        conversation = "\n".join(
            f"{item.get('role', '').title()}: {item.get('content', '')[:2500]}"
            for item in request.history[-40:]
            if item.get("role") in ("user", "assistant") and item.get("content")
        )

        prompt = f"""
You are Engineer AI's project assistant.

Continue the project-specific conversation using the conversation history.
Answer references to earlier messages, and keep the project's saved context in mind.

CONVERSATION HISTORY:
{conversation or "(This is the start of the project conversation.)"}

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


@app.post("/api/projects/{project_id}/plan")
async def update_project_plan(project_id: str, request: ProjectPlanRequest):
    """Review saved project state and its conversation, then persist an AI progress update."""
    start_time = time.perf_counter()
    connection = get_db()
    row = connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if row is None:
        connection.close()
        raise HTTPException(status_code=404, detail={"error": "Project not found"})
    project = dict(row)
    conversation = "\n".join(
        f"{item.get('role', '').title()}: {str(item.get('content', ''))[:1800]}"
        for item in request.history[-40:]
        if item.get("role") in ("user", "assistant") and item.get("content")
    )
    prompt = f"""Review this project's saved code and the available project conversation to update its plan.
Return ONLY one JSON object with keys: progress (integer 0-100), checkpoint (short sentence),
next_mission (short actionable task), timeline (array of 3-6 objects with task and estimate).
Estimate progress from completed evidence in the code and conversation. Never invent completed work.
Project: {project['name']}
Language: {project['language']}
Description: {project['description']}
Previous progress: {project['progress']}%
Saved code:
{str(project['code'])[:18000]}
Project conversation:
{conversation or '(No project chat has been saved yet.)'}
"""
    try:
        raw = await ask_ai(prompt, max_tokens=1200, provider=request.provider)
        match = re.search(r"\{[\s\S]*\}", raw)
        if not match:
            raise ValueError("AI did not return a project plan")
        plan = json.loads(match.group(0))
        progress = max(0, min(100, int(plan.get("progress", project["progress"]))))
        timeline = plan.get("timeline", [])
        if not isinstance(timeline, list):
            timeline = []
        timeline = [
            {"task": str(item.get("task", "Next task"))[:180], "estimate": str(item.get("estimate", ""))[:80]}
            for item in timeline[:6] if isinstance(item, dict)
        ]
        updated_at = datetime.now(timezone.utc).isoformat()
        connection.execute("UPDATE projects SET progress = ?, updated_at = ? WHERE id = ?", (progress, updated_at, project_id))
        connection.commit()
        record_request(start_time, True)
        return {
            "status": "success", "progress": progress,
            "checkpoint": str(plan.get("checkpoint", "Project reviewed."))[:500],
            "next_mission": str(plan.get("next_mission", "Review the saved project and choose the next task."))[:500],
            "timeline": timeline, "updated_at": updated_at,
        }
    except HTTPException:
        raise
    except Exception as error:
        record_request(start_time, False)
        logger.exception("Project plan update failed")
        raise HTTPException(status_code=502, detail={"error": "Project plan update failed", "message": str(error)})
    finally:
        connection.close()


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
# 🎨 AI IMAGE AND VIDEO CREATION
# Requires a Gemini API key with image/video model access.
# ============================================================

def generate_image_data(prompt: str) -> dict[str, str]:
    model = os.getenv("GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image")
    def generate(client):
        interaction = client.interactions.create(
            model=model,
            input=prompt,
            response_format=[{"type": "image", "mime_type": "image/png", "aspect_ratio": "1:1"}],
        )
        image = getattr(interaction, "output_image", None)
        image_data = getattr(image, "data", None) if image else None
        if not image_data:
            raise RuntimeError("Gemini did not return an image. Check model access and API quota.")
        if isinstance(image_data, bytes):
            image_data = base64.b64encode(image_data).decode("ascii")
        return {"status": "complete", "data": image_data, "mime_type": getattr(image, "mime_type", None) or "image/png"}
    return run_with_gemini_failover(generate)


def run_video_generation(job_id: str, prompt: str) -> None:
    job = media_generation_jobs.get(job_id)
    if not job:
        return
    job["status"] = "running"
    try:
        model = os.getenv("GEMINI_VIDEO_MODEL", "veo-3.1-generate-preview")
        client, operation = run_with_gemini_failover(lambda candidate: (candidate, candidate.models.generate_videos(model=model, prompt=prompt)))
        deadline = time.monotonic() + 900
        while not operation.done:
            if time.monotonic() >= deadline:
                raise TimeoutError("Video generation took longer than 15 minutes.")
            time.sleep(5)
            operation = client.operations.get(operation)
        videos = getattr(getattr(operation, "response", None), "generated_videos", None) or []
        if not videos:
            raise RuntimeError("Gemini finished without returning a video.")
        generated_video = videos[0]
        with tempfile.TemporaryDirectory(prefix="engineer-ai-media-") as temp_dir:
            destination = os.path.join(temp_dir, "generated.mp4")
            client.files.download(file=generated_video.video, destination=destination)
            with open(destination, "rb") as media_file:
                encoded = base64.b64encode(media_file.read()).decode("ascii")
        job.update({"status": "complete", "data": encoded, "mime_type": "video/mp4", "finished_at": time.time()})
    except Exception as error:
        logger.exception("Video generation failed")
        job.update({"status": "failed", "message": str(error)[:500], "finished_at": time.time()})


@app.post("/api/media/image")
async def create_image(request: MediaGenerationRequest):
    if not get_gemini_api_keys():
        raise HTTPException(status_code=503, detail={"message": "Image generation needs GEMINI_API_KEY configured on the server."})
    try:
        return await asyncio.to_thread(generate_image_data, request.prompt)
    except Exception as error:
        logger.exception("Image generation failed")
        raise HTTPException(status_code=502, detail={"message": str(error)[:500]})


@app.post("/api/media/video")
async def create_video(request: MediaGenerationRequest, background_tasks: BackgroundTasks):
    if not get_gemini_api_keys():
        raise HTTPException(status_code=503, detail={"message": "Video generation needs GEMINI_API_KEY configured on the server."})
    # Keep only a small number of recent results in memory on this server instance.
    expired = [key for key, value in media_generation_jobs.items() if value.get("finished_at", 0) and time.time() - value["finished_at"] > 3600]
    for key in expired:
        media_generation_jobs.pop(key, None)
    if len(media_generation_jobs) >= 8:
        raise HTTPException(status_code=429, detail={"message": "The video generation queue is full. Try again later."})
    job_id = uuid.uuid4().hex
    media_generation_jobs[job_id] = {"status": "queued", "created_at": time.time()}
    background_tasks.add_task(run_video_generation, job_id, request.prompt)
    return {"status": "queued", "job_id": job_id}


@app.get("/api/media/video/{job_id}")
async def get_video_generation(job_id: str):
    job = media_generation_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail={"message": "Video job not found; generation results are temporary."})
    return {key: value for key, value in job.items() if key not in {"created_at", "finished_at"}}


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
            "/api/media/image",
            "/api/media/video",
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
