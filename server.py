import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import google.generativeai as genai

app = FastAPI(title="AI Engineer Backend")

# Enable CORS for web deployment
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pull API key from environment variables on Render
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

model = genai.GenerativeModel('gemini-2.5-flash')

class PromptRequest(BaseModel):
    prompt: str

@app.get("/")
def home():
    return {"status": "online", "message": "AI Engineer Backend Running on Render"}

@app.post("/api/chat")
async def generate_response(request: PromptRequest):
    try:
        if not request.prompt.strip():
            raise HTTPException(status_code=400, detail="Prompt cannot be empty")
        
        system_instruction = "You are AI Engineer, an expert coding assistant built to help developer Ramesh write, debug, and optimize code efficiently."
        full_prompt = f"{system_instruction}\n\nUser Question: {request.prompt}"

        response = model.generate_content(full_prompt)
        return {"response": response.text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
