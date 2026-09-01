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

# Request schema for the AI chat endpoint
class ChatRequest(BaseModel):
    prompt: str

# Initialize Gemini AI Client
API_KEY = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=API_KEY) if API_KEY else None


# 1. SERVE UI (Frontend Home Route)
@app.get("/")
async def serve_ui():
    """Serves the index.html interface directly from the root directory."""
    if os.path.exists("index.html"):
        return FileResponse("index.html")
    return {"error": "index.html file not found in repository root."}


# 2. SERVE AI BRAIN (Backend Chat API)
@app.post("/api/chat")
async def chat_endpoint(request: ChatRequest):
    """Processes prompts from all UI tools and features through Gemini."""
    if not client:
        raise HTTPException(
            status_code=500, 
            detail="GEMINI_API_KEY is not configured in Render Environment Variables."
        )
    
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=request.prompt,
        )
        return {"response": response.text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# 3. HEALTH CHECK ROUTE
@app.get("/health")
async def health_check():
    return {"status": "online", "brain": "Gemini 2.5 Flash", "ui": "Active"}
