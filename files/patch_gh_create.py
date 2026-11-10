p = "github_auth.py"
s = open(p, encoding="utf-8").read()

new_block = '''    # ------------------------------------------------------------------
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
    def github_create_repo(request: Request, payload: dict = Body(...)):\n        token = github_token(request)\n        if not token:\n            return gh_needs_signin()\n        if not same_origin(request):\n            return gh_reply(403, {"status": "error", "message": "That create request did not come from this site."})\n\n        name = str(payload.get("name") or "").strip()\n        description = str(payload.get("description") or "").strip()\n        private = bool(payload.get("private", False))\n        license_template = str(payload.get("license_template") or "").strip()\n        gitignore_template = str(payload.get("gitignore_template") or "").strip()\n        auto_init = bool(payload.get("auto_init", True))\n        has_issues = bool(payload.get("has_issues", True))\n        has_wiki = bool(payload.get("has_wiki", False))\n\n        if not name:\n            return gh_reply(400, {"status": "error", "message": "Pick a repository name first."})\n        if "/" in name or "\\\\" in name or name in (".", "..") or not re.match(r"^[A-Za-z0-9._-]+$", name):\n            return gh_reply(400, {"status": "error", "message": "Repository names can only use letters, numbers, dots, hyphens and underscores."})\n        if len(name) > 100:\n            return gh_reply(400, {"status": "error", "message": "Repository name is too long (max 100 characters)."})\n        if license_template and license_template not in KNOWN_LICENSE_TEMPLATES:\n            return gh_reply(400, {"status": "error", "message": "That license is not recognised. Try a common one like mit or apache-2.0."})\n        if gitignore_template and gitignore_template not in KNOWN_GITIGNORE_TEMPLATES:\n            return gh_reply(400, {"status": "error", "message": "That gitignore template is not recognised."})\n\n        try:\n            body_payload = {\n                "name": name,\n                "description": description[:500] if description else "",\n                "private": private,\n                "auto_init": auto_init,\n                "has_issues": has_issues,\n                "has_wiki": has_wiki,\n            }\n            if license_template:\n                body_payload["license_template"] = license_template\n            if gitignore_template:\n                body_payload["gitignore_template"] = gitignore_template\n\n            result = gh_call("POST", "/user/repos", token, body_payload)\n        except GitHubError as error:\n            msg = error.message\n            if error.status == 422 and "name already exists" in msg.lower():\n                msg = "A repository with that name already exists. Try a different name."\n            return gh_reply(error.status, {"status": "error", "message": msg})\n\n        repo = result if isinstance(result, dict) else {}\n        return {\n            "status": "success",\n            "repo": str(repo.get("full_name", name)),\n            "name": str(repo.get("name", name)),\n            "private": bool(repo.get("private", private)),\n            "description": str(repo.get("description") or ""),\n            "html_url": str(repo.get("html_url") or ""),\n            "clone_url": str(repo.get("clone_url") or ""),\n            "default_branch": str(repo.get("default_branch") or "main"),\n            "license": ({"key": str(repo.get("license", {}).get("spdx_id", "")), "name": str(repo.get("license", {}).get("name", ""))} if repo.get("license") else None),\n            "created_at": str(repo.get("created_at", ""))[:10],\n        }

'''

old_end = '''    @app.post("/auth/logout")
    async def github_logout():
        response = JSONResponse({"status": "success", "signed_in": False})
        response.delete_cookie(SESSION_COOKIE)
        return response'''

assert old_end in s, "end of github_auth.py not found"
# Escape the new_block Python string for literal insertion
import re as _re
# new_block contains \n literals that we want as actual newlines
new_block_literal = new_block.encode().decode("unicode_escape") if "\\n" in new_block else new_block
# Actually, the file content has real newlines already — let me just use it as-is
# The issue is the heredoc-escaped content. Let me reconstruct more carefully.
# Actually the write_file already wrote it with literal \n sequences. Let me just
# read it back and fix.

print("placeholder — will fix below")
