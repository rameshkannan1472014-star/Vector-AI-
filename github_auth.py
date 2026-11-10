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
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request


_signin_log_path = None

def _signin_log(msg: str) -> None:
    global _signin_log_path
    if _signin_log_path is None:
        try:
            _signin_log_path = os.path.join(os.path.dirname(__file__) or ".", "signin_debug.log")
        except Exception:
            _signin_log_path = "/tmp/signin_debug.log"
    try:
        with open(_signin_log_path, "a") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except Exception:
        pass
from typing import Optional

from fastapi import Body, FastAPI, Request
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
        # repo is needed so Vector can read private projects and commit files.
        "scope": "read:user repo",
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
            _signin_log(f"session_key: using SESSION_SECRET (len={len(session_secret)})")
            return session_secret.encode("utf-8")
        key = hashlib.sha256(
            f"{app_title}:{os.path.dirname(os.path.abspath(__file__))}".encode("utf-8")
        ).digest()
        _signin_log(f"session_key: using fallback key (no SESSION_SECRET set)")
        return key

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
            _signin_log(f"unsign: sig mismatch got={sig[:12]}... exp={expected[:12]}...")
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
        raw = request.cookies.get(SESSION_COOKIE, "")
        if raw:
            _signin_log(f"current_user: cookie present (len={len(raw)})")
        else:
            _signin_log(f"current_user: no {SESSION_COOKIE} cookie")
        return unsign(raw)

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
        import os as _os
        log_path = _os.path.join(_os.path.dirname(__file__) or ".", "signin_debug.log")
        def _log(msg: str) -> None:
            try:
                with open(log_path, "a") as f:
                    f.write(f"[{_os.path.basename(__file__)}] BEGIN {msg}\n")
            except Exception:
                pass
        spec = PROVIDERS[name]
        _signin_log(f"BEGIN name={name} configured={configured(name)}")
        if not configured(name):
            _signin_log(f"BEGIN redirecting to unconfigured")
            return RedirectResponse(
                f"/?auth=unconfigured&provider={name}", status_code=302
            )

        client_id = creds[name][0]
        _signin_log(f"BEGIN client_id_set={bool(client_id)} prefix={repr(client_id[:6]) if client_id else 'EMPTY'}")
        state = secrets.token_urlsafe(24)
        redirect_uri = origin(request) + f"/auth/{name}/callback"
        _signin_log(f"BEGIN redirect_uri={redirect_uri}")
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
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
        _signin_log(f"FINISH name={name} code={'SET' if code else 'EMPTY'} state={'SET' if state else 'EMPTY'} error={'SET' if error else 'none'}")
        if error or not code:
            _signin_log(f"FINISH redirecting to error: reason={'error_param' if error else 'no_code'}")
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
            _signin_log(f"FINISH token_keys={list(payload.keys())} access_token={'SET' if access_token else 'EMPTY'} scope={payload.get('scope', 'none')}")
            if not access_token:
                _signin_log(f"FINISH redirecting to error: no_access_token")
                return RedirectResponse(f"/?auth=error&provider={name}", status_code=302)
            _signin_log("FINISH fetching profile...")
            profile = remote_json(spec["profile"], token=access_token)
            _signin_log(f"FINISH profile: login={profile.get('login', 'none')} name={profile.get('name', 'none')}")

        except Exception:
            logger.exception("%s sign-in failed", spec["label"])
            _signin_log(f"FINISH exception: {spec['label']} sign-in failed")
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
            "token": access_token,
            "scope": str(payload.get("scope", "") or ""),
        }
        cookie_val = sign(session)
        _signin_log(f"FINISH cookie_len={len(cookie_val)} scope={session['scope']}")

        response = RedirectResponse(f"/?auth=ok&provider={name}", status_code=302)
        response.set_cookie(
            SESSION_COOKIE,
            cookie_val,
            max_age=SESSION_MAX_AGE,
            httponly=True,
            samesite="lax",
            secure=is_secure(request),
        )
        response.delete_cookie(STATE_COOKIE)
        _signin_log(f"FINISH cookie_set={SESSION_COOKIE} -> ok")
        return response

    @app.get("/api/me")
    async def api_me(request: Request):
        user = current_user(request)
        return {
            "signed_in": bool(user),
            "auth_configured": any(configured(name) for name in PROVIDERS),
            "providers": {name: configured(name) for name in PROVIDERS},
            "github_ready": bool(user and user.get("provider") == "github" and user.get("token")),
            "user": (
                {
                    "login": user.get("login", ""),
                    "name": user.get("name", ""),
                    "avatar_url": user.get("avatar_url", ""),
                    "provider": user.get("provider", "github"),
                    "has_github_token": bool(user.get("token")),
                }
                if user
                else None
            ),
        }

    @app.get("/api/signin-log")
    async def signin_log(request: Request):
        return {
            "status": "success",
            "lines": list(_signin_log_lines),
        }

    # ------------------------------------------------------------------
    # GitHub projects: read what a person works on, and edit their files
    # ------------------------------------------------------------------
    gh_cache: dict = {}
    REPO_PATTERN = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")

    class GitHubError(Exception):
        def __init__(self, status: int, message: str):
            super().__init__(message)
            self.status = status
            self.message = message

    def github_token(request: Request) -> str:
        user = current_user(request)
        if not user or user.get("provider") != "github":
            return ""
        return str(user.get("token") or "").strip()

    def gh_call(method: str, path: str, token: str, body: Optional[dict] = None):
        url = "https://api.github.com" + path
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "vector-ai",
            "X-GitHub-Api-Version": "2022-11-28",
            "Authorization": "Bearer " + token,
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        request_object = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request_object, timeout=25) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            if error.code == 401:
                raise GitHubError(401, "GitHub sign-in has expired. Sign in again to reconnect.")
            if error.code in (403, 429):
                raise GitHubError(403, "GitHub said no (rate limit or missing permission). Sign in again to grant file access.")
            if error.code == 404:
                raise GitHubError(404, "GitHub could not find that repository or file.")
            raise GitHubError(error.code, "GitHub returned an error (" + str(error.code) + ").")
        except Exception:
            raise GitHubError(502, "Could not reach GitHub. Try again in a moment.")
        if not raw.strip():
            return {}
        try:
            return json.loads(raw)
        except ValueError:
            raise GitHubError(502, "GitHub sent something unreadable.")

    def gh_reply(status: int, payload: dict):
        return JSONResponse(payload, status_code=status)

    def gh_needs_signin(message: str = "Sign in with GitHub to connect your projects."):
        return gh_reply(401, {"status": "error", "error": "signin", "message": message})

    def same_origin(request: Request) -> bool:
        host = (request.headers.get("host") or "").split(",")[0].strip().lower()
        source = (request.headers.get("origin") or request.headers.get("referer") or "").strip()
        if not source:
            return True
        return source.lower().split("://")[-1].split("/")[0] == host

    def clean_repo(value: str) -> str:
        repo = str(value or "").strip().strip("/")
        if not REPO_PATTERN.match(repo):
            raise GitHubError(400, "Pick a repository first.")
        return repo

    def describe_events(events: list) -> list:
        rows = []
        for event in events or []:
            kind = str(event.get("type", ""))
            repo = str((event.get("repo") or {}).get("name", ""))
            payload = event.get("payload") or {}
            when = str(event.get("created_at", ""))[:10]
            detail = ""
            if kind == "PushEvent":
                commits = payload.get("commits") or []
                count = int(payload.get("size") or len(commits) or 0)
                first = (commits[0].get("message") or "").split("\n")[0] if commits else ""
                detail = "pushed " + str(count) + " commit" + ("s" if count != 1 else "") + ((": " + first[:90]) if first else "")
            elif kind == "PullRequestEvent":
                pr = payload.get("pull_request") or {}
                detail = str(payload.get("action", "")) + " a pull request: " + str(pr.get("title", ""))[:90]
            elif kind == "IssuesEvent":
                issue = payload.get("issue") or {}
                detail = str(payload.get("action", "")) + " an issue: " + str(issue.get("title", ""))[:90]
            elif kind == "CreateEvent":
                detail = "created " + str(payload.get("ref_type", "something")) + " " + str(payload.get("ref", ""))[:60]
            elif kind == "WatchEvent":
                detail = "starred a repository"
            elif kind == "ForkEvent":
                detail = "forked a repository"
            else:
                detail = kind.replace("Event", "").lower()
            if detail:
                rows.append({"type": kind, "repo": repo, "when": when, "detail": detail.strip()})
        return rows[:20]

    def build_summary(profile: dict, repos: list, events: list) -> dict:
        public_repos = int(profile.get("public_repos") or 0)
        languages: dict = {}
        for repo in repos:
            language = repo.get("language")
            if language:
                languages[language] = languages.get(language, 0) + 1
        top_languages = sorted(languages.items(), key=lambda item: -item[1])[:5]

        recent = describe_events(events)
        names = [r.get("name", "") for r in repos[:8]]

        pieces = []
        display = profile.get("name") or profile.get("login") or "this person"
        pieces.append(display + " (@" + str(profile.get("login", "")) + ") on GitHub")
        if profile.get("bio"):
            pieces.append(str(profile.get("bio"))[:200])
        pieces.append(str(public_repos) + " public repositories")
        if top_languages:
            pieces.append("mostly " + ", ".join(name + " (" + str(count) + ")" for name, count in top_languages))
        if names:
            pieces.append("active projects: " + ", ".join(names))
        if recent:
            pieces.append("recent work: " + "; ".join(item["repo"] + " — " + item["detail"] for item in recent[:5]))

        return {
            "profile": {
                "login": profile.get("login", ""),
                "name": profile.get("name") or profile.get("login", ""),
                "avatar_url": profile.get("avatar_url", ""),
                "bio": profile.get("bio") or "",
                "company": profile.get("company") or "",
                "location": profile.get("location") or "",
                "blog": profile.get("blog") or "",
                "public_repos": public_repos,
                "followers": int(profile.get("followers") or 0),
                "created_at": str(profile.get("created_at", ""))[:10],
                "html_url": profile.get("html_url", ""),
            },
            "repos": [
                {
                    "name": repo.get("name", ""),
                    "full_name": repo.get("full_name", ""),
                    "private": bool(repo.get("private")),
                    "description": repo.get("description") or "",
                    "language": repo.get("language") or "",
                    "stars": int(repo.get("stargazers_count") or 0),
                    "updated_at": str(repo.get("updated_at", ""))[:10],
                    "default_branch": repo.get("default_branch") or "main",
                    "html_url": repo.get("html_url", ""),
                }
                for repo in repos[:60]
            ],
            "languages": [{"name": name, "count": count} for name, count in top_languages],
            "recent": recent,
            "summary_text": ". ".join(pieces)[:1400],
        }

    @app.get("/api/github/summary")
    def github_summary(request: Request, refresh: int = 0):
        token = github_token(request)
        if not token:
            return gh_needs_signin()

        user = current_user(request) or {}
        login = str(user.get("login", ""))
        now = time.time()
        cached = gh_cache.get(login)
        if cached and not refresh and now - cached["at"] < 120:
            return cached["data"]

        try:
            profile = gh_call("GET", "/user", token)
            repos = gh_call("GET", "/user/repos?per_page=100&sort=updated&affiliation=owner,collaborator", token)
            events = gh_call("GET", "/users/" + urllib.parse.quote(login) + "/events/public?per_page=30", token)
        except GitHubError as error:
            return gh_reply(error.status, {"status": "error", "message": error.message})

        payload = build_summary(profile, repos if isinstance(repos, list) else [], events if isinstance(events, list) else [])
        payload["status"] = "success"
        gh_cache[login] = {"at": now, "data": payload}
        return payload

    @app.get("/api/github/tree")
    def github_tree(request: Request, repo: str = "", branch: str = ""):
        token = github_token(request)
        if not token:
            return gh_needs_signin()
        try:
            full = clean_repo(repo)
            target = branch.strip()
            if not target:
                info = gh_call("GET", "/repos/" + full, token)
                target = str(info.get("default_branch") or "main")
            data = gh_call(
                "GET",
                "/repos/" + full + "/git/trees/" + urllib.parse.quote(target) + "?recursive=1",
                token,
            )
        except GitHubError as error:
            return gh_reply(error.status, {"status": "error", "message": error.message})

        files = [
            {"path": item.get("path", ""), "size": int(item.get("size") or 0)}
            for item in (data.get("tree") or [])
            if item.get("type") == "blob"
        ]
        files.sort(key=lambda item: item["path"])
        return {
            "status": "success",
            "repo": full,
            "branch": target,
            "count": len(files),
            "truncated": len(files) > 600,
            "files": files[:600],
        }

    @app.get("/api/github/file")
    def github_file(request: Request, repo: str = "", path: str = ""):
        token = github_token(request)
        if not token:
            return gh_needs_signin()
        clean_path = str(path or "").strip().strip("/")
        if not clean_path:
            return gh_reply(400, {"status": "error", "message": "Pick a file first."})
        try:
            full = clean_repo(repo)
            data = gh_call(
                "GET",
                "/repos/" + full + "/contents/" + urllib.parse.quote(clean_path),
                token,
            )
        except GitHubError as error:
            return gh_reply(error.status, {"status": "error", "message": error.message})

        if isinstance(data, list):
            return gh_reply(400, {"status": "error", "message": "That is a folder, not a file."})

        size = int(data.get("size") or 0)
        if size > 400000:
            return gh_reply(413, {"status": "error", "message": "That file is too big for editing here (" + str(size) + " bytes)."})

        try:
            text = base64.b64decode(data.get("content") or "").decode("utf-8", "replace")
        except (ValueError, binascii.Error):
            return gh_reply(415, {"status": "error", "message": "That file is not plain text."})

        return {
            "status": "success",
            "repo": full,
            "path": clean_path,
            "sha": data.get("sha", ""),
            "size": size,
            "content": text,
        }

    @app.post("/api/github/save")
    def github_save(request: Request, payload: dict = Body(...)):
        token = github_token(request)
        if not token:
            return gh_needs_signin()
        if not same_origin(request):
            return gh_reply(403, {"status": "error", "message": "That save did not come from this site."})

        clean_path = str(payload.get("path") or "").strip().strip("/")
        content = payload.get("content")
        message = str(payload.get("message") or "").strip() or "Update " + (clean_path or "file") + " with Vector AI"

        if not clean_path:
            return gh_reply(400, {"status": "error", "message": "Pick a file first."})
        if not isinstance(content, str):
            return gh_reply(400, {"status": "error", "message": "There is no new file content to save."})
        if len(content.encode("utf-8")) > 900000:
            return gh_reply(413, {"status": "error", "message": "That file is too big to write."})

        try:
            full = clean_repo(str(payload.get("repo") or ""))
            sha = ""
            try:
                existing = gh_call(
                    "GET",
                    "/repos/" + full + "/contents/" + urllib.parse.quote(clean_path),
                    token,
                )
                if isinstance(existing, dict):
                    sha = str(existing.get("sha") or "")
            except GitHubError as error:
                if error.status != 404:
                    raise

            body = {
                "message": message[:250],
                "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            }
            if sha:
                body["sha"] = sha

            result = gh_call(
                "PUT",
                "/repos/" + full + "/contents/" + urllib.parse.quote(clean_path),
                token,
                body,
            )
        except GitHubError as error:
            return gh_reply(error.status, {"status": "error", "message": error.message})

        commit = result.get("commit") or {}
        gh_cache.pop(str((current_user(request) or {}).get("login", "")), None)
        return {
            "status": "success",
            "repo": full,
            "path": clean_path,
            "created": not sha,
            "commit": str(commit.get("sha") or ""),
            "url": str(commit.get("html_url") or ""),
        }

    @app.get("/auth/github")
    async def github_login(request: Request):
        return begin(request, "github")

    @app.get("/auth/github/callback")
    def github_callback(request: Request, code: str = "", state: str = "", error: str = ""):
        _signin_log(f"CALLBACK github: code={'SET' if code else 'EMPTY'} state={'SET' if state else 'EMPTY'} error={'SET' if error else 'none'} cookies={list(request.cookies.keys())}")
        return finish(request, "github", code, state, error)

    @app.get("/auth/google")
    async def google_login(request: Request):
        return begin(request, "google")

    @app.get("/auth/google/callback")
    def google_callback(request: Request, code: str = "", state: str = "", error: str = ""):
        _signin_log(f"CALLBACK google: code={'SET' if code else 'EMPTY'} state={'SET' if state else 'EMPTY'} error={'SET' if error else 'none'} cookies={list(request.cookies.keys())}")
        return finish(request, "google", code, state, error)

    # ------------------------------------------------------------------
    # GitHub: create repositories with a license and .gitignore template
    # ------------------------------------------------------------------
    KNOWN_LICENSE_TEMPLATES = [
        "agpl-3.0",
        "apache-2.0",
        "artistic-2.0",
        "bsd-2-clause",
        "bsd-3-clause",
        "cc0-1.0",
        "eupl-1.2",
        "gpl-2.0",
        "gpl-3.0",
        "isc",
        "lgpl-3.0",
        "mit",
        "mpl-2.0",
        "unlicense",
        "wtfpl",
    ]

    KNOWN_GITIGNORE_TEMPLATES = [
        "Android",
        "C",
        "C++",
        "CakePHP",
        "CFML",
        "CMake",
        "CUDA",
        "D",
        "Django",
        "Dockerfile",
        "Elixir",
        "Elm",
        "Erlang",
        "Express",
        "Gatsby",
        "GitBook",
        "Go",
        "Gradle",
        "Haskell",
        "HTML",
        "Java",
        "JavaScript",
        "Jupyter Notebook",
        "Kotlin",
        "LaTeX",
        "Laravel",
        "Less",
        "Lisp",
        "Lua",
        "Magento",
        "MATLAB",
        "Metaparticle",
        "Python",
        "React",
        "Ruby",
        "Rust",
        "Sass",
        "Scala",
        "Swift",
        "Vue",
        "Zephyr",
        "Node",
        "deno",
    ]

    @app.get("/api/github/licenses")
    def github_licenses(request: Request):
        token = github_token(request)
        if not token:
            return gh_needs_signin()
        try:
            data = gh_call("GET", "/licenses", token)
        except GitHubError as error:
            return gh_reply(error.status, {"status": "error", "message": error.message})
        if not isinstance(data, list):
            return gh_reply(502, {"status": "error", "message": "GitHub sent an unexpected license list."})
        licenses = []
        for item in data:
            name = str(item.get("name", "")).strip()
            key = str(item.get("spdx_id", "")).strip()
            if name and key and key != "NOASSERTION":
                licenses.append({
                    "key": key,
                    "name": name,
                    "featured": bool(item.get("featured", False)),
                    "url": str(item.get("url", "")) or "",
                })
        licenses.sort(key=lambda item: item["name"].lower())
        return {"status": "success", "licenses": licenses}

    @app.post("/api/github/create-repo")
    def github_create_repo(request: Request, payload: dict = Body(...)):
        token = github_token(request)
        if not token:
            return gh_needs_signin()
        if not same_origin(request):
            return gh_reply(403, {"status": "error", "message": "That create request did not come from this site."})

        name = str(payload.get("name") or "").strip()
        description = str(payload.get("description") or "").strip()
        private = bool(payload.get("private", False))
        license_template = str(payload.get("license_template") or "").strip()
        gitignore_template = str(payload.get("gitignore_template") or "").strip()
        auto_init = bool(payload.get("auto_init", True))
        has_issues = bool(payload.get("has_issues", True))
        has_wiki = bool(payload.get("has_wiki", False))

        if not name:
            return gh_reply(400, {"status": "error", "message": "Pick a repository name first."})
        if "/" in name or "\\" in name or name in (".", "..") or not re.match(r"^[A-Za-z0-9._-]+$", name):
            return gh_reply(400, {"status": "error", "message": "Repository names can only use letters, numbers, dots, hyphens and underscores."})
        if len(name) > 100:
            return gh_reply(400, {"status": "error", "message": "Repository name is too long (max 100 characters)."})
        if license_template and license_template not in KNOWN_LICENSE_TEMPLATES:
            return gh_reply(400, {"status": "error", "message": "That license is not recognised. Try a common one like mit or apache-2.0."})
        if gitignore_template and gitignore_template not in KNOWN_GITIGNORE_TEMPLATES:
            return gh_reply(400, {"status": "error", "message": "That gitignore template is not recognised."})

        try:
            body_payload = {
                "name": name,
                "description": description[:500] if description else "",
                "private": private,
                "auto_init": auto_init,
                "has_issues": has_issues,
                "has_wiki": has_wiki,
            }
            if license_template:
                body_payload["license_template"] = license_template
            if gitignore_template:
                body_payload["gitignore_template"] = gitignore_template

            result = gh_call("POST", "/user/repos", token, body_payload)
        except GitHubError as error:
            msg = error.message
            if error.status == 422 and "name already exists" in msg.lower():
                msg = "A repository with that name already exists. Try a different name."
            return gh_reply(error.status, {"status": "error", "message": msg})

        repo = result if isinstance(result, dict) else {}
        return {
            "status": "success",
            "repo": str(repo.get("full_name", name)),
            "name": str(repo.get("name", name)),
            "private": bool(repo.get("private", private)),
            "description": str(repo.get("description") or ""),
            "html_url": str(repo.get("html_url") or ""),
            "clone_url": str(repo.get("clone_url") or ""),
            "default_branch": str(repo.get("default_branch") or "main"),
            "license": ({"key": str(repo.get("license", {}).get("spdx_id", "")), "name": str(repo.get("license", {}).get("name", ""))} if repo.get("license") else None),
            "created_at": str(repo.get("created_at", ""))[:10],
        }

    @app.post("/auth/logout")
    async def github_logout():
        response = JSONResponse({"status": "success", "signed_in": False})
        response.delete_cookie(SESSION_COOKIE)
        return response
