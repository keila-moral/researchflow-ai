# Script and Workflow Explanation

This document provides a detailed breakdown of the components in the `researchflow-ai` repository.

## 1. Local Scripts

### `scripts/parse_xml.py`

A utility script used to process the XML response from the arXiv API.

- **Purpose**: arXiv returns search results in Atom (XML) format. This script converts that XML into a clean, structured JSON format that is easier for other tools (like LLMs or databases) to consume.
- **Key Functions**:
  - `parse_arxiv_xml(xml_content)`: Uses the `feedparser` library to iterate through the entries in the XML.
  - **Extracted Fields**:
    - `id`: The unique arXiv URL/ID of the paper.
    - `title`: The title of the paper (newlines removed).
    - `summary`: The abstract of the paper (newlines removed).
    - `published`: The publication date.
    - `link`: The direct link to the paper.
- **Usage**: Called via command line with an XML file path: `python scripts/parse_xml.py sample_arxiv.xml`.

---

## 2. n8n Workflows

There are three workflows in `workflows/`: the main ingestion pipeline, the semantic search agent, and a shared error handler.

### Arxiv Monitor & Process (`Arxiv_Monitor.json`)

The core "fetch → summarize → store → notify" pipeline. It runs across **six arXiv categories** and includes deduplication, a PDF-download step with a graceful fallback, and dual notification channels.

1. **Schedule Trigger** — runs on a daily schedule.
2. **Define Categories** — sets the list of categories to monitor: `cs.AI`, `cs.LG`, `cs.CV`, `cs.CL`, `cs.RO`, `stat.ML`.
3. **Loop Categories** (`splitInBatches`) + **Wait** — iterates through each category one at a time with a delay between calls, to stay within arXiv's rate limits.
4. **Fetch Arxiv** (HTTP Request) — queries the arXiv API for each category.
5. **Process Results** / **Merge Papers** — normalizes and merges results from all categories into a single list.
6. **Check If Exists** — queries Qdrant's `arxiv_papers` collection (`POST /collections/arxiv_papers/points/scroll`) for each paper's ID/URL to skip papers that have already been processed.
7. **Filter Processed** — drops any papers flagged as already processed in the previous step.
8. **Get PDF URL** — derives the direct PDF link from the paper's abstract URL (`/abs/` → `/pdf/`).
9. **Download PDF** — fetches the full PDF for summarization.
10. **PDF Fallback (Abstract Only)** — error-branch node. If the PDF download or summarization chain fails, this reconstructs the expected data shape using just the paper's abstract, tags the item with `used_fallback: true`, and lets the pipeline continue rather than failing outright.
11. **Summarization Chain** — a LangChain sub-pipeline (`OpenAI Chat Model` + `Recursive Character Text Splitter` + `Default Data Loader` + `Summarization Chain`) that chunks the PDF text and produces a summary, since full papers are typically too long for a single prompt.
12. **OpenAI Summarize** — a structured-output call that returns the core contribution, methodology, practical relevance, a relevance rating, a Telegram-formatted summary, and a list of tags as JSON.
13. **Extract Tags** — parses the structured JSON from the previous step, pulls out `tags`, and prepends a "⚠️ summary generated from abstract only" warning to the Telegram summary when the fallback path was used.
14. **Qdrant Store** — upserts the paper's summary and embedding into the `arxiv_papers` collection.
15. **Generate Thumbnail** / **Extract Image URL** / **Persist Thumbnail** — builds and stores a thumbnail image to accompany the notification.
16. **Send Telegram Notification** and **Send Discord Notification** — both channels are notified with the paper's link, summary, tags, and thumbnail.

### Arxiv Semantic Search (`Arxiv_Semantic_Search.json`)

Enables "chat with your research" capabilities.

1. **Webhook Trigger** — listens for incoming POST requests (e.g. from a mobile app, Telegram, or a simple curl command).
2. **AI Agent** — an orchestrator that interprets the user's question.
3. **OpenAI Chat Model** — the model (GPT-4o) that decides how to answer.
4. **Qdrant Retriever** — lets the agent retrieve relevant papers from the vector database based on the query's meaning.
5. **Window Buffer Memory** — retains conversation context for follow-up questions.

### Error Handler (`Error_Handler.json`)

A shared error-handling workflow, triggered whenever another workflow in the instance fails.

1. **Error Trigger** — fires automatically when an active workflow (e.g. Arxiv Monitor) throws an unhandled error.
2. **Telegram Notification** — sends an alert to Telegram with the failure details.
3. **Email Notification** — sends the same alert via email.

This gives visibility into pipeline failures (e.g. arXiv API outages, OpenAI/Qdrant errors) without needing to check the n8n UI manually.

---

## 3. Infrastructure

### `docker-compose.yml`

Defines four services:

- **traefik** — reverse proxy that terminates HTTPS for the stack and provisions TLS certificates automatically via Let's Encrypt (`ACME_EMAIL`). n8n is routed through Traefik using its own domain (`N8N_DOMAIN`) rather than being exposed on `localhost`.
- **qdrant** — the vector database for storing/searching paper embeddings. It does not publish any ports externally; it's only reachable internally at `http://qdrant:6333` by other containers on the same Docker network.
- **n8n** — the workflow engine, served over HTTPS behind Traefik at `https://${N8N_DOMAIN}/`.
- **backup** — an `offen/docker-volume-backup` service that periodically snapshots the `qdrant_data` and `n8n_data` volumes (default: daily at 3am, configurable via `BACKUP_CRON`), with a configurable retention window (`BACKUP_RETENTION_DAYS`, default 14 days) and optional offsite storage to an S3-compatible bucket (`AWS_S3_BUCKET_NAME`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_ENDPOINT`). It's configured to stop `qdrant` and `n8n` for the duration of each backup so volumes aren't captured mid-write.

**Volumes**: `n8n_data`, `qdrant_data`, `traefik_data`, `backup_data` — all persisted so data survives container restarts.

Environment variables are configured via `env.example` at the repo root (copy to `.env` and fill in before running `docker-compose up -d`).

### `.github/workflows/semgrep.yml`

A CI workflow that runs [Semgrep](https://semgrep.dev/) static analysis on every pull request, every push to `main`/`master`, and on a daily schedule, to catch security issues in the codebase automatically.
