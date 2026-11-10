# Vector AI — 2.0 Rebuild Brief

Repo: `rameshkannan1472014-star/Vector-AI-`
Live app: https://vector-ai-izgf.onrender.com
Prepared: 7 Nov 2026

---

## 1. What is actually in the repo today

| File | Size | What it is |
|---|---|---|
| `index.html` | 375 KB | The entire frontend — markup, CSS, all JavaScript, every screen — in one file |
| `server.py` | 85 KB | The entire backend — FastAPI app, every route, provider calls, prompt logic, SQLite |
| `requirements.txt` | 56 B | fastapi, uvicorn[standard], pydantic, google-genai, openai |
| `Dockerfile` | 431 B | python:3.11-slim → `uvicorn server:app` on port 7860 |
| `Render_Setup.txt` | 1.5 KB | Deployment notes (see providers below) |
| `manifest.json` | 545 B | PWA manifest — "Vector AI", standalone, dark `#050811` |
| `sw.js` | 1.8 KB | Service worker for offline/install |
| `static/`, `icons/` | — | Assets, including `vector-ai.svg` |

**Product:** "Engineer AI" — an AI engineering assistant for coding, debugging, code review, technical explanations, engineering projects, AI agents/workflows, and technical diagrams.

**Providers currently wired up** (from `Render_Setup.txt`):
- **Groq** — primary chat model (`GROQ_API_KEY`), chosen for speed
- **Gemini** — fallback chat provider (`GEMINI_API_KEY`), selected via `PRIMARY_CHAT_PROVIDER`
- **Cloudflare Workers AI** — image generation (`CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN`)
- **Resend** — feedback email, because Render's free tier blocks SMTP ports
- Video generation was deliberately removed

**Already tried:** there is a branch called `vector-ai-2.0-workspace`. It is a near-identical copy of `main` — the same 375 KB `index.html` and the same 85 KB `server.py`, differing by about 8 bytes. So the last "2.0" was a re-upload, not a rebuild.

---

## 2. Why it keeps needing a rebuild

The product is not the problem. The packaging is.

- **One 375 KB HTML file.** You cannot test one screen without loading all of them. One stray edit breaks something unrelated and you only find out when the whole app is blank.
- **One 85 KB Python file.** Every endpoint, every provider, every prompt lives in the same namespace. Changing the image endpoint risks the chat endpoint.
- **No tests, no layering.** There is no way to answer "does the chat still stream?" except by manually clicking around.
- **Provider logic is inline.** Groq, Gemini and Cloudflare are called from wherever they are needed, so a fallback is a copy-paste rather than one line.
- **`Render_Setup.txt` instead of config.** Deployment knowledge lives in a text file a human has to read, not in code that runs.

That is the cycle: each new feature makes the monolith more fragile, until starting over feels easier than changing anything.

---

## 3. Target structure

```
vector-ai/
├── server/
│   ├── app.py                  # creates the FastAPI app, mounts routers, nothing else
│   ├── config.py               # all env vars in one typed settings object
│   ├── routes/
│   │   ├── chat.py             # streaming chat
│   │   ├── code.py             # generate / review / debug
│   │   ├── images.py           # image generation
│   │   ├── feedback.py         # Resend email
│   │   └── health.py           # provider status
│   ├── providers/
│   │   ├── base.py             # one ChatProvider interface
│   │   ├── groq.py
│   │   ├── gemini.py
│   │   └── cloudflare.py
│   ├── services/
│   │   ├── prompts.py          # prompt templates, versioned
│   │   ├── history.py          # conversation storage
│   │   └── routing.py          # primary → fallback logic in one place
│   └── db/
│       ├── models.py
│       └── session.py
├── web/
│   ├── index.html              # ~50 lines: shell + one script tag
│   ├── src/
│   │   ├── main.js
│   │   ├── api/client.js       # every fetch in one file
│   │   ├── ui/                 # one module per screen
│   │   └── styles/
│   └── manifest.json, sw.js, icons/
├── tests/
│   └── test_health.py, test_chat.py
├── .env.example                # every variable, documented, no secrets
├── Dockerfile
├── requirements.txt
└── README.md
```

Three rules that make 2.0 different:

1. **`index.html` stays under ~100 lines.** All behaviour lives in `web/src/`.
2. **Every provider implements the same interface.** Switching or falling back is a one-line config change, never a code change.
3. **Every milestone ends deployable.** No big-bang rewrite; the app works on day one and gains features after that.

---

## 4. Phased plan

**Milestone 1 — Skeleton + streaming chat** (the hard part, done first)
- FastAPI app factory, settings object, `/api/health` reporting which providers are configured
- `ChatProvider` interface + Groq implementation + Gemini fallback
- Streaming chat end to end, frontend split into modules
- Health test + one chat test
- *Deployable: it is a working, faster, reliable version of what you have.*

**Milestone 2 — Port the features, one at a time**
- Code generation, code review, debugging, explanations
- Diagrams
- Image generation via Cloudflare
- Each one is a router + a UI module; each one lands with its own test

**Milestone 3 — Memory and feedback**
- Conversation history in SQLite (schema, migrations, per-conversation load)
- Feedback endpoint sending through Resend
- History limits as settings (`HISTORY_MESSAGES`, `HISTORY_CHARS`), not magic numbers

**Milestone 4 — Ship it properly**
- PWA: manifest, service worker, icons
- Docker image, `.env.example`, README that someone else could follow
- Render deploy + uptime ping on `/api/health`
- Key rotation note: any key that ever appeared in a screenshot gets rotated

---

## 5. Environment variables

```bash
# Chat
GROQ_API_KEY=
GEMINI_API_KEY=
PRIMARY_CHAT_PROVIDER=groq          # groq | gemini
GROQ_REASONING_EFFORT=low           # low | medium | high

# Images
CLOUDFLARE_ACCOUNT_ID=
CLOUDFLARE_API_TOKEN=

# Feedback
RESEND_API_KEY=
FEEDBACK_TO_EMAIL=

# History
HISTORY_MESSAGES=10
HISTORY_CHARS=1200
```

Never commit real values. `.env` stays untracked; `.env.example` holds the names only.

---

## 6. Decisions to make before starting

1. **Frontend stack** — keep vanilla JS but modularised (lowest risk, fastest), or move to React + Vite (more work, easier to grow)?
2. **Which features make the cut?** Porting everything at once recreates the monolith. Pick the three or four that matter and leave the rest until after launch.
3. **Single user or accounts?** If it is your tool, skip auth entirely for now — it is a large amount of work and can be added later behind the same routes.
4. **Repo strategy** — rebuild on a fresh branch in the same repo, or start a clean repo and keep this one untouched as the reference?

---

## 7. Paste-ready prompt for Code mode

> Rebuild my repo `rameshkannan1472014-star/Vector-AI-` (currently: a 375 KB `index.html` and an 85 KB `server.py` FastAPI monolith, deployed on Render, Groq primary with Gemini fallback, Cloudflare images, Resend feedback).
>
> Goal: a maintainable Vector AI 2.0 that works from the first commit. Start with Milestone 1 only — FastAPI app factory with a typed settings module, a `ChatProvider` interface with Groq and Gemini implementations plus automatic fallback, a streaming chat endpoint, `/api/health` reporting provider status, and a modular frontend (`web/src/`) so `index.html` stays under 100 lines. Add tests for health and chat, an `.env.example`, and keep the Dockerfile on port 7860.
>
> Non-negotiables: never inline provider calls in routes, never grow `index.html` past 100 lines, no secrets in the repo, and the app must run end to end before moving to the next milestone. Read the existing `server.py` for behaviour to preserve, but do not copy its structure.
