import os
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from google import genai

app = FastAPI()

# Enable CORS for browser access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatRequest(BaseModel):
    prompt: str

# 1. SERVE FRONTEND INTERFACE
@app.get("/")
async def serve_ui():
    """Serves index.html when opening the home page."""
    if os.path.exists("index.html"):
        return FileResponse("index.html")
    return {"error": "index.html not found in repository root."}

# 2. AI CHAT BACKEND API
@app.post("/api/chat")
async def chat_endpoint(request: ChatRequest):
    """Handles chat requests from your frontend and connects to Gemini."""
    api_key = os.getenv("GEMINI_API_KEY")
    
    if not api_key:
        return {
            "response": "API Key Missing: GEMINI_API_KEY is not set in Render Environment Variables."
        }
    
    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=request.prompt,
        )
        
        if response.text:
            return {"response": response.text}
        return {"response": "Gemini returned an empty response. Please try again."}
        
    except Exception as e:
        return {"response": f"Backend Error: {str(e)}"}

# 3. HEALTH CHECK ENDPOINT
@app.get("/health")
async def health_check():
    return {"status": "online", "brain": "Gemini 2.5 Flash"}
