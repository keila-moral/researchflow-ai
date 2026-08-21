# Script and Workflow Explanation

Detailed breakdown of every component in the `researchflow-ai` repository.

---

## Scripts

### `scripts/parse_xml.py`

A standalone utility for processing arXiv API responses outside of n8n.

**Purpose:** arXiv returns search results in Atom (XML) format. This script converts that XML into clean JSON that is easier for other tools to consume.

**Key function:** `parse_arxiv_xml(xml_content)` — uses the `feedparser` library to iterate through entries and extract:
- `id` — unique arXiv URL/ID
- `title` — paper title (newlines stripped)
- `summary` — abstract (newlines stripped)
- `published` — publication date
- `link` — direct link to the paper

**Usage:**
```bash
python scripts/parse_xml.py sample_arxiv.xml
```

---

### `scripts/migrate_qdrant_schema.py`

A one-off migration script to normalise Qdrant payload structure.

**Background:** early versions of the pipeline stored the arXiv paper URL at the top-level payload key `url`. Later versions moved it to `metadata.url`. This script migrates all legacy points so `Check If Exists` can use a clean `must` filter on `metadata.url` only.

**Features:**
- Paginates through the full `arxiv_papers` collection (100 points/page)
- Identifies points with a top-level `url` field but no `metadata.url`
- Batch-upserts the normalised payload in groups of 100
- Idempotent — safe to run multiple times
- `DRY_RUN=true` mode for preview without writing

**Usage:**
```bash
# From inside the Docker network
docker exec -it n8n python3 /files/migrate_qdrant_schema.py

# Dry run
docker exec -it n8n sh -c "DRY_RUN=true python3 /files/migrate_qdrant_schema.py"
```

---

## Workflows

### 1. `Arxiv_Monitor.json` — Ingest, summarise, route, store, notify (31 nodes)

The core scheduled pipeline. Runs daily across six arXiv categories.

**Nodes in order:**

1. **Schedule Trigger** — daily cron schedule.
2. **Define Categories** — code node that outputs a list of arXiv category strings: `cs.AI`, `cs.LG`, `cs.CV`, `cs.CL`, `cs.NE`, `stat.ML`.
3. **Loop Categories** (`splitInBatches`) — iterates one category at a time.
4. **Wait** — 3-second pause between iterations to respect arXiv's rate limit policy.
5. **Fetch Arxiv** (HTTP Request) — queries the arXiv API for up to 10 recent papers in the current category.
6. **Process Results** — code node that normalises the Atom/XML API response into a flat JSON array of paper objects with `id`, `title`, `summary`, `link`, `category`.
7. **Merge Papers** — aggregates results across all category loop iterations into a single array.
8. **Check If Exists** — for each paper, queries Qdrant's `arxiv_papers` collection using a `must` filter on `metadata.url`. Sets `already_processed: true` if a matching point exists.
9. **Filter Processed** — drops papers where `already_processed === true`.
10. **Get PDF URL** — code node that transforms the abstract URL (`/abs/<id>`) into a PDF URL (`/pdf/<id>`).
11. **Download PDF** (HTTP Request) — fetches the full PDF binary from arXiv.
12. **OpenAI Chat Model** — `gpt-4o-mini`, used by the LangChain summarisation chain for map-step chunk summarisation.
13. **Recursive Character Text Splitter** — splits PDF text into overlapping chunks.
14. **Default Data Loader** — wraps the binary PDF for LangChain processing.
15. **Summarization Chain** — LangChain map-reduce chain: summarises each chunk independently (`gpt-4o-mini`), then reduces all chunk summaries to a single passage.
16. **PDF Fallback (Abstract Only)** — error-branch node catching failures from nodes 11 and 15. Reconstructs the expected `{ response: { text } }` shape using the paper's abstract. Sets `used_fallback: true`.
17. **OpenAI Summarize** — `gpt-4o` with `response_format: json_object`. Prompt enforces schema: `{ what_it_does, how_it_works, why_it_matters, relevance, tags[], telegram_summary }`.
18. **Extract Tags** — code node that `JSON.parse()`s the GPT-4o output and exposes `tags` (array), `relevance` (string), and `parsed` (full object) as top-level fields. Prepends a ⚠️ warning to `telegram_summary` when `used_fallback` is set. Throws a descriptive error on non-JSON output.
19. **Relevance Router** (Switch) — branches on `relevance`: output[0] = High, output[1] = Medium, fallback = Low.
20. **Generate Thumbnail** (HTTP Request) — `gpt-4o`'s image generation (DALL-E 3). High papers only.
21. **Extract Image URL** — code node that extracts the signed URL from the DALL-E response.
22. **Persist Thumbnail** — code node that downloads the expiring signed URL and saves the image to `/home/node/.n8n/static/thumbnails/<paper_id>.png`. Replaces `thumbnail_url` with the permanent static URL.
23. **Set Empty Thumbnail** (Set node) — Medium path: sets `thumbnail_url: ""` and `thumbnail_filename: ""`.
24. **Qdrant Store** — upserts the paper's embedding and full metadata payload into the `arxiv_papers` collection.
25. **Notify Switch** (Switch) — branches on `relevance` to select notification format.
26. **Send Telegram Notification** — full notification for High papers. Includes all summary sections, thumbnail, and an inline keyboard with 🔍 Expand / ⏭ Skip / 🔖 Save buttons.
27. **Send Discord Notification** — full embed for High papers with thumbnail image.
28. **Send Telegram Compact** — compact notification for Medium papers: title, `what_it_does`, tags, link.
29. **Send Discord Compact** — compact embed for Medium papers.
30. **Merge Notifications** (Merge node) — collects items from all notification branches and the Qdrant Store (for Low-path items) into a single stream.
31. **Log Run** — code node that counts papers by relevance tier, computes run duration, upserts a record to the `run_logs` Qdrant collection, and sends a daily summary to Telegram.

---

### 2. `Semantic_Search_Agent.json` — RAG query webhook (9 nodes)

A webhook-triggered workflow that answers natural-language questions using stored papers as context.

**Nodes in order:**

1. **Search Webhook** — `POST /webhook/search`. Accepts `{ query, top_k, tag_filter }`.
2. **Validate Input** — guards missing `query`; clamps `top_k` to 1–20.
3. **Embed Query** — HTTP Request to OpenAI Embeddings API using `text-embedding-3-small`.
4. **Merge Embedding** — code node that combines the embedding vector with the original query params (query string, top_k, tag_filter).
5. **Qdrant Vector Search** — code node that POSTs to `/collections/arxiv_papers/points/search` with the embedding vector, optional tag filter, score threshold 0.35, and Qdrant API key auth. Returns top-k results with full payload.
6. **GPT-4o Answer** — HTTP Request to OpenAI chat completions. System prompt instructs grounded-only answers with inline `[Paper Title]` citations. `response_format: json_object` enforces `{ answer, sources[], confidence }`.
7. **Format Response** — code node that parses the GPT-4o JSON and assembles the final response: `{ query, answer, sources[], confidence, result_count, retrieved_papers[] }`.
8. **Respond to Webhook** — HTTP 200 with JSON body.
9. **Search Error Response** — HTTP 500 with `{ error, query }`. Wired to error outputs of nodes 5 and 6.

---

### 3. `Telegram_Query_Bot.json` — Conversational interface (16 nodes)

Listens for Telegram messages and inline keyboard callbacks, routes to the correct handler, and returns a reply.

**Nodes in order:**

1. **Telegram Trigger** — listens for `message` and `callback_query` update types.
2. **Parse Command** — code node that handles both event types. For messages: extracts command (`/search`, `/latest`, `/stats`, `/help`, or `unknown`) and args. For callbacks: extracts `action:paper_url` from `callback_data`.
3. **Route Command** (Switch, 7 outputs) — routes by command string: 0=search, 1=latest, 2=help, 3=stats, 4=expand, 5=skip, 6=save. Unknown falls through to fallback.
4. **Call Search Webhook** — HTTP Request to `POST /webhook/search` with the user's query text.
5. **Format Search Reply** — code node that formats the search response as Telegram Markdown with source links.
6. **Fetch Latest Papers** — code node that POSTs to Qdrant scroll ordered by `metadata.ingested_at` descending.
7. **Format Latest Reply** — code node that formats the paper list as a numbered Markdown list.
8. **Help Reply** — code node that returns the static command reference message.
9. **Unknown Command Reply** — code node that returns a redirect to `/help`.
10. **Fetch Stats** — code node that scrolls `run_logs`, filters to last 7 days, aggregates metrics, formats a summary table.
11. **Answer Callback Query** — HTTP Request to Telegram's `answerCallbackQuery` API. Required to dismiss the loading spinner on inline keyboard presses.
12. **Route Feedback** (Switch, 3 outputs) — fans out to expand/skip/save handlers after the callback is answered.
13. **Handle Expand** — code node: scrolls Qdrant for the paper by URL, calls GPT-4o for a deep analysis (methodology, limitations, related work, applications).
14. **Handle Skip** — code node: scrolls Qdrant for the point ID, PATCHes payload with `user_rating: -1` and `user_rated_at`.
15. **Handle Save** — code node: scrolls Qdrant for the point ID, PATCHes payload with `user_rating: 1` and `user_rated_at`.
16. **Send Reply** — single Telegram node. All branches write `{ chatId, text }` and converge here.

---

### 4. `Weekly_Digest.json` — Saturday morning research roundup (9 nodes)

Synthesises the week's papers into a narrative digest and sends it to Telegram and Discord.

**Nodes in order:**

1. **Saturday 9am Trigger** — cron: `0 9 * * 6`.
2. **Compute Date Window** — code node that computes `since` (7 days ago as ISO string) and a human-readable `weekLabel`.
3. **Fetch Week Papers** — code node that paginates through the full `arxiv_papers` collection (100 pts/page), filters by `ingested_at >= since`, and throws `NO_PAPERS_THIS_WEEK` if the result is empty.
4. **Group by Tag** — code node that groups papers by primary tag, sorts groups by count descending, and builds `topPicks` preferring saved papers (`user_rating: 1`) over High-relevance ones.
5. **GPT-4o Digest** — HTTP Request to OpenAI. `response_format: json_object`. Produces `{ overall_trend, group_narratives[], top_3_picks[] }`.
6. **Format Digest** — code node that assembles a Telegram Markdown message with trend, per-group narratives, and top-3 picks.
7. **Send Telegram Digest** — Telegram node.
8. **Send Discord Digest** — HTTP Request to Discord webhook.
9. **No Papers This Week** — Telegram node on the error branch from node 3.

---

### 5. `Error_Handler.json` — Global error alerting (3 nodes)

1. **Error Trigger** — n8n's built-in error trigger; fires when any active workflow execution fails.
2. **Telegram Notification** — sends alert with workflow name, failing node, error message, and execution link to `$vars.TELEGRAM_CHAT_ID`.
3. **Email Notification** — sends the same alert to `$vars.ALERT_TO_EMAIL` from `$vars.ALERT_FROM_EMAIL`.

All credentials are read from n8n variables (`$vars.*`). No hardcoded values.

---

### Legacy: `Arxiv_Semantic_Search.json` (5 nodes)

An earlier implementation of the semantic search feature using n8n's built-in AI Agent node with a Qdrant Retriever. Superseded by `Semantic_Search_Agent.json`, which uses a custom RAG pipeline with explicit embedding, vector search, and structured GPT-4o output. Kept in the repository for reference.
