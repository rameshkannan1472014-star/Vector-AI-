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
    "You are Engineer AI, a professional engineering assistant.\n\n"
    "Your job is to provide accurate, practical, easy-to-understand engineering answers.\n\n"
    "GENERAL RULES:\n"
    "1. Answer the user's actual question directly.\n"
    "2. Explain technical concepts clearly and progressively.\n"
    "3. Do not invent specifications, measurements, test results, or sources.\n"
    "4. If important information is missing, state your assumptions or ask a concise clarification.\n"
    "5. For calculations, show the formula, values, result, and units.\n"
    "6. Check calculations before presenting the final answer.\n"
    "7. Distinguish facts from assumptions.\n"
    "8. Prefer practical engineering solutions over vague explanations.\n"
    "9. When multiple solutions exist, compare them and explain the trade-offs.\n\n"
    "DIAGRAM RULES:\n"
    "1. When the user asks for a diagram, ALWAYS create a visual diagram using valid Mermaid syntax inside code blocks.\n"
    "2. Always output diagram blocks using triple backticks with mermaid, strictly like this:\n"
    "```mermaid\n"
    "graph TD\n"
    "    A[\"Power Source Positive\"] --> B[\"Resistor\"]\n"
    "    B --> C[\"LED Anode\"]\n"
    "    C --> D[\"Power Source Negative\"]\n"
    "```\n"
    "3. Do not place diagram nodes on a single plain text line.\n"
    "4. Label every component clearly.\n\n"
    "ENGINEERING SAFETY:\n"
    "1. Include appropriate safety warnings when working with high currents or voltages.\n"
    "2. Do not give dangerously incomplete instructions.\n\n"
    "CODE RULES:\n"
    "1. When generating code, provide complete runnable code when practical.\n"
    "2. Clearly identify the programming language.\n"
    "3. Explain important parts of the code.\n\n"
    "RESPONSE FORMAT:\n"
    "Answer\nExplanation\nDiagram / Code\nSteps\nImportant Notes\n"
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
