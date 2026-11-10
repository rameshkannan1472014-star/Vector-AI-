import http.cookiejar, urllib.request, urllib.parse, json

BASE = "https://vector-ai-izgf.onrender.com"

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
opener.addheaders = [("User-Agent", "Mozilla/5.0")]

def get(path, follow=True):
    return opener.open(urllib.request.Request(BASE + path), timeout=20)

# 1. Baseline: not signed in
me_before = json.loads(get("/api/me").read().decode())
print("BEFORE signin /api/me:", me_before)

# 2. Hit /auth/github and capture the redirect Location + set-cookie
req = urllib.request.Request(BASE + "/auth/github")
resp = opener.open(req, timeout=20)
loc = resp.headers.get("Location", "")
state_maybe = None
for c in jar:
    if c.name == "vector_oauth_state":
        state_maybe = c.value
print("GET /auth/github ->", resp.status, "location prefix:", loc[:90])
print("state cookie present:", bool(state_maybe))

# 3. Extract state from Location query
qs = urllib.parse.urlparse(loc).query
params = urllib.parse.parse_qs(qs)
state_from_url = params.get("state", [None])[0]
print("state from URL:", state_from_url)

# Use whichever state we have
state = state_from_url or state_maybe
print("Using state:", state[:20] + ("..." if state and len(state) > 20 else ""))

# 4. Hit the callback with a fake code but the REAL state
cb_url = f"/auth/github/callback?code=TESTCODE123&state={urllib.parse.quote(state)}"
resp = opener.open(urllib.request.Request(BASE + cb_url), timeout=20)
print("POST-like callback ->", resp.status, "location:", resp.headers.get("Location", "")[:120])

# 5. Check me again
me_after = json.loads(get("/api/me").read().decode())
print("AFTER callback /api/me:", me_after)

session_cookie = None
for c in jar:
    if c.name == "vector_session":
        session_cookie = c.value
print("vector_session cookie present:", bool(session_cookie))
if session_cookie:
    print("session cookie len:", len(session_cookie), "starts with:", session_cookie[:20])
