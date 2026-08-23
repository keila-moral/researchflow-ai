# ResearchFlow AI

**An automated arXiv research assistant, built entirely with n8n.**

ResearchFlow AI monitors arXiv for new papers in categories you choose, summarises them with GPT-4o, and indexes their embeddings in [Qdrant](https://qdrant.tech/) so you can search your growing research library by meaning — not just keywords. New papers are pushed to Telegram or Discord automatically, and a Telegram bot lets you ask questions, browse recent papers, give feedback, and receive a weekly digest.

`n8n` · `OpenAI GPT-4o` · `Qdrant` · `Docker` · `Traefik` · `Telegram/Discord APIs`

**Skills demonstrated:**
- **Workflow automation** — a scheduled, multi-step pipeline (fetch → deduplicate → PDF summarise → store → notify) orchestrated in n8n with no manual intervention.
- **LLM integration** — structured prompt design with JSON mode for consistent, typed output (contribution, methodology, relevance rating, tags) rather than free-text.
- **Retrieval-Augmented Generation (RAG)** — embedding generation, vector storage, and semantic retrieval via Qdrant, plus a grounded GPT-4o answer with inline paper citations.
- **Agentic routing** — relevance-based pipeline branching: High papers get full summarisation + thumbnail; Medium get compact notifications; Low are stored silently.
- **Human-in-the-loop feedback** — inline keyboard buttons on every notification let you expand, skip, or save papers; ratings are persisted to Qdrant and influence the weekly digest.
- **Error resilience** — PDF fallback path, structured error handler workflow, and per-node error branches ensure no silent failures.
- **Observability** — every pipeline run is logged to a `run_logs` Qdrant collection; `/stats` surfaces a 7-day summary on demand.
- **Infrastructure as code** — reproducible production environment via Docker Compose with Traefik HTTPS, Qdrant auth, pinned image versions, and automated volume backups.

## Table of Contents

- [How It Works](#how-it-works)
- [Workflows](#workflows)
- [Query API](#query-api)
- [Telegram Commands](#telegram-commands)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Project Structure](#project-structure)
- [Architecture](#architecture)
- [Testing](#testing)
- [Roadmap](#roadmap)
- [About the Author](#about-the-author)

## How It Works

Five workflows share the same Qdrant vector store.

**Pipeline 1 — Ingest & Notify** (scheduled daily)

```
arXiv API → fetch & deduplicate → download PDF → map-reduce summarise (gpt-4o-mini)
         → structured summary (gpt-4o, JSON mode) → relevance routing
               ├─ High   → DALL-E thumbnail → Qdrant → Telegram + Discord (full, with feedback buttons)
               ├─ Medium → Qdrant → Telegram + Discord (compact, no thumbnail)
               └─ Low    → Qdrant (silent, no notification)
         → Merge Notifications → Log Run (run_logs collection + daily Telegram summary)
```

**Pipeline 2 — Ask Questions** (on demand via Telegram or HTTP)

```
/search <question> → Semantic Search webhook → embed query (text-embedding-3-small)
                   → Qdrant vector search → GPT-4o answer with citations → Telegram reply
```

**Pipeline 3 — Weekly Digest** (every Saturday 9am)

```
Qdrant scroll (last 7 days) → group by tag → GPT-4o digest writer
→ overall trend + per-group narratives + top-3 picks → Telegram + Discord
```

**Pipeline 4 — Human feedback** (triggered by inline keyboard buttons)

```
Telegram button press → expand: GPT-4o deep analysis (methodology, limitations, related work)
                      → skip:   Qdrant PATCH user_rating: -1
                      → save:   Qdrant PATCH user_rating: 1
```

## Workflows

### `Arxiv_Monitor.json` (31 nodes)

The core ingest pipeline.

| Stage | Nodes |
|---|---|
| Trigger | Schedule Trigger |
| Ingest | Define Categories → Loop Categories → Wait (3s) → Fetch Arxiv → Process Results |
| Dedup | Merge Papers → Check If Exists → Filter Processed |
| PDF | Get PDF URL → Download PDF → Summarization Chain (+ OpenAI Chat Model, Text Splitter, Data Loader) |
| PDF fallback | PDF Fallback (Abstract Only) — catches Download PDF and Summarization Chain errors |
| AI summary | OpenAI Summarize (gpt-4o, JSON mode) → Extract Tags |
| Routing | Relevance Router → [High] Generate Thumbnail → Extract Image URL → Persist Thumbnail |
|  | → [Medium] Set Empty Thumbnail |
|  | → [Low] (straight to store) |
| Store | Qdrant Store |
| Notify | Notify Switch → [High] Send Telegram Notification + Send Discord Notification |
|  | → [Medium] Send Telegram Compact + Send Discord Compact |
| Observability | Merge Notifications → Log Run (writes to `run_logs` collection + Telegram summary) |

### `Semantic_Search_Agent.json` (9 nodes)

The RAG query endpoint. Triggered by a POST webhook; also called internally by the Telegram bot.

| Node | Role |
|---|---|
| Search Webhook | `POST /webhook/search` — accepts `query`, `top_k`, `tag_filter` |
| Validate Input | Guards missing query; clamps `top_k` to 1–20 |
| Embed Query | OpenAI `text-embedding-3-small` |
| Merge Embedding | Combines embedding with original query params |
| Qdrant Vector Search | Semantic search with optional tag filter; score threshold 0.35; Qdrant auth |
| GPT-4o Answer | Grounded answer with inline `[Paper Title]` citations; JSON mode |
| Format Response | Returns `{ answer, sources[], confidence, retrieved_papers[] }` |
| Respond to Webhook | HTTP 200 JSON response |
| Search Error Response | HTTP 500 JSON on Qdrant or GPT-4o failure |

### `Telegram_Query_Bot.json` (16 nodes)

The conversational interface. Listens for Telegram messages and inline keyboard callbacks.

| Command / Action | Behaviour |
|---|---|
| `/search <question>` | Calls Semantic Search webhook; returns cited answer with source links |
| `/latest [n]` | Scrolls Qdrant by ingestion order; returns n most recent papers (default 5, max 10) |
| `/stats` | Returns a 7-day run summary: papers indexed, relevance breakdown, PDF fallback rate, avg run time |
| `/help` | Returns the command reference |
| 🔍 Expand button | Fetches paper from Qdrant; GPT-4o deep analysis (methodology, limitations, related work) |
| ⏭ Skip button | PATCHes paper in Qdrant with `user_rating: -1` |
| 🔖 Save button | PATCHes paper in Qdrant with `user_rating: 1`; surfaces in weekly digest top picks |

### `Weekly_Digest.json` (9 nodes)

Runs every Saturday at 9am. Synthesises the week's papers into a narrative digest.

| Node | Role |
|---|---|
| Saturday 9am Trigger | Cron: `0 9 * * 6` |
| Compute Date Window | ISO timestamps for last 7 days + human-readable week label |
| Fetch Week Papers | Paginates Qdrant scroll, filters by `ingested_at`; fallback if no papers |
| Group by Tag | Groups by primary tag, sorted by count; `topPicks` prefers saved papers |
| GPT-4o Digest | JSON mode: `overall_trend`, `group_narratives[]`, `top_3_picks[]` |
| Format Digest | Assembles Telegram Markdown message |
| Send Telegram Digest | Sends to configured chat |
| Send Discord Digest | Posts to Discord webhook |
| No Papers This Week | Fallback Telegram message if nothing was indexed |

### `Error_Handler.json` (3 nodes)

Triggered by n8n's global error trigger. Sends Telegram alert and email with workflow name, failing node, error message, and execution link. All credentials read from `$vars`.

## Query API

The Semantic Search Agent exposes a single POST endpoint.

**Request**

```bash
curl -X POST https://<N8N_DOMAIN>/webhook/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "What are the latest improvements to RAG pipelines?",
    "top_k": 5,
    "tag_filter": "RAG"
  }'
```

| Field | Type | Required | Description |
|---|---|---|---|
| `query` | string | ✅ | Natural-language question |
| `top_k` | integer | ❌ | Papers to retrieve (default 5, max 20) |
| `tag_filter` | string | ❌ | Filter to a tag e.g. `"RAG"`, `"LLMs"`, `"Agents"` |

**Response**

```json
{
  "query": "What are the latest improvements to RAG pipelines?",
  "answer": "Recent work on RAG has focused on... [Improving RAG with X]",
  "sources": [
    {
      "title": "Improving RAG with X",
      "url": "https://arxiv.org/abs/2401.12345",
      "relevance_to_query": "Directly proposes a new RAG retrieval strategy"
    }
  ],
  "confidence": "High",
  "result_count": 5,
  "retrieved_papers": [
    {
      "title": "Improving RAG with X",
      "url": "https://arxiv.org/abs/2401.12345",
      "score": 0.87,
      "tags": ["RAG", "LLMs"],
      "thumbnail_url": "https://<N8N_DOMAIN>/static/thumbnails/2401.12345.png"
    }
  ]
}
```

## Telegram Commands

| Command | Example | Description |
|---|---|---|
| `/search <question>` | `/search How do MoE models scale?` | Ask a question; get a cited answer from indexed papers |
| `/latest [n]` | `/latest 3` | Show the 3 most recently ingested papers |
| `/stats` | `/stats` | 7-day run summary with relevance breakdown and fallback rate |
| `/help` | `/help` | Show the command reference |

**Notification buttons** (appear on every High-relevance paper notification):

| Button | What happens |
|---|---|
| 🔍 Expand | GPT-4o generates a deep analysis covering methodology, limitations, and related work |
| ⏭ Skip | Paper is rated -1 in Qdrant; deprioritised in future searches |
| 🔖 Save | Paper is rated +1 in Qdrant; bubbles up in the weekly digest top-3 |

## Quick Start

### Prerequisites

- [Docker](https://www.docker.com/) and Docker Compose
- A domain pointed at your server (required for Traefik HTTPS)
- An OpenAI API key
- A Telegram bot token (from [@BotFather](https://t.me/BotFather)) and your chat ID
- (Optional) a Discord webhook URL

### 1. Clone and configure

```bash
git clone https://github.com/keila-moral/researchflow-ai.git
cd researchflow-ai
cp env.example .env
# Edit .env and fill in all required values
```

### 2. Start the stack

```bash
docker compose up -d
```

Once running, n8n is available at `https://<N8N_DOMAIN>`.

### 3. Import workflows

In n8n → **Workflows** → **Import from File**, import in this order:

1. `workflows/Error_Handler.json`
2. `workflows/Arxiv_Monitor.json`
3. `workflows/Semantic_Search_Agent.json`
4. `workflows/Telegram_Query_Bot.json`
5. `workflows/Weekly_Digest.json`

Then:

- Configure credentials for OpenAI, Qdrant, Telegram, and Discord.
- Set n8n Variables (`Settings → Variables`): `N8N_DOMAIN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_BOT_TOKEN`, `DISCORD_WEBHOOK_URL`, `ALERT_FROM_EMAIL`, `ALERT_TO_EMAIL`, `QDRANT_API_KEY`, `OPENAI_API_KEY`.
- Activate all five workflows.

### 4. Run the schema migration (first time only)

If you have existing data in Qdrant from an earlier version:

```bash
docker exec -it n8n python3 /files/migrate_qdrant_schema.py
```

### 5. Test

```bash
# Test the search endpoint directly
curl -X POST https://<N8N_DOMAIN>/webhook/search \
  -H "Content-Type: application/json" \
  -d '{"query": "What is chain-of-thought prompting?", "top_k": 3}'
```

See [`docs/testing.md`](docs/testing.md) for a full manual test checklist.

## Configuration

| Setting | Where to change |
|---|---|
| Monitored arXiv categories | `Define Categories` node in `Arxiv_Monitor.json` |
| Schedule (default: daily) | `Schedule Trigger` node in `Arxiv_Monitor.json` |
| Papers per run | `Fetch Arxiv` HTTP Request node (query param `max_results`) |
| Qdrant collection name | `Qdrant Store` and `Check If Exists` nodes (default: `arxiv_papers`) |
| Relevance routing thresholds | `Relevance Router` Switch node in `Arxiv_Monitor.json` |
| Search score threshold | `Qdrant Vector Search` node in `Semantic_Search_Agent.json` (default: `0.35`) |
| Weekly digest schedule | `Saturday 9am Trigger` node in `Weekly_Digest.json` |
| Notification channels | Activate/deactivate Telegram or Discord nodes as needed |

## Project Structure

```
researchflow-ai/
├── workflows/
│   ├── Arxiv_Monitor.json              # Ingest, summarise, route, store, notify, log (31 nodes)
│   ├── Semantic_Search_Agent.json      # RAG query webhook (9 nodes)
│   ├── Telegram_Query_Bot.json         # /search, /latest, /stats, /help + feedback buttons (16 nodes)
│   ├── Weekly_Digest.json              # Saturday morning research roundup (9 nodes)
│   └── Error_Handler.json              # Global error alerting (3 nodes)
├── docs/
│   ├── architecture.md                 # Full data flow, node descriptions, design decisions
│   └── testing.md                      # Manual test checklist for all workflows
├── scripts/
│   ├── parse_xml.py                    # Standalone arXiv XML → JSON utility
│   └── migrate_qdrant_schema.py        # One-off schema migration: url → metadata.url
├── screenshots/                        # Workflow canvas and example output images
├── docker-compose.yml                  # n8n + Qdrant + Traefik + backup stack
├── env.example                         # All required environment variables
├── script_explanation.md               # Detailed node and script breakdown
├── optimization_todo_updated.md        # Original optimisation checklist (historical)
└── README.md
```

## Architecture

See [`docs/architecture.md`](docs/architecture.md) for full data flow diagrams, the Qdrant metadata schema, infrastructure tables, and key design decisions.

## Testing

See [`docs/testing.md`](docs/testing.md) for a step-by-step manual test checklist covering all five workflows, the query API, and the Telegram bot commands.

## Roadmap

The 3-week implementation roadmap is complete. All planned features are shipped:

- ✅ Week 1 — Stability: structured output, PDF fallback, thumbnail persistence, Qdrant auth, `.env.example`
- ✅ Week 2 — Interactivity: Semantic Search workflow, Telegram bot, relevance routing, daily run summary
- ✅ Week 3 — Agentic loops: human-in-the-loop feedback, weekly digest, run logging, schema migration

Backlog (future work): multi-source ingestion (Hugging Face Papers, Semantic Scholar), ReAct multi-tool query agent, paper clustering agent, fine-tuning signal export from saved papers.

See [`researchflow-roadmap.md`](researchflow-roadmap.md) for the full plan with per-day task breakdowns.

## About the Author

Built by [Keila Moral](https://github.com/keila-moral). [More projects here.](https://linktr.ee/kmoralfig)

Questions, feedback, or ideas? Open an issue or reach out directly.
