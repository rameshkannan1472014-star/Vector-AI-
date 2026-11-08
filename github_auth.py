"""Optional GitHub and Google sign-in for Vector AI.

Kept in its own module so the large server.py does not have to be rewritten.
Both app.py and server.py install it, and install() is idempotent, so it does
not matter which file your host starts.

Switched on by setting environment variables on the server:

    GITHUB_CLIENT_ID      from https://github.com/settings/developers
    GITHUB_CLIENT_SECRET  (same page, Generate a new client secret)
      callback URL: https://YOUR-APP.onrender.com/auth/github/callback

    GOOGLE_CLIENT_ID      from https://console.cloud.google.com/apis/credentials
    GOOGLE_CLIENT_SECRET
      callback URL: https://YOUR-APP.onrender.com/auth/google/callback

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

logger = logging.getLogger("vector.signin")

SESSION_COOKIE = "vector_session"
STATE_COOKIE = "vector_oauth_state"
SESSION_MAX_AGE = 60 * 60 * 24 * 30

PROVIDERS = {
    "github": {
        "label": "GitHub",
        "authorize": "https://github.com/login/oauth/authorize",
        "token": "https://github.com/login/oauth/access_token",
        "profile": "https://api.github.com/user",
        "scope": "read:user",
        "id_env": "GITHUB_CLIENT_ID",
        "secret_env": "GITHUB_CLIENT_SECRET",
    },
    "google": {
        "label": "Google",
        "authorize": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "profile": "https://www.googleapis.com/oauth2/v3/userinfo",
        "scope": "openid email profile",
        "id_env": "GOOGLE_CLIENT_ID",
        "secret_env": "GOOGLE_CLIENT_SECRET",
    },
}


def install(app: FastAPI, app_title: str = "Vector AI") -> None:
    # app.py and server.py both call this. Only wire the routes once.
    if getattr(app.state, "vector_signin_installed", False):
        return
    app.state.vector_signin_installed = True

    creds = {}
    for name, spec in PROVIDERS.items():
        creds[name] = (
            os.getenv(spec["id_env"], "").strip(),
            os.getenv(spec["secret_env"], "").strip(),
        )
    session_secret = (os.getenv("SESSION_SECRET") or os.getenv("SECRET_KEY") or "").strip()

    def configured(name: str) -> bool:
        client_id, client_secret = creds.get(name, ("", ""))
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

    def is_secure(request: Request) -> bool:
        return origin(request).startswith("https://")

    def remote_json(url: str, data: Optional[bytes] = None, token: Optional[str] = None) -> dict:
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

    def begin(request: Request, name: str) -> RedirectResponse:
        spec = PROVIDERS[name]
        if not configured(name):
            return RedirectResponse(
                f"/?auth=unconfigured&provider={name}", status_code=302
            )

        client_id = creds[name][0]
        state = secrets.token_urlsafe(24)
        params = {
            "client_id": client_id,
            "redirect_uri": origin(request) + f"/auth/{name}/callback",
            "scope": spec["scope"],
            "state": state,
        }
        if name == "google":
            params["response_type"] = "code"
            params["access_type"] = "online"
            params["prompt"] = "select_account"

        response = RedirectResponse(
            spec["authorize"] + "?" + urllib.parse.urlencode(params), status_code=302
        )
        response.set_cookie(
            STATE_COOKIE,
            name + ":" + state,
            max_age=600,
            httponly=True,
            samesite="lax",
            secure=is_secure(request),
        )
        return response

    def finish(request: Request, name: str, code: str, state: str, error: str) -> RedirectResponse:
        if error or not code:
            return RedirectResponse(f"/?auth=error&provider={name}", status_code=302)

        expected = request.cookies.get(STATE_COOKIE, "")
        if expected.startswith(name + ":"):
            expected = expected.split(":", 1)[1]
        else:
            expected = ""
        if not expected or not hmac.compare_digest(state or "", expected):
            return RedirectResponse(f"/?auth=error&provider={name}", status_code=302)

        spec = PROVIDERS[name]
        client_id, client_secret = creds[name]
        redirect_uri = origin(request) + f"/auth/{name}/callback"

        try:
            form = {
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            }
            if name == "github":
                form["state"] = state
            else:
                form["grant_type"] = "authorization_code"

            payload = remote_json(
                spec["token"], data=urllib.parse.urlencode(form).encode("utf-8")
            )
            access_token = payload.get("access_token", "")
            if not access_token:
                return RedirectResponse(f"/?auth=error&provider={name}", status_code=302)
            profile = remote_json(spec["profile"], token=access_token)
        except Exception:
            logger.exception("%s sign-in failed", spec["label"])
            return RedirectResponse(f"/?auth=error&provider={name}", status_code=302)

        if name == "github":
            login = profile.get("login", "")
            display = profile.get("name") or login
            avatar = profile.get("avatar_url", "")
        else:
            email = profile.get("email", "")
            login = email.split("@")[0] if email else ""
            display = profile.get("name") or login or email
            avatar = profile.get("picture", "")

        session = {
            "login": login,
            "name": display,
            "avatar_url": avatar,
            "provider": name,
            "exp": int(time.time()) + SESSION_MAX_AGE,
        }

        response = RedirectResponse(f"/?auth=ok&provider={name}", status_code=302)
        response.set_cookie(
            SESSION_COOKIE,
            sign(session),
            max_age=SESSION_MAX_AGE,
            httponly=True,
            samesite="lax",
            secure=is_secure(request),
        )
        response.delete_cookie(STATE_COOKIE)
        return response

    @app.get("/api/me")
    async def api_me(request: Request):
        user = current_user(request)
        return {
            "signed_in": bool(user),
            "auth_configured": any(configured(name) for name in PROVIDERS),
            "providers": {name: configured(name) for name in PROVIDERS},
            "user": (
                {
                    "login": user.get("login", ""),
                    "name": user.get("name", ""),
                    "avatar_url": user.get("avatar_url", ""),
                    "provider": user.get("provider", "github"),
                }
                if user
                else None
            ),
        }

    @app.get("/auth/github")
    async def github_login(request: Request):
        return begin(request, "github")

    @app.get("/auth/github/callback")
    def github_callback(request: Request, code: str = "", state: str = "", error: str = ""):
        return finish(request, "github", code, state, error)

    @app.get("/auth/google")
    async def google_login(request: Request):
        return begin(request, "google")

    @app.get("/auth/google/callback")
    def google_callback(request: Request, code: str = "", state: str = "", error: str = ""):
        return finish(request, "google", code, state, error)

    @app.post("/auth/logout")
    async def github_logout():
        response = JSONResponse({"status": "success", "signed_in": False})
        response.delete_cookie(SESSION_COOKIE)
        return response
