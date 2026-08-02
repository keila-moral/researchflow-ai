# ResearchFlow AI

**An automated arXiv research assistant, built entirely with n8n.**

ResearchFlow AI monitors arXiv for new papers in categories you choose, summarises them with GPT-4o, and indexes their embeddings in [Qdrant](https://qdrant.tech/) so you can search your growing research library by meaning — not just keywords. New papers are pushed to Telegram or Discord automatically, and a Telegram bot lets you ask questions over everything you've collected.

`n8n` · `OpenAI GPT-4o` · `Qdrant` · `Docker` · `Traefik` · `Telegram/Discord APIs`

**Skills demonstrated:**
- **Workflow automation** — a scheduled, multi-step pipeline (fetch → deduplicate → PDF summarise → store → notify) orchestrated in n8n with no manual intervention.
- **LLM integration** — structured prompt design with JSON mode for consistent, typed output (contribution, methodology, relevance rating, tags) rather than free-text.
- **Retrieval-Augmented Generation (RAG)** — embedding generation, vector storage, and semantic retrieval via Qdrant, plus a grounded GPT-4o answer with inline paper citations.
- **Agentic routing** — relevance-based pipeline branching: High papers get full summarisation + thumbnail; Medium get compact notifications; Low are stored silently.
- **Error resilience** — PDF fallback path, structured error handler workflow, and per-node error branches ensure no silent failures.
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
- [Roadmap](#roadmap)
- [About the Author](#about-the-author)

## How It Works

Two independent workflows share the same Qdrant vector store.

**Pipeline 1 — Ingest & Notify** (scheduled)

```
arXiv API → fetch & deduplicate → download PDF → map-reduce summarise (gpt-4o-mini)
         → structured summary (gpt-4o, JSON mode) → relevance routing
               ├─ High   → DALL-E thumbnail → Qdrant → Telegram + Discord (full)
               ├─ Medium → Qdrant → Telegram + Discord (compact, no thumbnail)
               └─ Low    → Qdrant (silent, no notification)
```

**Pipeline 2 — Ask Questions** (on demand)

```
Telegram /search → Semantic Search webhook → embed query (text-embedding-3-small)
                → Qdrant vector search → GPT-4o answer with citations → Telegram reply
```

1. A scheduled n8n workflow polls arXiv for new papers across multiple categories (cs.AI, cs.LG, cs.CL, cs.CV, stat.ML, cs.NE by default).
2. Each new paper is deduplicated against Qdrant, its PDF downloaded, and summarised via a LangChain map-reduce chain using `gpt-4o-mini`.
3. The map-reduce output is passed to `gpt-4o` with JSON mode, producing structured fields: `what_it_does`, `how_it_works`, `why_it_matters`, `relevance`, `tags[]`, and a pre-formatted Telegram message.
4. A relevance switch routes the paper: High relevance papers get a DALL-E 3 thumbnail (persisted to n8n's static file server) and a full notification; Medium get a compact notification; Low are stored silently.
5. All papers are upserted into Qdrant with structured metadata for later filtering.
6. On demand, the Telegram bot's `/search` command embeds the query, retrieves the top-k papers from Qdrant, and returns a GPT-4o answer with inline citations.

## Workflows

### `Arxiv_Monitor.json` (29 nodes)

The core ingest pipeline.

| Stage | Nodes |
|---|---|
| Trigger | Schedule Trigger |
| Ingest | Define Categories → Loop Categories → Wait → Fetch Arxiv → Process Results |
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

### `Semantic_Search_Agent.json` (9 nodes)

The RAG query endpoint. Triggered by a POST webhook; also called internally by the Telegram bot.

| Node | Role |
|---|---|
| Search Webhook | `POST /webhook/search` — accepts `query`, `top_k`, `tag_filter` |
| Validate Input | Guards missing query; clamps `top_k` to 1–20 |
| Embed Query | OpenAI `text-embedding-3-small` |
| Merge Embedding | Combines embedding with original query params |
| Qdrant Vector Search | Semantic search with optional tag filter; score threshold 0.35 |
| GPT-4o Answer | Grounded answer with inline `[Paper Title]` citations; JSON mode |
| Format Response | Returns `{ answer, sources[], confidence, retrieved_papers[] }` |
| Respond to Webhook | HTTP 200 JSON response |
| Search Error Response | HTTP 500 JSON on Qdrant or GPT-4o failure |

### `Telegram_Query_Bot.json` (10 nodes)

The conversational interface. Listens for Telegram messages and routes to the correct handler.

| Command | Behaviour |
|---|---|
| `/search <question>` | Calls the Semantic Search webhook; returns cited answer with source links |
| `/latest [n]` | Scrolls Qdrant in reverse insertion order; returns n most recent papers (default 5, max 10) |
| `/help` | Returns the command reference |
| (anything else) | Returns an unknown command message with a /help prompt |

### `Error_Handler.json`

Triggered by n8n's global error trigger. Sends a Telegram alert and an email notification containing the workflow name, failing node, error message, and execution link. Credentials are read from `$vars` — no hardcoded values.

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
| `top_k` | integer | ❌ | Number of papers to retrieve (default 5, max 20) |
| `tag_filter` | string | ❌ | Filter results to a specific tag (e.g. `"RAG"`, `"LLMs"`) |

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

Once the `Telegram_Query_Bot` workflow is active and your bot is configured:

| Command | Example | Description |
|---|---|---|
| `/search <question>` | `/search How do mixture of experts models scale?` | Ask a question; get a cited answer from indexed papers |
| `/latest [n]` | `/latest 3` | Show the 3 most recently ingested papers |
| `/help` | `/help` | Show the command reference |

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
cp .env.example .env
# Edit .env and fill in all required values
```

### 2. Start the stack

```bash
docker compose up -d
```

Once running, n8n is available at `https://<N8N_DOMAIN>`.

### 3. Import workflows

1. Open n8n → **Workflows** → **Import from File**
2. Import each file from `workflows/` in this order:
   - `Error_Handler.json`
   - `Arxiv_Monitor.json`
   - `Semantic_Search_Agent.json`
   - `Telegram_Query_Bot.json`
3. Configure credentials for OpenAI, Qdrant (API key: `$QDRANT_API_KEY`), Telegram, and Discord.
4. Set the following n8n variables (`Settings → Variables`): `N8N_DOMAIN`, `TELEGRAM_CHAT_ID`, `DISCORD_WEBHOOK_URL`, `ALERT_FROM_EMAIL`, `ALERT_TO_EMAIL`, `QDRANT_API_KEY`.
5. Activate all four workflows.

### 4. Test the search endpoint

```bash
curl -X POST https://<N8N_DOMAIN>/webhook/search \
  -H "Content-Type: application/json" \
  -d '{"query": "What is chain-of-thought prompting?", "top_k": 3}'
```

## Configuration

| Setting | Where to change |
|---|---|
| Monitored arXiv categories | `Define Categories` node in `Arxiv_Monitor.json` |
| Schedule (default: daily) | `Schedule Trigger` node in `Arxiv_Monitor.json` |
| Number of papers per run | `Fetch Arxiv` HTTP Request node (query param `max_results`) |
| Qdrant collection name | `Qdrant Store` and `Check If Exists` nodes (default: `arxiv_papers`) |
| Relevance routing thresholds | `Relevance Router` Switch node in `Arxiv_Monitor.json` |
| Search score threshold | `Qdrant Vector Search` node in `Semantic_Search_Agent.json` (default: `0.35`) |
| Notification channels | Activate/deactivate Telegram or Discord nodes as needed |

## Project Structure

```
researchflow-ai/
├── workflows/
│   ├── Arxiv_Monitor.json          # Ingest, summarise, route, store, notify
│   ├── Semantic_Search_Agent.json  # RAG query webhook
│   ├── Telegram_Query_Bot.json     # /search, /latest, /help commands
│   └── Error_Handler.json          # Global error alerting
├── docs/
│   └── architecture.md             # Full data flow and node-level architecture
├── scripts/
│   └── parse_xml.py                # Standalone arXiv XML → JSON utility
├── screenshots/                    # Workflow canvas and example output images
├── docker-compose.yml              # n8n + Qdrant + Traefik + backup stack
├── .env.example                    # All required environment variables
├── script_explanation.md           # Detailed node and script breakdown
└── README.md
```

## Architecture

See [`docs/architecture.md`](docs/architecture.md) for the full data flow diagram, node-level descriptions, and design decisions.

## Roadmap

Upcoming work: human-in-the-loop Telegram feedback (expand/skip/save), weekly digest agent, run observability with Grafana, and a ReAct multi-tool query agent.

See [`researchflow-roadmap.md`](researchflow-roadmap.md) for the full 3-week plan with weekly milestones.

## About the Author

Built by [Keila Moral](https://github.com/keila-moral). [More projects here.](https://linktr.ee/kmoralfig)

Questions, feedback, or ideas? Open an issue or reach out directly.
