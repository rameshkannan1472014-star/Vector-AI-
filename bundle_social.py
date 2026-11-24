"""Bundle.social hosted account-connection integration for Vector Growth Agency.

Keep BUNDLE_SOCIAL_API_KEY server-side. Each signed-in Vector user receives a
separate Bundle team unless BUNDLE_SINGLE_TEAM_MODE=true is explicitly enabled
for a single-user test.
"""
import hashlib
import json
import os
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import RedirectResponse

router = APIRouter()
DB_PATH = os.getenv("VECTOR_SOCIAL_DB", "vector_social.db")
PUBLIC_BASE = os.getenv(
    "VECTOR_PUBLIC_BASE_URL", "https://vector-ai-izgf.onrender.com"
).rstrip("/")
BUNDLE_BASE = "https://api.bundle.social/api/v1"
SOCIAL_TYPES = [
    "FACEBOOK", "INSTAGRAM", "TWITTER", "TIKTOK", "LINKEDIN", "YOUTUBE",
    "PINTEREST", "REDDIT", "DISCORD", "SLACK", "MASTODON", "THREADS",
    "BLUESKY", "GOOGLE_BUSINESS", "SNAPCHAT",
]


def _api(method, path, payload=None):
    key = (os.getenv("BUNDLE_SOCIAL_API_KEY") or
           os.getenv("BUNDLE_API_KEY") or "").strip()
    if not key:
        raise HTTPException(
            503, "Add BUNDLE_SOCIAL_API_KEY to Vector's Render environment."
        )
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {
        "x-api-key": key,
        "Accept": "application/json",
        # Identify Vector as an API client rather than urllib's default
        # Python-urllib/X user-agent, which can trigger Cloudflare Error 1010.
        "User-Agent": "Vector-AI-GrowthAgency/1.0 (+https://vector-ai-izgf.onrender.com)",
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        BUNDLE_BASE + path, data=body, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise HTTPException(exc.code, "Bundle API error: " + detail)
    except Exception as exc:
        raise HTTPException(502, "Bundle request failed: " + str(exc)[:250])


def _user(request):
    try:
        from github_auth import current_user
        person = current_user(request)
    except Exception:
        person = None
    if not person:
        raise HTTPException(401, "Sign in to Vector before connecting social accounts.")
    provider = str(person.get("provider") or "vector").lower()
    login = str(person.get("login") or "").strip().lower()
    if not login:
        raise HTTPException(401, "Your Vector sign-in profile is missing a user ID.")
    owner_key = provider + ":" + login
    digest = hashlib.sha256(owner_key.encode("utf-8")).hexdigest()[:10]
    safe_login = "".join(ch for ch in login if ch.isalnum() or ch in "-_")[:35] or "user"
    return owner_key, "Vector " + safe_login + " " + digest


def _db():
    con = sqlite3.connect(DB_PATH, timeout=20)
    con.row_factory = sqlite3.Row
    con.execute(
        """CREATE TABLE IF NOT EXISTS bundle_user_teams (
            owner_key TEXT PRIMARY KEY,
            team_id TEXT NOT NULL,
            team_name TEXT NOT NULL,
            updated REAL NOT NULL
        )"""
    )
    con.commit()
    return con


def _team_id(request):
    owner_key, team_name = _user(request)

    # This explicit mode is for one-person testing only. Never enable it for
    # a multi-user Vector deployment because all users would share one team.
    if os.getenv("BUNDLE_SINGLE_TEAM_MODE", "").strip().lower() == "true":
        configured = os.getenv("BUNDLE_SOCIAL_TEAM_ID", "").strip()
        if configured:
            return configured

    with _db() as con:
        row = con.execute(
            "SELECT team_id FROM bundle_user_teams WHERE owner_key=?",
            (owner_key,),
        ).fetchone()
        if row:
            return row["team_id"]

    # Recover a previously created team if Render's local SQLite file was
    # reset, avoiding duplicate teams after a restart where possible.
    query = urllib.parse.urlencode({
        "limit": 100, "offset": 0, "search": team_name
    })
    teams = _api("GET", "/team?" + query)
    items = teams.get("items", []) if isinstance(teams, dict) else []
    found = next(
        (item for item in items
         if isinstance(item, dict) and item.get("name") == team_name),
        None,
    )
    team_id = str(found.get("id") or "") if found else ""

    if not team_id:
        created = _api("POST", "/team", {"name": team_name})
        team_id = str(
            created.get("id") or
            (created.get("team") or {}).get("id") or ""
        )
    if not team_id:
        raise HTTPException(502, "Bundle did not return a team ID.")

    with _db() as con:
        con.execute(
            """INSERT INTO bundle_user_teams(owner_key,team_id,team_name,updated)
               VALUES(?,?,?,?)
               ON CONFLICT(owner_key) DO UPDATE SET
               team_id=excluded.team_id,team_name=excluded.team_name,
               updated=excluded.updated""",
            (owner_key, team_id, team_name, time.time()),
        )
        con.commit()
    return team_id


@router.get("/api/bundle/connect")
async def bundle_connect(request: Request):
    team_id = _team_id(request)
    redirect_url = PUBLIC_BASE + "/growth-agency?bundle=returned"
    result = _api("POST", "/social-account/create-portal-link", {
        "teamId": team_id,
        "redirectUrl": redirect_url,
        "socialAccountTypes": SOCIAL_TYPES,
        "expiresIn": 10,
    })
    portal_url = str(result.get("url") or "").strip()
    parsed = urllib.parse.urlparse(portal_url)
    if parsed.scheme != "https" or not (
        parsed.hostname == "bundle.social" or
        (parsed.hostname or "").endswith(".bundle.social")
    ):
        raise HTTPException(502, "Bundle returned an unexpected connection URL.")
    return RedirectResponse(portal_url, status_code=302)


@router.get("/api/bundle/accounts")
async def bundle_accounts(request: Request):
    # Single-team mode is only for the owner's private integration test.
    # It lets the chat inspect the same team as a manually generated portal.
    if os.getenv("BUNDLE_SINGLE_TEAM_MODE", "").strip().lower() == "true":
        team_id = os.getenv("BUNDLE_SOCIAL_TEAM_ID", "").strip()
        if not team_id:
            return []
    else:
        owner_key, _team_name = _user(request)
        with _db() as con:
            row = con.execute(
                "SELECT team_id FROM bundle_user_teams WHERE owner_key=?",
                (owner_key,),
            ).fetchone()
        if not row:
            return []
        team_id = row["team_id"]
    path = "/team/" + urllib.parse.quote(team_id, safe="")
    result = _api("GET", path)
    accounts = result.get("socialAccounts", [])
    if not isinstance(accounts, list):
        accounts = []
    safe_accounts = []
    for account in accounts:
        if not isinstance(account, dict):
            continue
        safe_accounts.append({
            "platform": account.get("type") or account.get("socialAccountType") or
                        account.get("platform") or "Social account",
            "name": account.get("name") or account.get("accountName") or
                    account.get("username") or "",
        })
    return {"accounts": safe_accounts}


def chat_connection_context(request):
    """Return non-sensitive Bundle account connection metadata for Vector chat.

    This confirms account linking only; it does not grant chat tools access to
    channel content, analytics, or publishing actions.
    """
    if os.getenv("BUNDLE_SINGLE_TEAM_MODE", "").strip().lower() == "true":
        team_id = os.getenv("BUNDLE_SOCIAL_TEAM_ID", "").strip()
    else:
        owner_key, _team_name = _user(request)
        with _db() as con:
            row = con.execute(
                "SELECT team_id FROM bundle_user_teams WHERE owner_key=?",
                (owner_key,),
            ).fetchone()
        team_id = str(row["team_id"]) if row else ""
    if not team_id:
        return []
    path = "/team/" + urllib.parse.quote(team_id, safe="")
    result = _api("GET", path)
    accounts = result.get("socialAccounts", []) if isinstance(result, dict) else []
    if not isinstance(accounts, list):
        accounts = []
    lines = []
    for account in accounts:
        if not isinstance(account, dict):
            continue
        platform = str(account.get("type") or account.get("socialAccountType") or account.get("platform") or "Social platform")
        name = str(account.get("name") or account.get("accountName") or account.get("username") or "").strip()
        lines.append(platform + (" — " + name if name else ""))
    return lines


def youtube_channel_context(request):
    """Fetch verified YouTube account metadata and available channel analytics.

    This is a read-only chat tool. It uses the user's existing Bundle team
    mapping (or the explicitly configured single-owner test team) and never
    creates a team as a side effect.
    """
    if os.getenv("BUNDLE_SINGLE_TEAM_MODE", "").strip().lower() == "true":
        team_id = os.getenv("BUNDLE_SOCIAL_TEAM_ID", "").strip()
    else:
        owner_key, _team_name = _user(request)
        with _db() as con:
            row = con.execute(
                "SELECT team_id FROM bundle_user_teams WHERE owner_key=?",
                (owner_key,),
            ).fetchone()
        team_id = str(row["team_id"]) if row else ""

    if not team_id:
        return {"status": "not_connected", "message": "No existing Bundle team is linked to this Vector user."}

    try:
        team = _api("GET", "/team/" + urllib.parse.quote(team_id, safe=""))
        accounts = team.get("socialAccounts", []) if isinstance(team, dict) else []
        youtube = [
            item for item in accounts
            if isinstance(item, dict) and str(
                item.get("type") or item.get("socialAccountType") or item.get("platform") or ""
            ).upper() == "YOUTUBE"
        ]
        if not youtube:
            return {"status": "not_connected", "message": "No YouTube account was found on the connected Bundle team."}

        account = youtube[0]
        safe_account = {
            key: account.get(key)
            for key in ("id", "name", "accountName", "username", "handle", "url", "profileUrl", "type")
            if account.get(key) is not None
        }
        result = {
            "status": "success",
            "platform": "YouTube",
            "account": safe_account,
            "analytics_available": False,
        }
        # Bundle's documented raw account analytics endpoint may return
        # channel-level YouTube metrics, subject to plan and granted scopes.
        query = urllib.parse.urlencode({
            "teamId": team_id,
            "platformType": "YOUTUBE",
        })
        try:
            raw = _api("GET", "/analytics/social-account/raw?" + query)
            raw_account = raw.get("socialAccount") if isinstance(raw, dict) else None
            if isinstance(raw_account, dict):
                result["account"].update({
                    key: raw_account.get(key)
                    for key in ("name", "accountName", "username", "handle", "url", "profileUrl")
                    if raw_account.get(key) is not None
                })
            snapshots = raw.get("items", []) if isinstance(raw, dict) else []
            if isinstance(snapshots, list) and snapshots:
                latest = snapshots[-1] if isinstance(snapshots[-1], dict) else {}
                raw_metrics = latest.get("raw") if isinstance(latest, dict) else {}
                if isinstance(raw_metrics, dict):
                    # Only forward aggregate channel metrics, not audience
                    # demographics or other raw analytics fields to the model.
                    allowed_metrics = {
                        "subscribers", "subscriberCount", "views", "viewCount",
                        "watchTime", "watchTimeMinutes", "averageViewDuration",
                        "likes", "comments", "shares", "impressions",
                    }
                    metrics = {
                        key: value for key, value in raw_metrics.items()
                        if key in allowed_metrics and isinstance(value, (str, int, float))
                    }
                    if metrics:
                        result["channel_analytics"] = metrics
                        result["analytics_available"] = True
        except HTTPException as analytics_error:
            # Account connection is still verified even if analytics is
            # unavailable (e.g. plan permissions or Cloudflare/API access).
            result["analytics_error_code"] = int(analytics_error.status_code)
        return result
    except HTTPException as error:
        return {
            "status": "lookup_failed",
            "http_status": int(error.status_code),
            "message": "Bundle could not verify channel details during this request.",
        }

# Universal chat publishing through Bundle.social. Every draft is staged and
# requires the user's exact approval before the API is called.
TEXT_ONLY_TYPES = {
    "FACEBOOK", "TWITTER", "LINKEDIN", "REDDIT", "THREADS",
    "BLUESKY", "MASTODON", "DISCORD", "SLACK", "GOOGLE_BUSINESS",
}
MEDIA_REQUIRED_TYPES = {
    "INSTAGRAM", "TIKTOK", "YOUTUBE", "PINTEREST", "SNAPCHAT",
}


def stage_chat_bundle_post(request, text):
    text = (text or "").strip()
    if not text:
        raise HTTPException(400, "Write the post text first.")
    if len(text) > 5000:
        raise HTTPException(400, "Post text is too long (maximum 5,000 characters).")
    owner_key, _team_name = _user(request)
    team_id = _team_id(request)
    team = _api("GET", "/team/" + urllib.parse.quote(team_id, safe=""))
    accounts = team.get("socialAccounts", []) if isinstance(team, dict) else []
    connected = []
    for account in accounts if isinstance(accounts, list) else []:
        if not isinstance(account, dict):
            continue
        kind = str(account.get("type") or account.get("socialAccountType") or account.get("platform") or "").upper()
        if kind == "X":
            kind = "TWITTER"
        if kind in SOCIAL_TYPES and kind not in connected:
            connected.append(kind)
    if not connected:
        raise HTTPException(400, "No connected Bundle.social accounts were found for this Vector user.")
    targets = [kind for kind in connected if kind in TEXT_ONLY_TYPES]
    skipped = [kind for kind in connected if kind in MEDIA_REQUIRED_TYPES]
    if not targets:
        raise HTTPException(400, "Your connected accounts need image/video media to publish. Add media in Growth Agency first.")
    with _db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS bundle_chat_pending_posts(
            owner_key TEXT PRIMARY KEY, team_id TEXT NOT NULL, post_text TEXT NOT NULL,
            targets_json TEXT NOT NULL, skipped_json TEXT NOT NULL, created REAL NOT NULL)""")
        con.execute("""INSERT INTO bundle_chat_pending_posts
            (owner_key,team_id,post_text,targets_json,skipped_json,created)
            VALUES(?,?,?,?,?,?) ON CONFLICT(owner_key) DO UPDATE SET
            team_id=excluded.team_id,post_text=excluded.post_text,
            targets_json=excluded.targets_json,skipped_json=excluded.skipped_json,
            created=excluded.created""",
            (owner_key, team_id, text, json.dumps(targets), json.dumps(skipped), time.time()))
        con.commit()
    return {"status":"pending_approval","targets":targets,"skipped_media_platforms":skipped,"text":text}


def confirm_chat_bundle_post(request):
    owner_key, _team_name = _user(request)
    current_team_id = _team_id(request)
    with _db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS bundle_chat_pending_posts(
            owner_key TEXT PRIMARY KEY, team_id TEXT NOT NULL, post_text TEXT NOT NULL,
            targets_json TEXT NOT NULL, skipped_json TEXT NOT NULL, created REAL NOT NULL)""")
        row = con.execute("SELECT * FROM bundle_chat_pending_posts WHERE owner_key=?",
                          (owner_key,)).fetchone()
        if not row:
            raise HTTPException(404, "No multi-platform post is awaiting approval.")
        if time.time() - float(row["created"]) > 86400:
            con.execute("DELETE FROM bundle_chat_pending_posts WHERE owner_key=?", (owner_key,))
            con.commit()
            raise HTTPException(410, "The post draft expired. Ask Vector to prepare it again.")
        if str(row["team_id"]) != str(current_team_id):
            raise HTTPException(403, "Your connected social team changed. Prepare the draft again.")
        text = row["post_text"]
        targets = json.loads(row["targets_json"])
        skipped = json.loads(row["skipped_json"])
    if not targets:
        raise HTTPException(400, "No eligible connected platforms in this draft.")
    now = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    payload = {
        "teamId": current_team_id,
        "title": text[:120],
        "postDate": now,
        "status": "SCHEDULED",
        "socialAccountTypes": targets,
        "data": {kind: {"text": text} for kind in targets},
    }
    result = _api("POST", "/post", payload)
    with _db() as con:
        con.execute("DELETE FROM bundle_chat_pending_posts WHERE owner_key=?", (owner_key,))
        con.commit()
    return {"submitted":True,"targets":targets,"skipped_media_platforms":skipped,"result":result}


# Image-post workflow: upload media to Bundle, review connected targets,
# then publish only after the user presses the explicit approval button.
IMAGE_POST_TYPES = {
    "FACEBOOK", "INSTAGRAM", "TWITTER", "LINKEDIN", "REDDIT",
    "THREADS", "BLUESKY", "MASTODON", "GOOGLE_BUSINESS",
}
IMAGE_MAX_BYTES = 10 * 1024 * 1024


def _bundle_upload_image(team_id, filename, content_type, content):
    key = (os.getenv("BUNDLE_SOCIAL_API_KEY") or
           os.getenv("BUNDLE_API_KEY") or "").strip()
    if not key:
        raise HTTPException(503, "Bundle.social API key is not configured on Vector.")
    boundary = "----VectorImageUpload" + str(int(time.time() * 1000))
    chunks = []
    def field(name, value):
        chunks.append(("--" + boundary + "\r\n").encode())
        chunks.append(('Content-Disposition: form-data; name="' + name + '"\r\n\r\n').encode())
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")
    field("teamId", team_id)
    safe_name = os.path.basename(filename or "vector-image").replace('"', "").replace("\\r", "").replace("\\n", "")
    chunks.append(("--" + boundary + "\r\n").encode())
    chunks.append(('Content-Disposition: form-data; name="file"; filename="' + safe_name + '"\r\n').encode())
    chunks.append(("Content-Type: " + content_type + "\r\n\r\n").encode())
    chunks.append(content)
    chunks.append(b"\r\n--" + boundary.encode() + b"--\r\n")
    req = urllib.request.Request(
        BUNDLE_BASE + "/upload", data=b"".join(chunks),
        headers={
            "x-api-key": key, "Accept": "application/json",
            "User-Agent": "Vector-AI-GrowthAgency/1.0 (+https://vector-ai-izgf.onrender.com)",
            "Content-Type": "multipart/form-data; boundary=" + boundary,
        }, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise HTTPException(exc.code, "Bundle image upload failed: " + detail)
    except Exception as exc:
        raise HTTPException(502, "Bundle image upload failed: " + str(exc)[:250])


@router.get("/api/agency/image-post/accounts")
async def image_post_accounts(request: Request):
    _owner_key, _team_name = _user(request)
    team_id = _team_id(request)
    team = _api("GET", "/team/" + urllib.parse.quote(team_id, safe=""))
    accounts = team.get("socialAccounts", []) if isinstance(team, dict) else []
    result = []
    seen = set()
    for account in accounts if isinstance(accounts, list) else []:
        if not isinstance(account, dict):
            continue
        kind = str(account.get("type") or account.get("socialAccountType") or account.get("platform") or "").upper()
        if kind == "X":
            kind = "TWITTER"
        if kind in IMAGE_POST_TYPES and kind not in seen:
            seen.add(kind)
            result.append({
                "platform": kind,
                "name": str(account.get("name") or account.get("accountName") or account.get("username") or kind)
            })
    return {"accounts": result}


@router.post("/api/agency/image-post/prepare")
async def image_post_prepare(
    request: Request,
    image: UploadFile = File(...),
    caption: str = Form(""),
    platforms: str = Form("[]"),
):
    owner_key, _team_name = _user(request)
    team_id = _team_id(request)
    content_type = (image.content_type or "").lower()
    if content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise HTTPException(415, "Choose a JPG, PNG, or WebP image.")
    content = await image.read(IMAGE_MAX_BYTES + 1)
    if not content:
        raise HTTPException(400, "The selected image is empty.")
    if len(content) > IMAGE_MAX_BYTES:
        raise HTTPException(413, "Images must be 10 MB or smaller.")
    caption = (caption or "").strip()
    if len(caption) > 5000:
        raise HTTPException(400, "Caption is too long (maximum 5,000 characters).")
    try:
        requested = json.loads(platforms or "[]")
    except Exception:
        raise HTTPException(400, "Invalid platform selection.")
    if not isinstance(requested, list) or not requested:
        raise HTTPException(400, "Select at least one connected platform.")
    requested = list(dict.fromkeys(str(item).upper() for item in requested))
    if any(item not in IMAGE_POST_TYPES for item in requested):
        raise HTTPException(400, "One or more selected platforms do not support this image workflow.")
    team = _api("GET", "/team/" + urllib.parse.quote(team_id, safe=""))
    connected = set()
    for account in (team.get("socialAccounts", []) if isinstance(team, dict) else []):
        if isinstance(account, dict):
            kind = str(account.get("type") or account.get("socialAccountType") or account.get("platform") or "").upper()
            connected.add("TWITTER" if kind == "X" else kind)
    targets = [item for item in requested if item in connected]
    if not targets:
        raise HTTPException(400, "None of the selected platforms are connected to this Vector account.")
    uploaded = _bundle_upload_image(team_id, image.filename or "vector-image", content_type, content)
    upload_id = str(uploaded.get("uploadId") or uploaded.get("id") or (uploaded.get("upload") or {}).get("id") or "")
    if not upload_id:
        raise HTTPException(502, "Bundle accepted the image but did not return an upload ID.")
    with _db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS bundle_image_pending_posts(
            owner_key TEXT PRIMARY KEY, team_id TEXT NOT NULL, caption TEXT NOT NULL,
            upload_id TEXT NOT NULL, targets_json TEXT NOT NULL, filename TEXT NOT NULL,
            created REAL NOT NULL)""")
        con.execute("""INSERT INTO bundle_image_pending_posts
            (owner_key,team_id,caption,upload_id,targets_json,filename,created)
            VALUES(?,?,?,?,?,?,?) ON CONFLICT(owner_key) DO UPDATE SET
            team_id=excluded.team_id,caption=excluded.caption,upload_id=excluded.upload_id,
            targets_json=excluded.targets_json,filename=excluded.filename,created=excluded.created""",
            (owner_key, team_id, caption, upload_id, json.dumps(targets),
             image.filename or "image", time.time()))
        con.commit()
    return {"status":"pending_approval","filename":image.filename or "image",
            "targets":targets,"caption":caption}


@router.post("/api/agency/image-post/confirm")
async def image_post_confirm(request: Request):
    owner_key, _team_name = _user(request)
    team_id = _team_id(request)
    with _db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS bundle_image_pending_posts(
            owner_key TEXT PRIMARY KEY, team_id TEXT NOT NULL, caption TEXT NOT NULL,
            upload_id TEXT NOT NULL, targets_json TEXT NOT NULL, filename TEXT NOT NULL,
            created REAL NOT NULL)""")
        row = con.execute("SELECT * FROM bundle_image_pending_posts WHERE owner_key=?",
                          (owner_key,)).fetchone()
        if not row:
            raise HTTPException(404, "No image post is awaiting approval. Prepare it again.")
        if time.time() - float(row["created"]) > 86400:
            con.execute("DELETE FROM bundle_image_pending_posts WHERE owner_key=?", (owner_key,))
            con.commit()
            raise HTTPException(410, "The image-post draft expired. Prepare it again.")
        if str(row["team_id"]) != str(team_id):
            raise HTTPException(403, "Your connected social team changed. Prepare the image post again.")
        caption = row["caption"]
        upload_id = row["upload_id"]
        targets = json.loads(row["targets_json"])
    now = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    data = {}
    for kind in targets:
        item = {"text": caption, "uploadIds": [upload_id]}
        if kind == "INSTAGRAM":
            item["type"] = "POST"
        data[kind] = item
    payload = {
        "teamId": team_id, "title": (caption or "Image post")[:120],
        "postDate": now, "status": "SCHEDULED",
        "socialAccountTypes": targets, "data": data,
    }
    result = _api("POST", "/post", payload)
    with _db() as con:
        con.execute("DELETE FROM bundle_image_pending_posts WHERE owner_key=?", (owner_key,))
        con.commit()
    return {"submitted":True,"targets":targets,"result":result}


# YouTube video publishing through Bundle.social (single unified integration).
@router.post("/api/agency/publish/youtube-bundle")
async def agency_publish_youtube_bundle(
    request: Request,
    video: UploadFile = File(...),
    title: str = Form(...),
    description: str = Form(""),
    tags: str = Form(""),
    privacy: str = Form("private"),
):
    owner_key, _team_name = _user(request)
    team_id = _team_id(request)
    if privacy not in {"private", "unlisted", "public"}:
        raise HTTPException(400, "Choose private, unlisted, or public visibility.")
    content_type = (video.content_type or "").lower()
    if content_type not in {"video/mp4", "video/quicktime", "video/webm", "video/mpeg"}:
        raise HTTPException(415, "Choose an MP4, MOV, WebM, or MPEG video.")
    content = await video.read(64 * 1024 * 1024 + 1)
    if not content:
        raise HTTPException(400, "The selected video is empty.")
    if len(content) > 64 * 1024 * 1024:
        raise HTTPException(413, "Video exceeds Vector's 64 MB upload limit.")
    title = (title or "").strip()
    if not title:
        raise HTTPException(400, "Add a YouTube video title.")
    if len(title) > 100:
        raise HTTPException(400, "YouTube titles must be 100 characters or fewer.")
    if len(description or "") > 5000:
        raise HTTPException(400, "Description must be 5,000 characters or fewer.")
    tag_list = [x.strip() for x in (tags or "").split(",") if x.strip()][:30]
    if sum(len(x) for x in tag_list) > 500:
        raise HTTPException(400, "YouTube tags must total 500 characters or fewer.")

    team = _api("GET", "/team/" + urllib.parse.quote(team_id, safe=""))
    accounts = team.get("socialAccounts", []) if isinstance(team, dict) else []
    youtube_connected = any(
        isinstance(a, dict) and str(
            a.get("type") or a.get("socialAccountType") or a.get("platform") or ""
        ).upper() == "YOUTUBE"
        for a in accounts
    )
    if not youtube_connected:
        raise HTTPException(
            400,
            "YouTube is not connected to this Vector user's Bundle team. "
            "Open Growth Agency > Connect accounts and verify YouTube is connected to this same team."
        )

    boundary = "----VectorBundleVideo" + str(int(time.time() * 1000))
    filename = os.path.basename(video.filename or "vector-video.mp4").replace('"', "")
    body = (
        ("--" + boundary + "\r\n").encode()
        + b'Content-Disposition: form-data; name="teamId"\r\n\r\n'
        + team_id.encode() + b"\r\n"
        + ("--" + boundary + "\r\n").encode()
        + ('Content-Disposition: form-data; name="file"; filename="' + filename + '"\r\n').encode()
        + ("Content-Type: " + content_type + "\r\n\r\n").encode()
        + content + b"\r\n"
        + ("--" + boundary + "--\r\n").encode()
    )
    key = (os.getenv("BUNDLE_SOCIAL_API_KEY") or os.getenv("BUNDLE_API_KEY") or "").strip()
    if not key:
        raise HTTPException(503, "Bundle.social API key is not configured on Vector.")
    upload_req = urllib.request.Request(
        BUNDLE_BASE + "/upload", data=body,
        headers={
            "x-api-key": key, "Accept": "application/json",
            "User-Agent": "Vector-AI-GrowthAgency/1.0 (+https://vector-ai-izgf.onrender.com)",
            "Content-Type": "multipart/form-data; boundary=" + boundary,
        }, method="POST"
    )
    try:
        with urllib.request.urlopen(upload_req, timeout=120) as response:
            upload_result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1200]
        raise HTTPException(exc.code, "Bundle video upload failed: " + detail)
    except Exception as exc:
        raise HTTPException(502, "Bundle video upload failed: " + str(exc)[:300])

    upload_id = str(upload_result.get("uploadId") or upload_result.get("id") or "")
    if not upload_id:
        raise HTTPException(502, "Bundle accepted the video but did not return an upload ID.")

    youtube_data = {
        "type": "VIDEO",
        "title": title,
        "text": title,
        "description": description or "",
        "uploadIds": [upload_id],
        "privacy": privacy.upper(),
        "madeForKids": False,
    }
    if tag_list:
        youtube_data["tags"] = tag_list
    post_payload = {
        "teamId": team_id,
        "title": title,
        "postDate": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
        "status": "SCHEDULED",
        "socialAccountTypes": ["YOUTUBE"],
        "data": {"YOUTUBE": youtube_data},
    }
    post_result = _api("POST", "/post", post_payload)
    post_id = str(post_result.get("id") or post_result.get("postId") or "")
    return {
        "submitted": True,
        "platform": "YOUTUBE",
        "postId": post_id,
        "uploadId": upload_id,
        "status": post_result.get("status") or "submitted_to_bundle",
        "result": post_result,
        "note": "Bundle.social accepted the post request. Check its post status for final YouTube processing and publication."
    }
