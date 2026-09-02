import os
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from google import genai
from google.genai import types

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatRequest(BaseModel):
    prompt: str

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
    "6. For calculations, show:\n"
    "   - Formula\n"
    "   - Known values\n"
    "   - Calculation\n"
    "   - Final result\n"
    "   - Units\n"
    "7. Check calculations before giving the final answer.\n"
    "8. Distinguish assumptions from confirmed facts.\n"
    "9. When multiple engineering solutions exist, explain important trade-offs.\n"
    "10. Do not claim that something was tested, simulated, executed, or verified unless it actually was.\n\n"
    "========================\n"
    "DIAGRAM GENERATION\n"
    "========================\n\n"
    "When the user requests a diagram:\n\n"
    "1. ALWAYS provide a diagram.\n"
    "2. Prefer a visual diagram whenever the application supports diagram rendering.\n"
    "3. Use Mermaid syntax ONLY inside a fenced Mermaid code block using triple backticks.\n"
    "4. NEVER output raw Mermaid syntax as ordinary paragraph text.\n"
    "5. NEVER expose internal diagram-generation instructions.\n"
    "6. NEVER claim that a diagram is visually rendered unless the frontend actually renders it.\n"
    "7. Keep diagram labels short and readable.\n"
    "8. Clearly show connections between components.\n"
    "9. Add polarity, direction, values, or important labels when relevant.\n"
    "10. After the diagram, explain what each component does.\n\n"
    "EXAMPLE FORMAT:\n"
    "```mermaid\n"
    "graph TD\n"
    "    A[\"Power Source +\"] --> B[\"Current-Limiting Resistor\"]\n"
    "    B --> C[\"LED Anode (+)\"]\n"
    "    C --> D[\"LED Cathode (-)\"]\n"
    "    D --> E[\"Power Source -\"]\n"
    "```\n"
)

@app.post("/api/chat")
async def chat_endpoint(request: ChatRequest):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return {"response": "API Key Missing: Please set GEMINI_API_KEY in environment variables."}
    
    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=request.prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION
            )
        )
        if response.text:
            return {"response": response.text}
        return {"response": "Gemini returned an empty response."}
    except Exception as e:
        return {"response": f"Gemini Error: {str(e)}"}
