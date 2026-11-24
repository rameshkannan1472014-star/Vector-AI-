"""Vector Growth Agency social connectors (LinkedIn, YouTube, TikTok).

OAuth tokens are encrypted at rest. Configure VECTOR_SOCIAL_ENCRYPTION_KEY
with a Fernet key and provider credentials in the hosting environment.
"""
import os, json, time, secrets, hashlib, base64, sqlite3, urllib.parse, urllib.request, urllib.error
from pathlib import Path
from fastapi import APIRouter, Request, HTTPException, UploadFile, File, Form
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from cryptography.fernet import Fernet, InvalidToken

router = APIRouter()
DB_PATH = os.getenv("VECTOR_SOCIAL_DB", "vector_social.db")
PUBLIC_BASE = os.getenv("VECTOR_PUBLIC_BASE_URL", "https://vector-ai-izgf.onrender.com").rstrip("/")
COOKIE = "vector_social_sid"
PROVIDERS = {
    "linkedin": {
        "client": "LINKEDIN_CLIENT_ID", "secret": "LINKEDIN_CLIENT_SECRET",
        "authorize": "https://www.linkedin.com/oauth/v2/authorization",
        "token": "https://www.linkedin.com/oauth/v2/accessToken",
        "callback": "/api/social/callback/linkedin",
        "scopes": "openid profile w_member_social",
    },
    "youtube": {
        "client": "YOUTUBE_CLIENT_ID", "secret": "YOUTUBE_CLIENT_SECRET",
        "authorize": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "callback": "/api/social/callback/youtube",
        "scopes": "openid email profile https://www.googleapis.com/auth/youtube.upload",
    },
    "tiktok": {
        "client": "TIKTOK_CLIENT_KEY", "secret": "TIKTOK_CLIENT_SECRET",
        "authorize": "https://www.tiktok.com/v2/auth/authorize/",
        "token": "https://open.tiktokapis.com/v2/oauth/token/",
        "callback": "/api/social/callback/tiktok",
        "scopes": "user.info.basic,video.upload,video.publish",
    },
}

def _db():
    con = sqlite3.connect(DB_PATH, timeout=20)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS social_states(
        state TEXT PRIMARY KEY, session_id TEXT NOT NULL, platform TEXT NOT NULL,
        verifier TEXT, created REAL NOT NULL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS social_accounts(
        session_id TEXT NOT NULL, platform TEXT NOT NULL, access_token TEXT NOT NULL,
        refresh_token TEXT, expires_at REAL, profile_json TEXT NOT NULL,
        updated REAL NOT NULL, PRIMARY KEY(session_id,platform))""")
    con.commit()
    return con

def _fernet():
    key = os.getenv("VECTOR_SOCIAL_ENCRYPTION_KEY", "").strip()
    if not key:
        raise HTTPException(503, "Set VECTOR_SOCIAL_ENCRYPTION_KEY in Render before connecting social accounts.")
    try:
        return Fernet(key.encode())
    except Exception:
        raise HTTPException(500, "VECTOR_SOCIAL_ENCRYPTION_KEY must be a valid Fernet key.")

def _enc(value):
    return _fernet().encrypt((value or "").encode()).decode() if value else None

def _dec(value):
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        raise HTTPException(500, "Social token encryption key changed. Reconnect this account.")

def _session(request):
    return request.cookies.get(COOKIE) or secrets.token_urlsafe(32)

def _json_request(url, data=None, headers=None, method=None):
    body = None
    hdr = dict(headers or {})
    if data is not None:
        if isinstance(data, dict):
            body = json.dumps(data).encode()
            hdr.setdefault("Content-Type", "application/json")
        else:
            body = data
    req = urllib.request.Request(url, data=body, headers=hdr, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read()
            return json.loads(raw.decode()) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:1500]
        raise HTTPException(exc.code, f"Provider API error: {detail}")
    except Exception as exc:
        raise HTTPException(502, f"Provider request failed: {str(exc)[:300]}")

def _account(session, platform):
    with _db() as con:
        row = con.execute("SELECT * FROM social_accounts WHERE session_id=? AND platform=?", (session, platform)).fetchone()
    if not row:
        raise HTTPException(401, f"Connect your {platform} account first.")
    return {
        "access_token": _dec(row["access_token"]),
        "refresh_token": _dec(row["refresh_token"]),
        "expires_at": row["expires_at"],
        "profile": json.loads(row["profile_json"] or "{}")
    }

def _save_account(session, platform, tokens, profile):
    expires = time.time() + int(tokens.get("expires_in", 3600))
    with _db() as con:
        con.execute("""INSERT INTO social_accounts VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(session_id,platform) DO UPDATE SET
        access_token=excluded.access_token, refresh_token=excluded.refresh_token,
        expires_at=excluded.expires_at, profile_json=excluded.profile_json,
        updated=excluded.updated""",
        (session, platform, _enc(tokens.get("access_token")), _enc(tokens.get("refresh_token")),
         expires, json.dumps(profile), time.time()))
        con.commit()

def _refresh_google(account):
    if account["expires_at"] and account["expires_at"] > time.time() + 90:
        return account["access_token"]
    if not account["refresh_token"]:
        raise HTTPException(401, "YouTube authorization expired. Reconnect your channel.")
    result = _json_request(PROVIDERS["youtube"]["token"], urllib.parse.urlencode({
        "client_id": os.getenv("YOUTUBE_CLIENT_ID",""),
        "client_secret": os.getenv("YOUTUBE_CLIENT_SECRET",""),
        "refresh_token": account["refresh_token"], "grant_type": "refresh_token"
    }).encode(), {"Content-Type":"application/x-www-form-urlencoded"}, "POST")
    return result.get("access_token", account["access_token"])

class DraftRequest(BaseModel):
    project_name: str = Field(min_length=1, max_length=120)
    project_url: str = Field(default="", max_length=500)
    summary: str = Field(min_length=1, max_length=3000)

class LinkedInPost(BaseModel):
    text: str = Field(min_length=1, max_length=3000)
    project_url: str = Field(default="", max_length=500)

class TikTokPublish(BaseModel):
    video_url: str = Field(min_length=8, max_length=1000)
    caption: str = Field(default="", max_length=2200)
    privacy_level: str = Field(default="SELF_ONLY")

@router.get("/growth-agency", response_class=HTMLResponse)
async def growth_agency_page():
    path = Path("static/growth-agency.html")
    if not path.exists():
        raise HTTPException(404, "Growth Agency page not installed")
    return HTMLResponse(path.read_text(encoding="utf-8"))

@router.get("/api/social/connect/{platform}")
async def social_connect(platform: str, request: Request):
    if platform not in PROVIDERS:
        raise HTTPException(404, "Unsupported social platform")
    cfg = PROVIDERS[platform]
    client_id, secret = os.getenv(cfg["client"], "").strip(), os.getenv(cfg["secret"], "").strip()
    if not client_id or not secret:
        raise HTTPException(503, f"Set {cfg['client']} and {cfg['secret']} in Render.")
    session = _session(request)
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    with _db() as con:
        con.execute("INSERT INTO social_states VALUES(?,?,?,?,?)", (state, session, platform, verifier, time.time()))
        con.commit()
    callback = PUBLIC_BASE + cfg["callback"]
    params = {"client_id":client_id, "redirect_uri":callback, "response_type":"code",
              "scope":cfg["scopes"], "state":state}
    if platform == "tiktok":
        params.pop("client_id", None)
        params["client_key"] = client_id
        params["redirect_uri"] = callback
        params["code_challenge"] = challenge
        params["code_challenge_method"] = "S256"
    elif platform == "youtube":
        params.update({"access_type":"offline","prompt":"consent","include_granted_scopes":"true"})
        params["code_challenge"] = challenge
        params["code_challenge_method"] = "S256"
    else:
        params["code_challenge"] = challenge
        params["code_challenge_method"] = "S256"
    response = RedirectResponse(cfg["authorize"] + "?" + urllib.parse.urlencode(params), status_code=302)
    response.set_cookie(COOKIE, session, httponly=True, secure=True, samesite="lax", max_age=60*60*24*180)
    return response

@router.get("/api/social/callback/{platform}")
async def social_callback(platform: str, request: Request, code: str = "", state: str = "", error: str = ""):
    if platform not in PROVIDERS:
        raise HTTPException(404, "Unsupported social platform")
    if error:
        return RedirectResponse("/growth-agency?connect=cancelled")
    cfg = PROVIDERS[platform]
    session = request.cookies.get(COOKIE, "")
    with _db() as con:
        row = con.execute("SELECT * FROM social_states WHERE state=? AND session_id=? AND platform=?",
                          (state, session, platform)).fetchone()
        con.execute("DELETE FROM social_states WHERE state=?", (state,))
        con.commit()
    if not row or time.time() - row["created"] > 600:
        raise HTTPException(400, "OAuth state expired or mismatched. Start Connect again.")
    callback = PUBLIC_BASE + cfg["callback"]
    form = {"client_id":os.getenv(cfg["client"],""), "client_secret":os.getenv(cfg["secret"],""),
            "code":code, "grant_type":"authorization_code", "redirect_uri":callback}
    if platform == "tiktok":
        form["client_key"] = form.pop("client_id")
        form["code_verifier"] = row["verifier"]
    elif platform == "youtube":
        form["code_verifier"] = row["verifier"]
    else:
        form["code_verifier"] = row["verifier"]
    tokens = _json_request(cfg["token"], urllib.parse.urlencode(form).encode(),
                            {"Content-Type":"application/x-www-form-urlencoded"}, "POST")
    if platform == "tiktok" and isinstance(tokens.get("data"), dict):
        tokens = {**tokens, **tokens["data"]}
    access = tokens.get("access_token")
    if not access:
        raise HTTPException(502, "Provider did not return an access token.")
    profile = {}
    if platform == "linkedin":
        profile = _json_request("https://api.linkedin.com/v2/userinfo", headers={"Authorization":"Bearer "+access})
    elif platform == "youtube":
        profile = _json_request("https://www.googleapis.com/oauth2/v3/userinfo", headers={"Authorization":"Bearer "+access})
    else:
        data = _json_request("https://open.tiktokapis.com/v2/user/info/?fields=open_id,display_name,avatar_url",
                             headers={"Authorization":"Bearer "+access})
        profile = data.get("data",{}).get("user",{})
    tokens["access_token"] = access
    _save_account(session, platform, tokens, profile)
    return RedirectResponse("/growth-agency?connected=" + platform, status_code=303)

@router.get("/api/social/accounts")
async def social_accounts(request: Request):
    session = request.cookies.get(COOKIE, "")
    if not session:
        return {"accounts":[]}
    with _db() as con:
        rows = con.execute("SELECT platform,profile_json,expires_at,updated FROM social_accounts WHERE session_id=?",
                           (session,)).fetchall()
    return {"accounts":[{"platform":r["platform"],"profile":json.loads(r["profile_json"] or "{}"),
                         "expires_at":r["expires_at"],"connected":True} for r in rows]}

@router.delete("/api/social/accounts/{platform}")
async def social_disconnect(platform: str, request: Request):
    session = request.cookies.get(COOKIE, "")
    with _db() as con:
        con.execute("DELETE FROM social_accounts WHERE session_id=? AND platform=?", (session,platform))
        con.commit()
    return {"disconnected":platform}

@router.post("/api/agency/draft")
async def agency_draft(body: DraftRequest):
    name = body.project_name.strip()
    url = body.project_url.strip()
    summary = body.summary.strip()
    link = ("\n" + url) if url else ""
    return {"drafts":{
        "linkedin":f"🚀 Introducing {name}!\n\n{summary}{link}\n\n#BuildInPublic #IndieProjects",
        "youtube":f"{name} — project demo\n\n{summary}\n\n{url}".strip(),
        "tiktok":f"{name} is live 🚀 {summary[:220]} {url} #buildinpublic #tech"
    }, "note":"Drafts only. Review and approve each post before publishing."}

@router.post("/api/agency/publish/linkedin")
async def agency_publish_linkedin(body: LinkedInPost, request: Request):
    session = request.cookies.get(COOKIE, "")
    account = _account(session, "linkedin")
    urn = account["profile"].get("sub")
    if not urn:
        raise HTTPException(400, "LinkedIn profile ID unavailable. Reconnect your account.")
    text = body.text.strip()
    if body.project_url:
        text += "\n" + body.project_url.strip()
    post = {"author":"urn:li:person:"+urn, "commentary":text,
             "visibility":"PUBLIC", "distribution":{"feedDistribution":"MAIN_FEED",
             "targetEntities":[],"thirdPartyDistributionChannels":[]},
             "lifecycleState":"PUBLISHED","isReshareDisabledByAuthor":False}
    result = _json_request("https://api.linkedin.com/rest/posts", post,
        {"Authorization":"Bearer "+account["access_token"],"LinkedIn-Version":"202601",
         "X-Restli-Protocol-Version":"2.0.0"})
    return {"published":True,"platform":"linkedin","result":result}

@router.post("/api/agency/publish/tiktok")
async def agency_publish_tiktok(body: TikTokPublish, request: Request):
    session = request.cookies.get(COOKIE, "")
    account = _account(session, "tiktok")
    if body.privacy_level not in {"SELF_ONLY","PUBLIC_TO_EVERYONE","MUTUAL_FOLLOW_FRIENDS","FOLLOWER_OF_CREATOR"}:
        raise HTTPException(400, "Invalid TikTok privacy setting")
    payload = {"post_info":{"title":body.caption[:150],"privacy_level":body.privacy_level,
               "disable_duet":False,"disable_comment":False,"disable_stitch":False},
               "source_info":{"source":"PULL_FROM_URL","video_url":body.video_url}}
    result = _json_request("https://open.tiktokapis.com/v2/post/publish/video/init/",
        payload, {"Authorization":"Bearer "+account["access_token"]})
    return {"submitted":True,"platform":"tiktok","result":result,
            "note":"Public posting requires TikTok app review and a verified video URL domain. Provider may restrict unaudited apps."}

@router.post("/api/agency/publish/youtube")
async def agency_publish_youtube(request: Request, video: UploadFile = File(...),
                                title: str = Form(...), description: str = Form(""),
                                tags: str = Form(""), privacy: str = Form("private")):
    session = request.cookies.get(COOKIE, "")
    account = _account(session, "youtube")
    if privacy not in {"private","unlisted","public"}:
        raise HTTPException(400, "privacy must be private, unlisted, or public")
    content = await video.read(64*1024*1024 + 1)
    if len(content) > 64*1024*1024:
        raise HTTPException(413, "Video exceeds the 64 MB initial upload limit")
    if not content:
        raise HTTPException(400, "Empty video file")
    access = _refresh_google(account)
    tag_list = [item.strip() for item in (tags or "").split(",") if item.strip()]
    tag_list = tag_list[:30]
    if sum(len(item) for item in tag_list) > 500:
        raise HTTPException(400, "YouTube tags must total 500 characters or fewer.")
    snippet = {"title":title[:100],"description":description[:5000],"categoryId":"28"}
    if tag_list:
        snippet["tags"] = tag_list
    metadata = json.dumps({"snippet":snippet,"status":{"privacyStatus":privacy}}).encode()
    init_req = urllib.request.Request(
        "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
        data=metadata,
        headers={"Authorization":"Bearer "+access,"Content-Type":"application/json; charset=UTF-8",
                "X-Upload-Content-Type":video.content_type or "video/mp4",
                "X-Upload-Content-Length":str(len(content))}, method="POST")
    try:
        with urllib.request.urlopen(init_req, timeout=30) as init_response:
            upload_url = init_response.headers.get("Location")
    except urllib.error.HTTPError as exc:
        raise HTTPException(exc.code,"YouTube upload initialization failed: "+exc.read().decode(errors="replace")[:1000])
    if not upload_url:
        raise HTTPException(502, "YouTube did not return a resumable upload URL.")
    req = urllib.request.Request(upload_url,data=content,headers={"Content-Type":video.content_type or "video/mp4",
        "Content-Length":str(len(content))},method="PUT")
    try:
        with urllib.request.urlopen(req,timeout=120) as response:
            result=json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        raise HTTPException(exc.code,"YouTube upload failed: "+exc.read().decode(errors="replace")[:1000])
    return {"published":True,"platform":"youtube","video_id":result.get("id"),
            "url":"https://youtube.com/watch?v="+result.get("id",""),"result":result}


# Chat publishing: a post is staged first and only published after the user
# explicitly replies "APPROVE POST" in the same signed browser session.
def stage_chat_linkedin_post(session, text, project_url=""):
    if not session:
        raise HTTPException(401, "Open Vector in the same browser and connect LinkedIn first.")
    text = (text or "").strip()
    project_url = (project_url or "").strip()
    if not text:
        raise HTTPException(400, "Post text is required.")
    if len(text) > 3000:
        raise HTTPException(400, "LinkedIn post text must be 3,000 characters or fewer.")
    if project_url:
        text += "\n" + project_url[:500]
    with _db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS chat_pending_posts(
            session_id TEXT PRIMARY KEY, platform TEXT NOT NULL,
            post_text TEXT NOT NULL, created REAL NOT NULL)""")
        con.execute("""INSERT INTO chat_pending_posts(session_id,platform,post_text,created)
            VALUES(?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET
            platform=excluded.platform,post_text=excluded.post_text,created=excluded.created""",
            (session, "linkedin", text, time.time()))
        con.commit()
    return {"status":"pending_approval","platform":"linkedin","text":text}


def confirm_chat_linkedin_post(session):
    if not session:
        raise HTTPException(401, "Your Vector social session is missing. Reopen Growth Agency.")
    with _db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS chat_pending_posts(
            session_id TEXT PRIMARY KEY, platform TEXT NOT NULL,
            post_text TEXT NOT NULL, created REAL NOT NULL)""")
        row = con.execute("SELECT * FROM chat_pending_posts WHERE session_id=?",
                          (session,)).fetchone()
        if not row:
            raise HTTPException(404, "No post is waiting for approval.")
        if time.time() - float(row["created"]) > 86400:
            con.execute("DELETE FROM chat_pending_posts WHERE session_id=?", (session,))
            con.commit()
            raise HTTPException(410, "The draft expired. Ask Vector to prepare it again.")
        post_text = row["post_text"]
    account = _account(session, "linkedin")
    urn = account["profile"].get("sub")
    if not urn:
        raise HTTPException(400, "LinkedIn profile ID unavailable. Reconnect LinkedIn.")
    post = {"author":"urn:li:person:"+urn,"commentary":post_text,
            "visibility":"PUBLIC","distribution":{"feedDistribution":"MAIN_FEED",
            "targetEntities":[],"thirdPartyDistributionChannels":[]},
            "lifecycleState":"PUBLISHED","isReshareDisabledByAuthor":False}
    result = _json_request("https://api.linkedin.com/rest/posts", post,
        {"Authorization":"Bearer "+account["access_token"],"LinkedIn-Version":"202601",
         "X-Restli-Protocol-Version":"2.0.0"})
    with _db() as con:
        con.execute("DELETE FROM chat_pending_posts WHERE session_id=?", (session,))
        con.commit()
    return {"published":True,"platform":"linkedin","result":result,"text":post_text}


class ChatPublishStage(BaseModel):
    text: str = Field(min_length=1, max_length=3000)
    project_url: str = Field(default="", max_length=500)


@router.post("/api/agency/chat-publish/stage")
async def chat_publish_stage(body: ChatPublishStage, request: Request):
    session = request.cookies.get(COOKIE, "")
    return stage_chat_linkedin_post(session, body.text, body.project_url)


@router.post("/api/agency/chat-publish/confirm")
async def chat_publish_confirm(request: Request):
    session = request.cookies.get(COOKIE, "")
    return confirm_chat_linkedin_post(session)
