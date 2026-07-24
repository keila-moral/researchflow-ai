# ResearchFlow AI

**An automated arXiv research assistant, built entirely with n8n.**

ResearchFlow AI monitors arXiv for new papers in categories you choose, summarizes them with an LLM, and indexes their embeddings in [Qdrant](https://qdrant.tech/) so you can search your growing research library by meaning, not just keywords. New papers are pushed to Telegram or Discord automatically, and a webhook lets you "chat" with everything you've collected.

`n8n` · `OpenAI GPT-4o` · `Qdrant` · `Docker` · `Python` · `REST/Webhooks` · `Telegram/Discord APIs`

**Skills demonstrated:**
- **Workflow automation** — a scheduled, multi-step pipeline (fetch → transform → summarize → store → notify) orchestrated in n8n with no manual intervention.
- **LLM integration** — structured prompt design for consistent, extractable summaries (contribution, methodology, relevance) rather than free-text output.
- **Retrieval-Augmented Generation (RAG)** — embedding generation, vector storage, and semantic retrieval via Qdrant, plus a conversational agent with memory.
- **API design** — a webhook interface for programmatic, natural-language querying over the indexed corpus.
- **Infrastructure as code** — reproducible local environment via Docker Compose.

## Table of Contents

- [How It Works](#how-it-works)
- [Features](#features)
- [Screenshots](#screenshots)
- [Quick Start](#quick-start)
- [Workflows](#workflows)
- [Scripts](#scripts)
- [Configuration](#configuration)
- [Project Structure](#project-structure)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [About the Author](#about-the-author)

## How It Works

There are two independent flows that share the same Qdrant store: a scheduled pipeline that ingests and summarizes new papers, and an on-demand pipeline that answers questions about them.

**Pipeline 1: Ingest & Notify** (runs on a schedule)

```
arXiv API → n8n (fetch + parse) → GPT-4o (summarize) → Qdrant (store embedding)
                                                            │
                                                            └──→ Telegram / Discord (notify)
```

**Pipeline 2: Ask Questions** (runs on demand, via webhook)

```
You → Webhook → n8n Agent → Qdrant (semantic search) → GPT-4o (answer) → You
```

1. A scheduled n8n workflow polls the arXiv API for new papers in your chosen categories.
2. Each paper is summarized by an LLM, which extracts the core contribution, methodology, and practical relevance.
3. The summary and its embedding are stored in Qdrant for semantic retrieval, and a notification goes out to Telegram/Discord.
4. Separately, a webhook-driven agent lets you ask natural-language questions across everything that's been indexed, retrieving relevant context from Qdrant before answering.

## Features

- **Daily Monitoring** — Fetches new papers from arXiv categories (e.g. `cs.AI`) on a schedule.
- **AI Summarization** — Generates concise, structured summaries with GPT-4o, rated for relevance to LLM/RAG/AI-automation work.
- **Vector Search** — Stores paper embeddings in Qdrant for semantic querying, not just keyword match.
- **Multi-Channel Notifications** — Sends new-paper alerts to Telegram or Discord.
- **Semantic Querying** — Ask questions about your indexed corpus via a webhook/chat interface with conversational memory.

## Screenshots

<!-- Replace with actual images from the screenshots/ folder, e.g.: -->
<!-- ![n8n workflow overview](screenshots/workflow-overview.png) -->
<!-- ![Semantic search in action](screenshots/semantic-search.png) -->

See the [`screenshots/`](screenshots/) folder for the n8n workflow canvas and example query results.

## Quick Start

### 1. Prerequisites

- [Docker](https://www.docker.com/) and Docker Compose
- An OpenAI API key
- (Optional) a Telegram bot token or Discord webhook URL, for notifications

### 2. Set Up Infrastructure

```bash
git clone https://github.com/keila-moral/researchflow-ai.git
cd researchflow-ai
cp .env.example .env   # then fill in your API keys
docker-compose up -d
```

Once running:

| Service | URL |
|---|---|
| n8n | http://localhost:5678 |
| Qdrant dashboard | http://localhost:6333/dashboard |

### 3. Import the Workflows

1. Open n8n in your browser.
2. Go to **Workflows**.
3. For each `.json` file in [`workflows/`](workflows/), click **Import from File** and select it.
4. Configure credentials (OpenAI, Qdrant, Telegram/Discord, etc.) for any nodes that need them.
5. Activate the workflows.

## Workflows

### Arxiv Monitor & Process (`workflows/Arxiv_Monitor.json`)

The core "fetch → summarize → store" pipeline:

1. **Schedule Trigger** — runs daily (default 9:00 AM).
2. **Fetch Arxiv** — queries the arXiv API for the latest papers in the configured category.
3. **Process Results** — normalizes the raw response for the LLM step.
4. **Summarize (GPT-4o)** — extracts contribution, methodology, and relevance from each paper.
5. **Store in Qdrant** — upserts the summary and its embedding into the `arxiv_papers` collection.
6. **Notify** — sends the paper link and summary to Telegram/Discord.

### Arxiv Semantic Search (`workflows/Arxiv_Semantic_Search.json`)

A "chat with your research" agent:

1. **Webhook Trigger** — accepts POST requests (e.g. from curl, a bot, or a custom UI).
2. **AI Agent** — interprets the incoming question.
3. **Qdrant Retriever** — pulls relevant abstracts from the vector store based on meaning.
4. **GPT-4o** — answers using the retrieved context.
5. **Buffer Memory** — retains context across follow-up questions in the same conversation.

## Scripts

### `scripts/parse_xml.py`

Standalone utility for converting arXiv's Atom/XML API responses into clean JSON.

- `parse_arxiv_xml(xml_content)` parses entries with `feedparser` and extracts `id`, `title`, `summary`, `published`, and `link`.
- Useful for testing or debugging outside of n8n.

Usage:

```bash
python scripts/parse_xml.py sample_arxiv.xml
```

See [`script_explanation.md`](script_explanation.md) for a full breakdown of every script and workflow node.

## Configuration

- **Monitored topics** — edit the `HTTP Request` node in the **Arxiv Monitor** workflow (defaults to `cs.AI`). Any valid [arXiv category](https://arxiv.org/category_taxonomy) works.
- **Schedule** — adjust the `Schedule Trigger` node to change how often new papers are fetched.
- **Notification channel** — enable/configure the Telegram or Discord node depending on which you use.
- **Qdrant collection** — the default collection name is `arxiv_papers`; change it in the Qdrant nodes if you want to run multiple independent corpora.

## Project Structure

```
researchflow-ai/
├── workflows/         # n8n workflow definitions (import these into n8n)
├── scripts/           # Standalone Python utilities (e.g. parse_xml.py)
├── screenshots/        # UI screenshots for documentation
├── docker-compose.yml  # n8n + Qdrant infrastructure
├── sample_arxiv.xml    # Sample arXiv API response for testing parse_xml.py
├── script_explanation.md # Detailed breakdown of scripts and workflow nodes
└── README.md
```

## Roadmap

See [`optimization_todo_updated.md`](optimization_todo_updated.md) for planned improvements and open tasks.

## Contributing

Issues and pull requests are welcome. If you add a new workflow or script, please update `script_explanation.md` so the docs stay in sync with the code.

## About the Author

Built by [Keila Moral](https://github.com/keila-moral).[Check out more projects here!](https://linktr.ee/kmoralfig)

Questions, feedback, or ideas for extending it? Open an issue or reach out directly.
