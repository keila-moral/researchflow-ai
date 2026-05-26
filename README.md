# AI-Powered arXiv Research Assistant (n8n)

An automated research assistant that monitors arXiv, summarizes new papers using LLMs, stores them in a vector database (Qdrant), and provides a semantic search interface via Telegram/Discord.

## Features
- **Daily Monitoring**: Fetches new papers from arXiv categories (e.g., `cs.AI`).
- **AI Summarization**: Generates concise, human-readable summaries using GPT-4.
- **Vector Search**: Stores paper embeddings in Qdrant for semantic querying.
- **Multi-Channel Notifications**: Sends updates to Telegram or Discord.
- **Semantic Querying**: Ask questions about the stored papers via a webhook/chat interface.

## Quick Start

### 1. Prerequisites
- [Docker](https://www.docker.com/) and Docker Compose.
- OpenAI API Key.
- (Optional) Telegram Bot Token or Discord Webhook URL.

### 2. Setup Infrastructure
1. Clone this repository.
2. Copy `.env.example` to `.env` and fill in your API keys.
3. Start the services:
   ```bash
   docker-compose up -d
   ```
   - n8n will be available at `http://localhost:5678`
   - Qdrant dashboard will be available at `http://localhost:6333/dashboard`

### 3. Import Workflows
1. Open n8n in your browser.
2. Go to **Workflows**.
3. For each file in the `workflows/` directory:
   - Click **Import from File**.
   - Select the `.json` file.
   - Configure the credentials (OpenAI, Telegram, etc.) if they aren't automatically set.

## Workflows

### Arxiv Monitor & Process
Automatically triggers daily, fetches papers from arXiv, generates summaries, and saves them to Qdrant. It then sends a notification with the link and summary.

### Semantic Search Agent
A webhook-based flow that allows you to query your stored papers using natural language. It retrieves relevant abstracts from Qdrant and uses an LLM to answer your question.

## Configuration
You can customize the monitored topics by editing the `HTTP Request` node in the **Arxiv Monitor** workflow. By default, it tracks `cs.AI`.
