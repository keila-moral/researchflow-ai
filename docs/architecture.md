# ResearchFlow AI — Architecture

This document describes the full data flow, node-level design, and key decisions across all five workflows.

---

## System overview

```
┌─────────────────────────────────────────────────────┐
│                  Docker Compose stack               │
│                                                     │
│  ┌─────────┐   ┌──────────────┐   ┌─────────────┐  │
│  │ Traefik │   │     n8n      │   │   Qdrant    │  │
│  │  HTTPS  │──▶│  workflows   │──▶│  vector DB  │  │
│  │  proxy  │   │  + webhooks  │   │  (internal) │  │
│  └─────────┘   └──────────────┘   └─────────────┘  │
│                       │                             │
│               ┌───────┴────────┐                   │
│               ▼                ▼                   │
│         OpenAI API      Telegram / Discord          │
└─────────────────────────────────────────────────────┘
```

Traefik handles TLS termination (Let's Encrypt) and proxies all traffic to n8n. Qdrant is not exposed externally — n8n reaches it over the internal Docker network at `http://qdrant:6333`. Qdrant API key authentication is enabled on all collection endpoints.

---

## Workflow 1 — Arxiv Monitor (31 nodes)

### Full data flow

```
Schedule Trigger
       │
       ▼
Define Categories ──▶ Loop Categories ──▶ Wait (3s) ──▶ Fetch Arxiv
                             ▲                               │
                             │                               ▼
                             └────────────────── Process Results
                                                       │
                                              (loop continues)
                                                       │
                                              (loop done)
                                                       ▼
                                               Merge Papers
                                                       │
                                                       ▼
                                             Check If Exists (Qdrant)
                                                       │
                                                       ▼
                                              Filter Processed
                                                       │
                              ┌────────────────────────┘
                              ▼
                          Get PDF URL
                              │
                              ▼
                         Download PDF ──[error]──▶ PDF Fallback (Abstract Only)
                              │                        │
                              ▼                        │
                    Summarization Chain ──[error]──▶───┘
                    (gpt-4o-mini, map-reduce)          │
                              │                        │
                              └──────────┬─────────────┘
                                         ▼
                               OpenAI Summarize (gpt-4o, JSON mode)
                                         │
                                         ▼
                                    Extract Tags
                                         │
                                         ▼
                                  Relevance Router
                              ┌──────────┼──────────┐
                            High       Medium       Low
                              │          │           │
                              ▼          ▼           │
                     Generate Thumbnail  Set Empty   │
                     (DALL-E 3)         Thumbnail    │
                              │          │           │
                              ▼          └───────────┤
                       Extract Image URL             │
                              │                      │
                              ▼                      │
                       Persist Thumbnail             │
                       (to /static/)                 │
                              │                      │
                              └──────────────────────┤
                                                     ▼
                                               Qdrant Store ──────────────────┐
                                                     │                        │
                                               Notify Switch          (input 1 for Low path)
                                          ┌──────────┴──────────┐            │
                                        High                  Medium          │
                                          │                      │            │
                              ┌───────────┘           ┌──────────┘            │
                              ▼                       ▼                       │
                  Send Telegram Notification   Send Telegram Compact           │
                  Send Discord Notification    Send Discord Compact            │
                              │                       │                       │
                              └───────────┬───────────┘                       │
                                          ▼                                   │
                                 Merge Notifications ◀─────────────────────────┘
                                          │
                                          ▼
                                       Log Run
                              (run_logs Qdrant collection
                               + Telegram daily summary)
```

### Stage notes

**Ingest loop**
Six arXiv categories are defined in `Define Categories` and iterated by `Loop Categories`. A 3-second `Wait` node between iterations respects arXiv's API rate limits. `Process Results` normalises the Atom/XML response into a flat JSON array.

**Deduplication**
`Check If Exists` queries Qdrant's `arxiv_papers` collection for the paper's arXiv ID using a `must` filter on `metadata.url`. `Filter Processed` drops any paper that already has a matching point.

**PDF pipeline**
`Get PDF URL` derives the PDF link (`/abs/` → `/pdf/`). `Download PDF` fetches the binary. The LangChain `Summarization Chain` (map-reduce, `gpt-4o-mini`) handles long papers by chunking and reducing to a single passage for `OpenAI Summarize`.

**PDF fallback**
If `Download PDF` or `Summarization Chain` fails, the error branch routes to `PDF Fallback (Abstract Only)`. This node reconstructs the expected `{ response: { text } }` shape using the abstract, sets `used_fallback: true`, and rejoins at `OpenAI Summarize`. Notifications include a ⚠️ warning.

**Structured summary (JSON mode)**
`OpenAI Summarize` uses `gpt-4o` with `response_format: json_object`. Output schema: `what_it_does`, `how_it_works`, `why_it_matters`, `relevance` (Low/Medium/High), `tags[]`, `telegram_summary`. `Extract Tags` parses the JSON and exposes typed fields.

**Relevance routing**
`Relevance Router` branches on `relevance`:
- **High** — DALL-E 3 thumbnail generated, downloaded, persisted to `/home/node/.n8n/static/thumbnails/`, stored in Qdrant. Full Telegram + Discord notification with inline keyboard buttons (Expand / Skip / Save).
- **Medium** — thumbnail skipped (`Set Empty Thumbnail`). Compact Telegram + Discord notification.
- **Low** — stored in Qdrant silently. No notification.

**Observability**
All four notification nodes and the Qdrant Store (for Low-path items) fan into `Merge Notifications`, then `Log Run`. `Log Run` writes a structured record to the `run_logs` Qdrant collection and sends a daily summary to Telegram.

**Qdrant metadata schema — `arxiv_papers` collection**

| Field | Source |
|---|---|
| `title` | arXiv API |
| `url` | arXiv paper ID (canonical link) |
| `tags` | GPT-4o JSON output (array) |
| `relevance` | GPT-4o JSON output (Low/Medium/High) |
| `thumbnail_url` | Persist Thumbnail node (permanent static URL) |
| `thumbnail_filename` | Persist Thumbnail node |
| `category` | Define Categories node |
| `abstract` | arXiv API |
| `what_it_does` | GPT-4o JSON output |
| `how_it_works` | GPT-4o JSON output |
| `why_it_matters` | GPT-4o JSON output |
| `used_fallback` | PDF Fallback node (`"true"` / `"false"`) |
| `user_rating` | Telegram bot feedback (1 = saved, -1 = skipped) |
| `user_rated_at` | Telegram bot feedback (ISO timestamp) |
| `ingested_at` | Log Run / Store node (ISO timestamp) |

**Qdrant metadata schema — `run_logs` collection**

| Field | Description |
|---|---|
| `run_id` | Unique ID derived from execution start time |
| `timestamp` | ISO start time of the run |
| `duration_ms` | Total run duration in milliseconds |
| `papers_fetched` | Total papers returned by arXiv API |
| `already_seen` | Papers filtered by dedup |
| `processed` | Papers that entered the AI pipeline |
| `high` / `medium` / `low` | Count by relevance tier |
| `pdf_fallbacks` | Papers that used abstract-only fallback |

---

## Workflow 2 — Semantic Search Agent (9 nodes)

### Data flow

```
POST /webhook/search
{ query, top_k, tag_filter }
         │
         ▼
   Validate Input
   (guard + clamp top_k to 1-20)
         │
         ▼
    Embed Query
    (text-embedding-3-small)
         │
         ▼
   Merge Embedding
   (combine vector with query params)
         │
         ▼
 Qdrant Vector Search ──[error]──▶ Search Error Response (HTTP 500)
 (cosine similarity,
  score threshold 0.35,
  optional tag filter,
  Qdrant API key auth)
         │
         ▼
  GPT-4o Answer ──[error]──▶ Search Error Response (HTTP 500)
  (json_object mode,
   inline [Paper Title] citations)
         │
         ▼
  Format Response
  { answer, sources[], confidence, result_count, retrieved_papers[] }
         │
         ▼
 Respond to Webhook (HTTP 200)
```

### Design notes

**Embedding model**: `text-embedding-3-small` (1536 dimensions) is used at both ingestion and query time. Consistent model choice is essential for cosine similarity to be meaningful.

**Score threshold**: `0.35` filters out low-similarity results. Lower = more recall, less precision; higher = fewer but more relevant results.

**JSON mode on the answer**: GPT-4o returns `{ answer, sources[], confidence }`. `confidence` (High/Medium/Low) reflects how well the retrieved papers address the question — useful for surfacing cases where the store doesn't yet contain relevant papers.

---

## Workflow 3 — Telegram Query Bot (16 nodes)

### Data flow

```
Telegram message or callback_query
         │
         ▼
   Parse Command
   (handles both message and callback_query events)
         │
         ▼
   Route Command (Switch, 7 outputs)
    ┌────┼────┬────┬──────────────────────┐
    │    │    │    │    (expand/skip/save callbacks)
  /search /latest /stats /help             │
    │    │    │    │                       ▼
    ▼    ▼    ▼    ▼               Answer Callback Query
  Call Fetch Fetch Help            (dismiss Telegram spinner)
  Search Latest Stats Reply               │
  Webhook Papers                          ▼
    │    │    │    │              Route Feedback (Switch)
    ▼    ▼    ▼    │         ┌────────┼────────┐
  Format Format Format       │        │        │
  Search Latest Stats      expand   skip     save
  Reply  Reply  Reply       │        │        │
    │    │    │    │         ▼        ▼        ▼
    └────┴────┴────┴──▶ Handle  Handle  Handle
                        Expand   Skip    Save
                            │        │        │
                            └────────┴────────┘
                                     │
                                     ▼
                                 Send Reply
                              (single Telegram node)
```

### Command handlers

**`/search`** — calls `Semantic_Search_Agent` webhook internally, formats cited answer as Telegram Markdown with source hyperlinks.

**`/latest`** — Qdrant scroll API ordered by `metadata.ingested_at` descending. Lightweight — no embedding required.

**`/stats`** — scrolls `run_logs` collection, filters to last 7 days, aggregates totals and computes fallback rate.

**`/help`** — static message listing all commands.

**Expand** — fetches paper from Qdrant by `metadata.url`, calls GPT-4o for a deeper analysis (methodology, limitations, related work, practical applications).

**Skip / Save** — scroll Qdrant to find the point ID by `metadata.url`, PATCH payload with `user_rating` and `user_rated_at`. Non-destructive; does not remove the point.

**Single converge node**: all branches write `{ chatId, text }` and converge at `Send Reply`. Adding a new command requires only a new branch — the send step never changes.

---

## Workflow 4 — Weekly Digest (9 nodes)

### Data flow

```
Saturday 9am (cron: 0 9 * * 6)
         │
         ▼
Compute Date Window
(since = 7 days ago, weekLabel)
         │
         ▼
Fetch Week Papers ──[error/empty]──▶ No Papers This Week (Telegram)
(paginated Qdrant scroll,
 filter by ingested_at >= since)
         │
         ▼
Group by Tag
(primary tag, sort by count desc,
 topPicks: saved papers first, then High)
         │
         ▼
GPT-4o Digest (json_object mode)
{ overall_trend, group_narratives[], top_3_picks[] }
         │
         ▼
Format Digest
         │
    ┌────┴────┐
    ▼         ▼
Telegram   Discord
```

### Design note — top picks preference

`Group by Tag` builds `topPicks` by first checking for papers with `user_rating: 1` (saved via Telegram button). If none exist, it falls back to High-relevance papers. This means user feedback from Day 15–16 directly influences the weekly digest — saved papers surface as recommended reads.

---

## Workflow 5 — Error Handler (3 nodes)

Triggered by n8n's global error trigger. Receives structured error context and fans out to:
- **Telegram**: alert to `$vars.TELEGRAM_CHAT_ID`
- **Email**: alert to `$vars.ALERT_TO_EMAIL` from `$vars.ALERT_FROM_EMAIL`

All credentials are read from n8n variables — no hardcoded values in the workflow JSON.

---

## Infrastructure

### Docker Compose services

| Service | Image | Role |
|---|---|---|
| `traefik` | `traefik:v3.1` | HTTPS reverse proxy + Let's Encrypt |
| `n8n` | `n8nio/n8n:1.48.3` | Workflow engine + webhook server |
| `qdrant` | `qdrant/qdrant:v1.12.3` | Vector database (internal only) |
| `backup` | `offen/docker-volume-backup:v2` | Scheduled volume backups with optional S3/R2 offsite |

### Volumes

| Volume | Contents |
|---|---|
| `n8n_data` | n8n database, credentials, workflow state |
| `thumbnails_data` | Persisted DALL-E thumbnails (mounted at `/home/node/.n8n/static/thumbnails/`) |
| `qdrant_data` | Qdrant vector store (both collections) |
| `traefik_data` | Let's Encrypt certificates |
| `backup_data` | Local backup archives |

### Security

- Qdrant publishes no host ports. n8n reaches it at `http://qdrant:6333` over the internal Docker network.
- Qdrant API key authentication enabled via `QDRANT__SERVICE__API_KEY`. All workflow nodes pass the key in the `api-key` header.
- All external traffic goes through Traefik with automatic HTTPS.
- Secrets passed to n8n as `N8N_VARIABLES_*` env vars, accessed in workflows via `$vars.*`.
- n8n thumbnails served at `/static/thumbnails/*` — publicly readable but unguessable (filenames are arXiv IDs).

---

## Key design decisions

**Why n8n?** Visual workflow debugging is a significant advantage when building multi-step pipelines — every node's input and output is inspectable per execution. n8n also provides built-in credential management, scheduling, and webhook hosting, eliminating a lot of glue code.

**Why map-reduce for PDF summarisation?** Research papers regularly exceed GPT-4o's context window. A map-reduce chain (chunk → summarise each chunk → reduce) handles arbitrarily long papers without truncation. `gpt-4o-mini` for the map step (cost efficiency); `gpt-4o` for the reduce/structured output (quality).

**Why persist thumbnails to the static file server?** DALL-E 3 signed URLs expire in ~60 minutes. Persisting to `/home/node/.n8n/static/thumbnails/` gives a permanent URL served directly by n8n with no additional infrastructure.

**Why a separate Notify Switch after Qdrant Store?** Routing notification format at the end of the pipeline keeps the storage step uniform — every paper goes through the same Qdrant upsert regardless of relevance. The notification decision is decoupled from the storage decision.

**Why inline keyboard buttons instead of text reply parsing?** Callback queries carry structured `callback_data` (`action:paper_url`), so the bot always knows exactly which paper is being acted on without needing to track message IDs or parse free-text replies. It also gives users a better UX — one tap instead of typing.

**Why store run logs in Qdrant?** Keeps the infrastructure minimal — no separate time-series DB or logging service needed. Qdrant's scroll API with payload filters is sufficient for the query patterns `/stats` needs (last N days, aggregate counts). For larger deployments, migrating to a proper TSDB would be appropriate.
