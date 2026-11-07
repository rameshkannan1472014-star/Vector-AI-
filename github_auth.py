"""Optional GitHub sign-in for Vector AI.

Kept in its own module so the large server.py does not have to be rewritten.
app.py imports it, adds nothing else, and is started instead of server.py.

Switched on by setting two environment variables on the server:

    GITHUB_CLIENT_ID      from https://github.com/settings/developers
    GITHUB_CLIENT_SECRET  (same page, Generate a new client secret)

with the OAuth app Authorization callback URL set to

    https://YOUR-APP.onrender.com/auth/github/callback

Optionally set SESSION_SECRET (any long random text) so sign-ins survive a
restart. Without it, cookies are signed with a key regenerated per process.
"""

import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
import urllib.parse
import urllib.request
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse

logger = logging.getLogger("vector.github")

SESSION_COOKIE = "vector_session"
OAUTH_STATE_COOKIE = "vector_oauth_state"
SESSION_MAX_AGE = 60 * 60 * 24 * 30


def install(app: FastAPI, app_title: str = "Vector AI") -> None:
    client_id = os.getenv("GITHUB_CLIENT_ID", "").strip()
    client_secret = os.getenv("GITHUB_CLIENT_SECRET", "").strip()
    session_secret = (os.getenv("SESSION_SECRET") or os.getenv("SECRET_KEY") or "").strip()

    def configured() -> bool:
        return bool(client_id and client_secret)

    def session_key() -> bytes:
        if session_secret:
            return session_secret.encode("utf-8")
        return hashlib.sha256(f"{app_title}:{os.getpid()}".encode("utf-8")).digest()

    def sign(payload: dict) -> str:
        raw = base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
        ).decode("ascii").rstrip("=")
        sig = hmac.new(session_key(), raw.encode("ascii"), hashlib.sha256).hexdigest()[:40]
        return raw + "." + sig

    def unsign(token: str) -> Optional[dict]:
        if not token or "." not in token:
            return None
        raw, _, sig = token.rpartition(".")
        expected = hmac.new(session_key(), raw.encode("ascii"), hashlib.sha256).hexdigest()[:40]
        if not hmac.compare_digest(sig, expected):
            return None
        try:
            padded = raw + "=" * (-len(raw) % 4)
            data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        except (ValueError, binascii.Error):
            return None
        if not isinstance(data, dict):
            return None
        try:
            if int(data.get("exp", 0)) < int(time.time()):
                return None
        except (TypeError, ValueError):
            return None
        return data

    def current_user(request: Request) -> Optional[dict]:
        return unsign(request.cookies.get(SESSION_COOKIE, ""))

    def origin(request: Request) -> str:
        proto = request.headers.get("x-forwarded-proto") or request.url.scheme or "https"
        host = (
            request.headers.get("x-forwarded-host")
            or request.headers.get("host")
            or request.url.netloc
        )
        return f"{proto.split(',')[0].strip()}://{host.split(',')[0].strip()}"

    def github_json(url: str, data: Optional[bytes] = None, token: Optional[str] = None) -> dict:
        headers = {"Accept": "application/json", "User-Agent": "vector-ai"}
        if data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request_object = urllib.request.Request(
            url, data=data, headers=headers, method="POST" if data is not None else "GET"
        )
        with urllib.request.urlopen(request_object, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))

    @app.get("/api/me")
    async def api_me(request: Request):
        user = current_user(request)
        return {
            "signed_in": bool(user),
            "auth_configured": configured(),
            "user": (
                {
                    "login": user.get("login", ""),
                    "name": user.get("name", ""),
                    "avatar_url": user.get("avatar_url", ""),
                }
                if user
                else None
            ),
        }

    @app.get("/auth/github")
    async def github_login(request: Request):
        if not configured():
            return RedirectResponse("/?auth=unconfigured", status_code=302)

        state = secrets.token_urlsafe(24)
        query = urllib.parse.urlencode(
            {
                "client_id": client_id,
                "redirect_uri": origin(request) + "/auth/github/callback",
                "scope": "read:user",
                "state": state,
            }
        )
        response = RedirectResponse(
            f"https://github.com/login/oauth/authorize?{query}", status_code=302
        )
        response.set_cookie(
            OAUTH_STATE_COOKIE, state, max_age=600, httponly=True, samesite="lax", secure=True
        )
        return response

    @app.get("/auth/github/callback")
    def github_callback(request: Request, code: str = "", state: str = "", error: str = ""):
        if error or not code:
            return RedirectResponse("/?auth=error", status_code=302)

        expected_state = request.cookies.get(OAUTH_STATE_COOKIE, "")
        if not expected_state or not hmac.compare_digest(state or "", expected_state):
            return RedirectResponse("/?auth=error", status_code=302)

        try:
            token_payload = github_json(
                "https://github.com/login/oauth/access_token",
                data=urllib.parse.urlencode(
                    {
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "code": code,
                        "state": state,
                    }
                ).encode("utf-8"),
            )
            access_token = token_payload.get("access_token", "")
            if not access_token:
                return RedirectResponse("/?auth=error", status_code=302)
            profile = github_json("https://api.github.com/user", token=access_token)
        except Exception:
            logger.exception("GitHub sign-in failed")
            return RedirectResponse("/?auth=error", status_code=302)

        session = {
            "login": profile.get("login", ""),
            "name": profile.get("name") or profile.get("login", ""),
            "avatar_url": profile.get("avatar_url", ""),
            "exp": int(time.time()) + SESSION_MAX_AGE,
        }

        response = RedirectResponse("/?auth=ok", status_code=302)
        response.set_cookie(
            SESSION_COOKIE,
            sign(session),
            max_age=SESSION_MAX_AGE,
            httponly=True,
            samesite="lax",
            secure=True,
        )
        response.delete_cookie(OAUTH_STATE_COOKIE)
        return response

    @app.post("/auth/logout")
    async def github_logout():
        response = JSONResponse({"status": "success", "signed_in": False})
        response.delete_cookie(SESSION_COOKIE)
        return response
