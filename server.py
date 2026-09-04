import os
import time
import logging
from typing import Dict, Any, Optional
from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from google import genai
from google.genai import types

# Setup logging and internal telemetry analytics
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("EngineerAI")

analytics_data = {
    "total_requests": 0,
    "successful_responses": 0,
    "failed_requests": 0,
    "total_latency_seconds": 0.0,
    "agent_tasks_executed": 0,
    "security_scans_completed": 0,
    "tests_generated": 0
}

app = FastAPI(title="Engineer AI Core Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatRequest(BaseModel):
    prompt: str
    provider: Optional[str] = "gemini"

class MultiAgentRequest(BaseModel):
    task_description: str
    include_tests: bool = True
    include_security_scan: bool = True

class CodeReviewRequest(BaseModel):
    code_snippet: str
    language: str

@app.get("/")
async def serve_ui():
    if os.path.exists("index.html"):
        return FileResponse("index.html")
    return {"error": "index.html not found"}

SYSTEM_INSTRUCTION = (
    "You are Engineer AI, an advanced engineering assistant.\n\n"
    "Your purpose is to help users understand, design, calculate, troubleshoot, simulate, and implement engineering systems.\n\n"
    "========================\n"
    "CORE BEHAVIOR\n"
    "========================\n\n"
    "1. Answer the user's exact request.\n"
    "2. Be technically accurate.\n"
    "3. Explain concepts clearly and logically.\n"
    "4. Never invent facts, specifications, measurements, test results, or sources.\n"
    "5. If required information is missing, clearly state assumptions or ask for the missing information.\n"
    "6. For calculations, show formula, known values, calculation, final result, and units.\n"
    "7. Check calculations before giving the final answer.\n"
    "8. Distinguish assumptions from confirmed facts.\n"
    "9. When multiple engineering solutions exist, explain important trade-offs.\n"
    "10. Do not claim that something was tested or verified unless it actually was.\n\n"
    "========================\n"
    "DIAGRAM GENERATION\n"
    "========================\n\n"
    "When the user requests a diagram:\n"
    "1. ALWAYS provide a diagram using valid Mermaid syntax inside triple backticks.\n"
    "2. NEVER output raw Mermaid syntax as ordinary paragraph text.\n"
    "3. Keep diagram labels short and readable.\n"
    "4. Clearly show connections between components.\n"
    "5. After the diagram, explain what each component does.\n\n"
    "EXAMPLE FORMAT:\n"
    "```mermaid\n"
    "graph TD\n"
    "    A[\"Power Source +\"] --> B[\"Current-Limiting Resistor\"]\n"
    "    B --> C[\"LED Anode (+)\"]\n"
    "    C --> D[\"LED Cathode (-)\"]\n"
    "    D --> E[\"Power Source -\"]\n"
    "```\n"
)

async def execute_model_inference(prompt: str, provider: str = "gemini", custom_system_prompt: Optional[str] = None) -> str:
    """Provider abstraction layer supporting Gemini and local CUDA model execution."""
    if provider == "gemini":
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY environment variable is missing.")
        
        client = genai.Client(api_key=api_key)
        instruction = custom_system_prompt if custom_system_prompt else SYSTEM_INSTRUCTION
        
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=instruction
            )
        )
        return response.text if response.text else "Empty response received."
    
    elif provider == "local_cuda":
        return "Local CUDA/TensorRT inference engine is not configured on this host."
    
    else:
        raise ValueError(f"Unknown provider: {provider}")

def log_telemetry(start_time: float, success: bool):
    latency = time.time() - start_time
    analytics_data["total_requests"] += 1
    analytics_data["total_latency_seconds"] += latency
    if success:
        analytics_data["successful_responses"] += 1
    else:
        analytics_data["failed_requests"] += 1

@app.post("/api/chat")
async def chat_endpoint(request: ChatRequest, background_tasks: BackgroundTasks):
    start_time = time.time()
    try:
        response_text = await execute_model_inference(request.prompt, request.provider)
        background_tasks.add_task(log_telemetry, start_time, True)
        return {"response": response_text, "provider": request.provider}
    except Exception as e:
        background_tasks.add_task(log_telemetry, start_time, False)
        logger.error(f"Inference Error: {str(e)}")
        return {"response": f"Engine Error: {str(e)}"}

@app.post("/api/agent/execute")
async def multi_agent_endpoint(request: MultiAgentRequest, background_tasks: BackgroundTasks):
    """Executes multi-agent workflow: Planning -> Execution -> Test Generation -> Security Audit."""
    start_time = time.time()
    analytics_data["agent_tasks_executed"] += 1
    
    try:
        # Agent 1: Planner
        planner_prompt = f"Planner Agent: Deconstruct this engineering request into clear technical steps: {request.task_description}"
        plan = await execute_model_inference(planner_prompt)
        
        # Agent 2: Executor
        executor_prompt = f"Executor Agent: Provide complete technical execution, code, or schematics for: {request.task_description}"
        implementation = await execute_model_inference(executor_prompt)
        
        result = {
            "plan": plan,
            "implementation": implementation,
            "tests": None,
            "security_scan": None
        }
        
        # Agent 3: Test Generator
        if request.include_tests:
            analytics_data["tests_generated"] += 1
            test_prompt = f"Test Agent: Generate unit tests, assertion checks, and validation logic for:\n{implementation}"
            result["tests"] = await execute_model_inference(test_prompt)
            
        # Agent 4: Security & Safety Auditor
        if request.include_security_scan:
            analytics_data["security_scans_completed"] += 1
            security_prompt = f"Security & Safety Agent: Conduct a safety, current limit, thermal, and security audit on:\n{implementation}"
            result["security_scan"] = await execute_model_inference(security_prompt)
            
        background_tasks.add_task(log_telemetry, start_time, True)
        return {"status": "success", "agent_outputs": result}
        
    except Exception as e:
        background_tasks.add_task(log_telemetry, start_time, False)
        return {"status": "error", "message": f"Multi-Agent Execution Error: {str(e)}"}

@app.post("/api/code/review")
async def code_review_endpoint(request: CodeReviewRequest):
    """Automated PR/Code Reviewer."""
    review_prompt = f"Review the following {request.language} code for bugs, performance optimizations, thermal/electrical limits, and style:\n\n{request.code_snippet}"
    try:
        review_result = await execute_model_inference(review_prompt)
        return {"status": "success", "review": review_result}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/ops/analytics")
async def get_analytics():
    """Returns telemetry and ops performance metrics."""
    avg_latency = (
        analytics_data["total_latency_seconds"] / analytics_data["total_requests"]
        if analytics_data["total_requests"] > 0 else 0.0
    )
    return {
        "metrics": analytics_data,
        "average_latency_seconds": round(avg_latency, 3),
        "system_status": "Operational"
    }
