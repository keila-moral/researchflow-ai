# ResearchFlow AI — Architecture

This document describes the full data flow, node-level design, and key decisions across all four workflows.

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

Traefik handles TLS termination (Let's Encrypt) and proxies all traffic to n8n. Qdrant is not exposed externally — n8n reaches it over the internal Docker network at `http://qdrant:6333`. Qdrant API key authentication is enabled.

---

## Workflow 1 — Arxiv Monitor (29 nodes)

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
                         Download PDF ──[error]──▶ PDF Fallback
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
                                               Qdrant Store
                                                     │
                                               Notify Switch
                                          ┌──────────┴──────────┐
                                        High                  Medium
                                          │                      │
                              ┌───────────┘           ┌──────────┘
                              ▼                       ▼
                  Send Telegram Notification   Send Telegram Compact
                  Send Discord Notification    Send Discord Compact
```

### Stage notes

**Ingest loop**
Six arXiv categories are defined in `Define Categories` and iterated by `Loop Categories`. A 3-second `Wait` node between iterations respects arXiv's API rate limits. `Process Results` normalises the raw Atom/XML response into a flat JSON array of papers.

**Deduplication**
`Check If Exists` queries Qdrant's `arxiv_papers` collection for the paper's arXiv ID before processing. `Filter Processed` drops any paper that already has a matching point, preventing re-summarisation of papers seen in previous runs.

**PDF pipeline**
`Get PDF URL` derives the PDF link from the arXiv abstract URL (`/abs/` → `/pdf/`). `Download PDF` fetches the binary. The LangChain `Summarization Chain` (map-reduce strategy, `gpt-4o-mini`) handles long papers by chunking the text and reducing chunk summaries to a single passage, which is then passed to `OpenAI Summarize`.

**PDF fallback**
If `Download PDF` or `Summarization Chain` fails (network error, malformed PDF, timeout), the error branch routes to `PDF Fallback (Abstract Only)`. This node reconstructs the same `{ response: { text } }` shape using the paper's abstract, sets `used_fallback: true`, and rejoins the main path at `OpenAI Summarize`. Notifications include a ⚠️ warning when the fallback was used.

**Structured summary (JSON mode)**
`OpenAI Summarize` uses `gpt-4o` with `response_format: json_object`. The prompt enforces a schema with six fields: `what_it_does`, `how_it_works`, `why_it_matters`, `relevance` (Low/Medium/High), `tags[]`, and `telegram_summary`. `Extract Tags` parses the JSON and exposes `tags` as a native array and `relevance` as a top-level field.

**Relevance routing**
`Relevance Router` (Switch node) branches on the `relevance` field:
- **High** — full path: DALL-E 3 thumbnail generated, downloaded, persisted to `/home/node/.n8n/static/thumbnails/` (served at `https://<N8N_DOMAIN>/static/thumbnails/<id>.png`), then stored in Qdrant. Full Telegram + Discord notification with thumbnail and all summary sections.
- **Medium** — thumbnail skipped (`Set Empty Thumbnail` passes an empty string). Stored in Qdrant. Compact Telegram + Discord notification (title, `what_it_does`, tags, link).
- **Low** — stored in Qdrant silently. No notification.

**Qdrant metadata schema**
Each point stored in `arxiv_papers` has the following payload fields:

| Field | Source |
|---|---|
| `title` | arXiv API |
| `url` | arXiv paper ID (canonical link) |
| `tags` | GPT-4o JSON output |
| `relevance` | GPT-4o JSON output |
| `thumbnail_url` | Persist Thumbnail node (permanent static URL) |
| `thumbnail_filename` | Persist Thumbnail node |
| `category` | Define Categories node |
| `abstract` | arXiv API |
| `what_it_does` | GPT-4o JSON output |
| `how_it_works` | GPT-4o JSON output |
| `why_it_matters` | GPT-4o JSON output |
| `used_fallback` | PDF Fallback node (`"true"` / `"false"`) |

---

## Workflow 2 — Semantic Search Agent (9 nodes)

### Data flow

```
POST /webhook/search
{ query, top_k, tag_filter }
         │
         ▼
   Validate Input
   (guard + clamp top_k)
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
  optional tag filter)             
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

**Embedding model**: `text-embedding-3-small` (1536 dimensions) is used for both ingestion (via Qdrant's built-in embedding during store) and query-time embedding. Keeping the same model at both ends is essential for cosine similarity to be meaningful.

**Score threshold**: `0.35` filters out low-similarity results before they reach GPT-4o. Lowering this increases recall at the cost of answer quality; raising it improves precision but may return zero results for niche queries.

**JSON mode on the answer**: GPT-4o returns `{ answer, sources[], confidence }`. `confidence` is a self-reported High/Medium/Low that reflects how well the retrieved papers actually address the question — useful for surfacing cases where the vector store doesn't yet contain relevant papers.

**Error handling**: Both `Qdrant Vector Search` and `GPT-4o Answer` have error output branches wired to `Search Error Response`, which returns a structured `{ error, query }` JSON with HTTP 500. This prevents the webhook from hanging on a silent failure.

---

## Workflow 3 — Telegram Query Bot (10 nodes)

### Data flow

```
Telegram message received
         │
         ▼
   Parse Command
   (extract command, args, chatId)
         │
         ▼
   Route Command (Switch)
    ┌────┼────┬──────┐
    │    │    │      │
  /search /latest /help  (unknown)
    │    │    │      │
    ▼    ▼    ▼      ▼
 Call  Fetch  Help  Unknown
 Search Latest Reply  Reply
 Webhook Papers
    │    │    │      │
    ▼    ▼    └──────┘
 Format Format        │
 Search Latest        │
 Reply  Reply         │
    │    │            │
    └────┴────────────┘
              │
              ▼
         Send Reply
         (Telegram)
```

### Design notes

**`/search`** calls the `Semantic_Search_Agent` webhook internally via HTTP Request, passing the user's message text as `query` and `top_k: 5`. The response is formatted as a Telegram Markdown message with source titles as hyperlinks.

**`/latest`** uses Qdrant's scroll API (not vector search) ordered by `metadata.ingested_at` descending. This is a lightweight lookup — no embedding required — and returns a numbered list of recent papers with tags and relevance rating.

**Single converge node**: All four branches write `{ chatId, text }` and converge at a single `Send Reply` Telegram node. Adding a new command requires only a new branch and a new format node — the send step never needs to change.

---

## Workflow 4 — Error Handler

Triggered by n8n's global error trigger. Receives structured error context (workflow name, failing node, error message, execution ID) and fans out to:
- **Telegram**: alert message to `$vars.TELEGRAM_CHAT_ID`
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
| `qdrant_data` | Qdrant vector store |
| `traefik_data` | Let's Encrypt certificates |
| `backup_data` | Local backup archives |

### Security

- Qdrant is not published on any host port. n8n reaches it at `http://qdrant:6333` over the internal Docker network.
- Qdrant API key authentication is enabled via `QDRANT__SERVICE__API_KEY`. n8n workflow nodes pass the key in the `api-key` header.
- All external traffic goes through Traefik with automatic HTTPS.
- Secrets are passed to n8n as `N8N_VARIABLES_*` environment variables and accessed in workflows via `$vars.*`.

---

## Key design decisions

**Why n8n?** Visual workflow debugging is a significant advantage when building multi-step pipelines — every node's input and output is inspectable at any execution. n8n also provides built-in credential management, scheduling, and webhook hosting, eliminating a lot of glue code.

**Why map-reduce for PDF summarisation?** Research papers regularly exceed GPT-4o's context window in raw PDF-extracted text. A map-reduce chain (chunk → summarise each chunk → reduce to one summary) handles arbitrarily long papers without truncation. `gpt-4o-mini` is used for the map step (cost efficiency); `gpt-4o` for the reduce/final structured output (quality).

**Why persist thumbnails to the static file server?** DALL-E 3 returns signed CDN URLs that expire in approximately 60 minutes. Storing those URLs in Qdrant or sending them in Telegram would result in broken images within an hour. Persisting to `/home/node/.n8n/static/thumbnails/` gives a permanent URL served directly by n8n with no additional infrastructure.

**Why a separate Notify Switch after Qdrant Store?** Routing notification format at the end of the pipeline (after storage) rather than at the beginning keeps the storage step uniform — every paper, regardless of relevance, goes through the same Qdrant upsert path. The notification decision is separate from the storage decision.
