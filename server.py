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

# System instructions to force concise answers and diagrams
SYSTEM_INSTRUCTION = """
You are AI Engineer, an expert technical partner.
Follow these strict output rules:
1. Keep answers brief, direct, and well-structured.
2. Use clear bullet points and bold headers instead of long paragraphs.
3. NEVER say "As a text-based AI, I cannot create diagrams".
4. When asked for a diagram, flowchart, or architecture visual, output valid Mermaid syntax inside a standard ```mermaid code block.
"""

@app.post("/api/chat")
async def chat_endpoint(request: ChatRequest):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return {"response": "API Key Missing in Render settings."}
    
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
