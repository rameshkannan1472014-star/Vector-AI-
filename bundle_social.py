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

from fastapi import APIRouter, HTTPException, Request
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
    headers = {"x-api-key": key, "Accept": "application/json"}
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
    team_id = _team_id(request)
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
