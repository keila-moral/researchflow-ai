# ResearchFlow AI — Testing Guide

Manual test checklist for all five workflows. Run these after initial setup or after importing updated workflow files.

---

## Prerequisites

- All five workflows imported and active in n8n
- n8n Variables set: `N8N_DOMAIN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_BOT_TOKEN`, `DISCORD_WEBHOOK_URL`, `QDRANT_API_KEY`, `OPENAI_API_KEY`, `ALERT_FROM_EMAIL`, `ALERT_TO_EMAIL`
- Qdrant running and reachable at `http://qdrant:6333` from n8n
- At least one test paper ingested (run the monitor manually once first)

---

## 1. Arxiv Monitor

### 1a. Manual trigger — happy path

1. Open `Arxiv_Monitor` in n8n → click **Execute Workflow**.
2. Watch the execution panel. Verify:
   - `Define Categories` outputs a list of category strings.
   - `Loop Categories` iterates through each one.
   - `Fetch Arxiv` returns HTTP 200 for each category.
   - `Check If Exists` runs without error (Qdrant reachable).
   - At least one paper reaches `OpenAI Summarize`.
   - `Extract Tags` outputs a `tags` array (not empty, not a string).
   - `Relevance Router` routes to one of the three branches.
   - `Qdrant Store` completes without error.
   - `Log Run` completes and a run summary appears in Telegram.

**Expected Telegram message (end of run):**
```
📊 ResearchFlow run complete
🗂 Fetched: X · Already seen: Y · New: Z
✅ High: N · ⚡ Medium: N · 🔇 Low: N
⏱ Duration: Xs
```

### 1b. Deduplication check

1. Run the monitor twice in succession.
2. On the second run, verify `Filter Processed` drops all papers (all `already_processed: true`).
3. `Log Run` should show `New: 0` in the Telegram summary.

### 1c. PDF fallback

1. Temporarily change `Get PDF URL` to output a broken URL (e.g. `https://arxiv.org/pdf/0000.00000`).
2. Run the monitor.
3. Verify `PDF Fallback (Abstract Only)` fires and the paper still reaches `OpenAI Summarize`.
4. Telegram notification should include `⚠️ PDF unavailable — summary generated from abstract only`.
5. Qdrant payload for that paper should have `used_fallback: "true"`.
6. Revert `Get PDF URL` after the test.

### 1d. Relevance routing

1. After a successful run, open Qdrant at `http://localhost:6333/dashboard` (temporarily expose port for debugging, or use n8n's HTTP Request node).
2. Scroll `arxiv_papers` — verify points have `metadata.relevance` set to `High`, `Medium`, or `Low`.
3. Verify `metadata.tags` is an array (not a comma-separated string).
4. Verify `metadata.thumbnail_url` for High papers starts with `https://<N8N_DOMAIN>/static/thumbnails/`.
5. Verify the static thumbnail is actually accessible: `curl -I https://<N8N_DOMAIN>/static/thumbnails/<id>.png` → HTTP 200.

### 1e. Run log

```bash
# Verify run_logs collection exists and has entries
curl -s -X POST http://qdrant:6333/collections/run_logs/points/scroll \
  -H "Content-Type: application/json" \
  -H "api-key: <QDRANT_API_KEY>" \
  -d '{"limit": 5, "with_payload": true}' | python3 -m json.tool
```

Expected: one point per pipeline run with `run_id`, `timestamp`, `processed`, `high`, `medium`, `low`, `pdf_fallbacks`, `duration_ms`.

---

## 2. Semantic Search Agent

### 2a. Basic query

```bash
curl -X POST https://<N8N_DOMAIN>/webhook/search \
  -H "Content-Type: application/json" \
  -d '{"query": "What is retrieval-augmented generation?", "top_k": 3}'
```

Expected response shape:
```json
{
  "query": "...",
  "answer": "...",
  "sources": [{"title": "...", "url": "...", "relevance_to_query": "..."}],
  "confidence": "High|Medium|Low",
  "result_count": 3,
  "retrieved_papers": [...]
}
```

Verify:
- `answer` contains inline `[Paper Title]` citations.
- `sources` titles match papers in `retrieved_papers`.
- `confidence` is one of `High`, `Medium`, `Low`.

### 2b. Tag filter

```bash
curl -X POST https://<N8N_DOMAIN>/webhook/search \
  -H "Content-Type: application/json" \
  -d '{"query": "transformer architecture", "top_k": 5, "tag_filter": "LLMs"}'
```

Verify: all `retrieved_papers` have `tags` containing `"LLMs"`.

### 2c. Missing query (validation)

```bash
curl -X POST https://<N8N_DOMAIN>/webhook/search \
  -H "Content-Type: application/json" \
  -d '{"top_k": 5}'
```

Expected: HTTP 500 with `{ "error": "Missing required field: query", ... }`.

### 2d. Empty results (low-similarity query)

```bash
curl -X POST https://<N8N_DOMAIN>/webhook/search \
  -H "Content-Type: application/json" \
  -d '{"query": "medieval castle architecture 14th century", "top_k": 3}'
```

Expected: `result_count: 0` and `answer` stating no relevant papers were found. Should not error.

---

## 3. Telegram Query Bot

Open a Telegram conversation with your bot and test each command.

### 3a. /search

```
/search What are the main approaches to few-shot learning?
```

Expected: message with answer text, inline `[Paper Title]` citations, and a numbered source list with links.

### 3b. /latest

```
/latest 3
```

Expected: numbered list of 3 papers with title (linked to arXiv), tags, and relevance rating.

```
/latest
```

Expected: same but with 5 papers (default).

### 3c. /stats

```
/stats
```

Expected message format:
```
📊 ResearchFlow — 7-day stats (N runs)

📥 Fetched: X · Already seen: Y · New: Z
✅ High: N · ⚡ Medium: N · 🔇 Low: N
⚠️ PDF fallback rate: X.X%
⏱ Avg run time: Xs

Recent runs:
  DD Mon: N new (H:N M:N L:N)
  ...

📦 Total runs logged: N
```

### 3d. /help

```
/help
```

Expected: command reference listing `/search`, `/latest`, `/stats`, `/help`.

### 3e. Unknown command

```
hello there
```

Expected: `❓ Unknown command. Send /help to see available commands.`

### 3f. Feedback buttons (Expand)

1. Wait for or trigger a High-relevance paper notification — it should arrive with three inline buttons.
2. Tap **🔍 Expand**.
3. Verify:
   - Telegram spinner dismissed immediately (callback answered).
   - Follow-up message arrives with a deeper analysis covering methodology and limitations.
   - Message ends with the arXiv link.

### 3g. Feedback buttons (Skip)

1. On a paper notification, tap **⏭ Skip**.
2. Verify Telegram replies: `⏭ Got it — this paper will be deprioritised in future searches.`
3. Verify in Qdrant that the paper's payload has `metadata.user_rating: -1` and `metadata.user_rated_at` set.

```bash
# Check via Qdrant scroll with filter
curl -s -X POST http://qdrant:6333/collections/arxiv_papers/points/scroll \
  -H "Content-Type: application/json" \
  -H "api-key: <QDRANT_API_KEY>" \
  -d '{"filter": {"must": [{"key": "metadata.user_rating", "match": {"value": -1}}]}, "limit": 5, "with_payload": true}'
```

### 3h. Feedback buttons (Save)

1. On a paper notification, tap **🔖 Save**.
2. Verify Telegram replies with a confirmation message.
3. Verify `metadata.user_rating: 1` in Qdrant.
4. After the next weekly digest runs, verify the saved paper appears in the top-3 picks section.

---

## 4. Weekly Digest

### 4a. Manual trigger

1. Open `Weekly_Digest` in n8n → **Execute Workflow**.
2. Verify:
   - `Fetch Week Papers` returns at least one paper (run the monitor first if needed).
   - `Group by Tag` outputs a `groups` array with at least one entry.
   - `GPT-4o Digest` returns valid JSON with `overall_trend`, `group_narratives`, `top_3_picks`.
   - A digest message arrives in Telegram.
   - A digest embed arrives in Discord.

**Expected Telegram message structure:**
```
📰 ResearchFlow Weekly Digest
_Week of DD Mon–DD Mon YYYY · N papers indexed_

🌐 This week's trend: ...

🏷️ LLMs
[2-3 sentence narrative]

🏷️ RAG
[2-3 sentence narrative]

⭐ Papers of the week
1. *Paper title*
   _One sentence on why it's a standout_

_Use /search to ask questions about any of these papers._
```

### 4b. No papers this week

1. Temporarily change `Compute Date Window` to set `since` to tomorrow's date (so no papers fall in range).
2. Run the workflow.
3. Verify: `No Papers This Week` node fires and Telegram receives `📭 ResearchFlow Weekly Digest\n\nNo new papers were indexed this week.`
4. Revert `Compute Date Window`.

### 4c. Saved papers in top picks

1. Save at least one paper via the 🔖 button.
2. Run the weekly digest.
3. Verify the saved paper appears in `⭐ Papers of the week`.

---

## 5. Error Handler

### 5a. Trigger a test error

1. Open any workflow (e.g. `Semantic_Search_Agent`).
2. Temporarily set an invalid Qdrant URL in `Qdrant Vector Search` (e.g. `http://qdrant:9999/...`).
3. Execute the workflow — it should fail.
4. Verify:
   - A Telegram alert arrives with the workflow name, failing node name, and error message.
   - If email is configured, an email alert arrives.
5. Revert the URL.

---

## 6. Schema migration (one-time check)

Run the migration in dry-run mode to confirm no legacy points remain:

```bash
docker exec -it n8n sh -c "DRY_RUN=true python3 /files/migrate_qdrant_schema.py"
```

Expected output after migration:
```
Already normalised (metadata.url present): N
Needs migration   (top-level url only)   : 0
Nothing to migrate. Schema is already consistent.
```

---

## 7. Infrastructure checks

```bash
# Qdrant is NOT accessible from outside Docker (no host port)
curl http://<SERVER_IP>:6333/healthz
# Expected: connection refused

# n8n HTTPS works
curl -I https://<N8N_DOMAIN>
# Expected: HTTP 200 or 302

# Thumbnail static files accessible
curl -I https://<N8N_DOMAIN>/static/thumbnails/<any-paper-id>.png
# Expected: HTTP 200

# Qdrant collections exist
curl -s http://localhost:6333/collections \
  -H "api-key: <QDRANT_API_KEY>" | python3 -m json.tool
# Expected: arxiv_papers and run_logs both listed
```

---

## Quick smoke test (30 seconds)

Run this after any workflow update to confirm nothing is broken:

```bash
# 1. Search endpoint responds
curl -s -X POST https://<N8N_DOMAIN>/webhook/search \
  -H "Content-Type: application/json" \
  -d '{"query": "test", "top_k": 1}' | python3 -c "import sys,json; d=json.load(sys.stdin); print('OK' if 'answer' in d else 'FAIL')"

# 2. Qdrant has papers
curl -s -X POST http://qdrant:6333/collections/arxiv_papers/points/count \
  -H "Content-Type: application/json" \
  -H "api-key: <QDRANT_API_KEY>" \
  -d '{}' | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Papers: {d[\"result\"][\"count\"]}')"

# 3. Run logs exist
curl -s -X POST http://qdrant:6333/collections/run_logs/points/count \
  -H "Content-Type: application/json" \
  -H "api-key: <QDRANT_API_KEY>" \
  -d '{}' | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Run logs: {d[\"result\"][\"count\"]}')"
```
