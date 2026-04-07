# DevMate v2

**Context-aware AI coding assistant powered by DigitalOcean Gradient**

> Paste your GitHub repo. Ask anything. Get code that actually fits your codebase.

[![Demo Video](https://img.shields.io/badge/Demo-YouTube-red?style=for-the-badge)](https://youtu.be/yAmvyOEalBU?si=qKijhf8PdA1e8mty)

---

## 📹 Demo Video

**[→ Watch Demo on YouTube](https://youtu.be/yAmvyOEalBU?si=qKijhf8PdA1e8mty)**

---

## How to run

### What you need
- [Docker Desktop](https://docker.com) installed and running
- DigitalOcean Personal Access Token → [cloud.digitalocean.com/account/api/tokens](https://cloud.digitalocean.com/account/api/tokens)
- DigitalOcean Gradient Model Access Key → [cloud.digitalocean.com/gradient](https://cloud.digitalocean.com/gradient) → Serverless Inference → Model Access Keys → Create Key
- (Optional) GitHub Token → [github.com/settings/tokens](https://github.com/settings/tokens) — increases rate limit from 60 to 5000 requests/hr

---

### Step 1 — Clone the repo

```bash
git clone https://github.com/devu729/devmate-v2
cd devmate-v2
```

---

### Step 2 — Add your keys

```bash
cp .env.example .env
```

Open `.env` and fill in your 3 keys:

```env
DO_GRADIENT_API_KEY=dop_v1_xxxxxxxxxxxxxxxx
GRADIENT_MODEL_ACCESS_KEY=xxxxxxxxxxxxxxxxxxxxxxxx
GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx
```

---

### Step 3 — Build and start

```bash
docker compose build --no-cache
docker compose up
```

**Expected output — you should see this:**

```
[+] Building 22.4s (8/8) FINISHED
 ✔ Image devmate-backend  Built
 ✔ Container devmate_v2   Started

devmate_v2 | INFO: DevMate v2 starting up
devmate_v2 | INFO: DO_GRADIENT_API_KEY detected ✓
devmate_v2 | INFO: GITHUB_TOKEN detected (higher rate limits) ✓
devmate_v2 | INFO: Application startup complete.
devmate_v2 | INFO: Uvicorn running on http://0.0.0.0:8000
```

---

### Step 4 — Open the app

```
http://localhost:8000
```

---

### Step 5 — Index a repo

1. Paste this URL into the input box:
   ```
   https://github.com/pallets/flask
   ```
2. Click **Index Repo**
3. Wait ~30 seconds. You should see:
   ```
   ✓ Repository indexed successfully.
   212 files loaded · Framework: Flask · Dependencies: 28 packages detected
   ```

---

### Step 6 — Test all 3 modes

**[CHAT] tab** — type:
```
How does Flask handle routing?
```
Expected: Detailed explanation with code examples using Flask's actual `@app.route()` decorator.

---

**[BUILD] tab** — type:
```
Add a health check endpoint to Flask
```
Expected: Code blocks with `modify`/`create` action badges, file paths validated against the real repo, copy buttons per block.

---

**[DEBUG] tab** — paste:
```
AttributeError: 'NoneType' object has no attribute 'json'
```
Expected: Root cause diagnosis, fix code referencing real Flask files, prevention tip.

---

## What it does

DevMate v2 reads your entire GitHub repository and becomes an AI assistant that knows **your specific codebase** — not generic answers.

| Mode | What it does |
|------|-------------|
| **[CHAT]** | Ask anything about the codebase — architecture, patterns, "how does X work" |
| **[BUILD]** | Describe a feature → get exact code matching your folder structure, naming conventions, and pinned dependency versions |
| **[DEBUG]** | Paste error + stack trace → root cause diagnosis using your actual indexed code |

---

## How it works

```
GitHub URL
    ↓
Fetch all source files concurrently (filtered, up to 100KB each)
    ↓
Parse dependencies — requirements.txt / package.json / go.mod / Cargo.toml
    ↓
Detect code patterns — framework, naming conventions, import style, test framework
    ↓
Build rich system prompt — file tree + deps + conventions + entry points
    ↓
DO Gradient Serverless Inference — llama3.3-70b-instruct
    ↓
Chat / Generate / Debug with full repo context
```

---

## DigitalOcean Gradient features used

| Feature | How DevMate uses it |
|---------|-------------------|
| **Serverless Inference** | All AI — chat, code generation, debugging — via `llama3.3-70b-instruct` at `https://inference.do-ai.run/v1/chat/completions` |
| **Model Access Keys** | Secure key-based auth for the inference endpoint |
| **Knowledge Base** | Architecture designed for DO Gradient KB vector search; uses prompt injection for broad compatibility |
| **Agent pattern** | System prompts inject full repo context — file tree, pinned deps, naming conventions, entry points |

---

## Why DevMate beats Cursor, Copilot, and ChatGPT

| Feature | DevMate v2 | Cursor | GitHub Copilot | ChatGPT |
|---------|-----------|--------|---------------|---------|
| Full repo indexing | ✅ | Partial | ❌ | ❌ |
| Pinned dep version awareness | ✅ | ❌ | ❌ | ❌ |
| File path validation | ✅ | ❌ | ❌ | ❌ |
| Naming convention matching | ✅ | ❌ | ❌ | ❌ |
| Self-hosted on DO infrastructure | ✅ | ❌ | ❌ | ❌ |
| Debug with repo context | ✅ | Partial | ❌ | ❌ |
| No IDE install required | ✅ | ❌ | ❌ | ✅ |
| Python + Node + Go + Rust deps | ✅ | ❌ | ❌ | ❌ |

---

## Project structure

```
devmate/
├── backend/
│   ├── main.py               # FastAPI app — 8 routes
│   ├── github_client.py      # GitHub REST client + dep extraction
│   ├── gradient_agent.py     # DO Gradient Inference — chat/generate/debug
│   ├── gradient_kb.py        # Knowledge Base client (prompt injection mode)
│   ├── gradient_inference.py # Serverless Inference client
│   ├── code_generator.py     # Prompt builder + file path validator
│   ├── docs_fetcher.py       # 80+ versioned docs URL registry
│   └── session_store.py      # In-memory session store (4h TTL)
├── frontend/
│   └── index.html            # Dark terminal UI — single file, vanilla JS
├── Dockerfile
├── docker-compose.yml
├── .env.example
└── DEMO_SCRIPT.md
```

---

## API reference

| Method | Route | Description |
|--------|-------|-------------|
| `POST` | `/index` | Start indexing a GitHub repo |
| `GET` | `/status/{id}` | Poll indexing progress |
| `POST` | `/chat` | Ask a question |
| `POST` | `/generate` | Generate code for a feature |
| `POST` | `/debug` | Diagnose error + stack trace |
| `GET` | `/health` | Health check |

Interactive docs: **http://localhost:8000/docs**

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `ERR_EMPTY_RESPONSE` | Run `docker logs devmate_v2 --tail 20` and check for errors |
| `DO_GRADIENT_API_KEY not set` | Check `.env` exists in the devmate folder with no spaces around `=` |
| Port 8000 already in use | `docker stop devmate_v2 && docker rm devmate_v2` then `docker compose up` |
| Indexing stuck at "Initializing" | Repo may be private — use a public repo like `pallets/flask` |
| Large repo times out | Add `GITHUB_TOKEN` to `.env` — raises rate limit from 60 to 5000 req/hr |

---

## Stop and restart

```bash
# Stop
docker compose down

# Start again (fast — uses cached build)
docker compose up

# Full rebuild after any code change
docker compose build --no-cache && docker compose up
```

---

## License

MIT
