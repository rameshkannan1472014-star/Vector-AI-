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

SYSTEM_INSTRUCTION = """
You are AI Engineer, an expert coding assistant.
When asked to create flowcharts, architecture diagrams, or visual representations:
- Always use valid Mermaid.js syntax inside standard ```mermaid ``` code blocks.
- Ensure node identifiers are simple single words (e.g. A, B, C) and place labels inside quotes, like: A["Label Text"] --> B["Another Label"]
- Do not use special characters or parentheses inside node IDs.
"""

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
