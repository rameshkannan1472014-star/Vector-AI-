import sys, re, urllib.request, json

base = "https://vector-ai-izgf.onrender.com"
def get(p):
    return urllib.request.urlopen(
        urllib.request.Request(base + p, headers={"User-Agent": "Mozilla/5.0"}), timeout=20
    )

# 1. What versioned assets does the live HTML reference?
html = get("/").read().decode("utf-8", "replace")
ver = re.findall(r"vector-ai-2\.v(\d+)\.(css|js)", html)
print("Live HTML asset filenames:", sorted(set(ver)))

# 2. Do those URLs actually resolve on the live server, and what cache headers do they return?
for v, ext in sorted(set(ver)):
    url = f"/static/vector-ai-2.v{v}.{ext}"
    try:
        r = get(url)
        print(f"  {url} -> HTTP {r.status}, cache-control={r.headers.get('cache-control', '')!r}")
    except Exception as e:
        print(f"  {url} -> ERROR {e}")

# 3. What does the /api/health version say?
try:
    health = json.loads(get("/api/health").read().decode())
    print("api/health version:", health.get("version"))
except Exception as e:
    print("api/health error:", e)

# 4. What does /api/me say?
try:
    print("api/me:", json.loads(get("/api/me").read().decode()))
except Exception as e:
    print("api/me error:", e)
