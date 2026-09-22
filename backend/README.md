# Pigulkin platform — backend + MCP

This is the **backend of the GetPostach project** (it lives in the same repo, under
`backend/`; the static frontend is the repo root, served by GitHub Pages). It powers
the «Пігулькін» procurement assistant (Alliance Group 95: pharma / cosmetics /
veterinary). Provides:

1. **FastAPI backend** — supplier REST API + `POST /api/chat` (Claude with tool-calling over the DB).
2. **PostgreSQL** — supplier DB, imported from the `CPHI_MILAN` JSONL sources.
3. **Two MCP servers** — `procurement` (supplier tools) and `email` (Gmail drafts) for the team's Claude Desktop / Claude Code and for the backend.

Stack: Python 3.12 · FastAPI · SQLAlchemy 2 · Anthropic Claude (`claude-sonnet-4-6`) · MCP 2 · Docker · Caddy.

```
frontend (GetPostach) ──HTTP──▶ FastAPI /api/chat ──▶ Claude (tool-calling)
                                      │                    │ tools
                                      ▼                    ▼
                                  Postgres ◀── app.tools.registry ──▶ MCP servers
                                      ▲                                   (procurement, email)
                              import_data.py ◀── CPHI_MILAN/data/*.jsonl
```

The chat and the procurement MCP share **one** tool implementation (`app/tools/registry.py`).

## Layout

```
backend/
  app/
    config.py        settings (.env)
    db.py            engine/session (SQLite local, Postgres prod)
    models.py        SQLAlchemy tables (ranking, companies, contacts, quotes, thread_states, exhibitors, profiles)
    services/
      queries.py     search / detail / benchmark / exhibitors / stats
      prep.py        prepare_meeting_brief (exhibition prep)
      chat.py        Claude tool-calling loop (Пігулькін)
    tools/registry.py  tool schemas + dispatch (shared by chat + MCP)
    routers/         suppliers.py, chat.py
    main.py          FastAPI app
  import_data.py     JSONL -> DB (rebuild-from-source)
  mcp_servers/
    procurement.py   MCP: supplier tools
    email_drafts.py  MCP: Gmail draft tools
    gmail_auth.py    one-time Gmail OAuth
  Dockerfile, entrypoint.sh
docker-compose.yml, requirements.txt, .env.example
```

## Local development (SQLite)

Run from the **repo root** (`GetPostach/`):

```bash
python -m venv .venv && .venv\Scripts\activate      # Windows (bash: source .venv/Scripts/activate)
pip install -r requirements.txt
cp .env.example .env                                # set ANTHROPIC_API_KEY to enable the chat
cd backend
python import_data.py                              # loads ../../CPHI_MILAN/data into ./pigulkin.db
uvicorn app.main:app --reload --port 8900
```

Check:
- `GET  http://localhost:8900/health`
- `GET  http://localhost:8900/api/stats`
- `GET  http://localhost:8900/api/suppliers?q=diclofenac`
- `GET  http://localhost:8900/api/benchmark?product=Diclofenac%20Sodium`
- `GET  http://localhost:8900/api/meeting-brief/aarti`
- `POST http://localhost:8900/api/chat` `{"messages":[{"role":"user","text":"Хто постачає диклофенак?"}]}`

Without `ANTHROPIC_API_KEY` the chat returns an honest placeholder; the DB endpoints work regardless.

## Anthropic API key

The backend needs an **API key** (a Claude subscription is not the same thing).
Create one at <https://console.anthropic.com> → API Keys, put it in `.env` as `ANTHROPIC_API_KEY`.
Model defaults to `claude-sonnet-4-6` (`CHAT_MODEL`).

## Wiring the frontend

The GetPostach chat (repo root `index.html`) calls the endpoint in `window.GP_PIGULKIN_API`.
Point it at this backend by adding a tiny script tag before `<script src="support.js">` in
`index.html` (and mirror it into `GetPostach visionOS.dc.html`):

```html
<script>window.GP_PIGULKIN_API = "https://your-server.example.com/api/chat";</script>
```

Set `CORS_ORIGINS` in `.env` to include the site origin (e.g. `https://artyd.github.io`).

## Deploy (Docker, on your server)

`docker-compose.yml` lives at the repo root and builds `backend/` — deploy the whole repo.

```bash
git clone https://github.com/artyd/GetPostach          # this repo (frontend + backend)
git clone https://github.com/artyd/CPHI_MILAN          # data source, as a sibling dir
cd GetPostach
cp .env.example .env                                   # set ANTHROPIC_API_KEY, CORS_ORIGINS, POSTGRES_PASSWORD
#   CPHI_REPO_DIR defaults to ../CPHI_MILAN (mounted read-only for the one-time import)
docker compose up -d --build
```

First boot imports the data automatically (backend `AUTO_IMPORT=1`, only if the DB is empty).

**Updating a running deploy:** use `./deploy.sh` from the repo root — it pulls, rebuilds, and
force-recreates Caddy (needed because the frontend is bind-mounted as single files, which a
running container does not re-read after `git reset` replaces them). It never touches `.env`.

To re-import after updating CPHI_MILAN:

```bash
git -C ../CPHI_MILAN pull
docker compose run --rm backend python import_data.py
```

Services:
- backend API → `:8000`
- procurement MCP (streamable-http) → `:8931`
- email MCP (streamable-http) → `:8932`

Put a reverse proxy (nginx/Caddy) with TLS in front of `:8000` for the public chat endpoint.

## Email drafts (Gmail) — one-time authorization

The email MCP creates **drafts only** (scope `gmail.compose`); a human reviews and sends from Gmail.

1. Google Cloud Console → enable Gmail API → OAuth client (type **Desktop app**) → download the JSON.
2. Save it as `secrets/gmail_credentials.json` (mounted into the email container).
3. Authorize once (on a machine with a browser):
   ```bash
   cd backend
   GMAIL_CREDENTIALS_PATH=../secrets/gmail_credentials.json \
   GMAIL_TOKEN_PATH=../secrets/gmail_token.json \
   python -m mcp_servers.gmail_auth authorize
   ```
4. `docker compose up -d mcp-email` — it uses the stored token, no browser needed.

## Using the MCP servers from Claude Desktop / Claude Code

Remote (streamable-http): point the client at `http://your-server:8931/mcp` (procurement) and
`http://your-server:8932/mcp` (email). Or run locally over stdio:

```bash
cd backend
python -m mcp_servers.procurement      # stdio
python -m mcp_servers.email_drafts     # stdio
```

## Notes / next steps

- Data-quality caveat (from CPHI_MILAN): pre-2026-09-11 quotes/threads are flagged unverified; a live Gmail re-sync is the real fix. The `thread`/`msg` ids are the reconciliation keys — a future "read email body" MCP tool can use them.
- Semantic search (pgvector) is intentionally deferred; the chat uses tool-calling, which needs no embeddings.
