# Script and Workflow Explanation

This document provides a detailed breakdown of the components in the `researchflow-ai` repository.

## 1. Local Scripts

### `scripts/parse_xml.py`
This is a utility script used to process the XML response from the arXiv API.

- **Purpose**: arXiv returns search results in Atom (XML) format. This script converts that XML into a clean, structured JSON format that is easier for other tools (like LLMs or databases) to consume.
- **Key Functions**:
    - `parse_arxiv_xml(xml_content)`: Uses the `feedparser` library to iterate through the entries in the XML.
    - **Extracted Fields**:
        - `id`: The unique arXiv URL/ID of the paper.
        - `title`: The title of the paper (newlines removed).
        - `summary`: The abstract of the paper (newlines removed).
        - `published`: The publication date.
        - `link`: The direct link to the paper.
- **Usage**: Typically called via command line with an XML file path: `python parse_xml.py sample.xml`.

---

## 2. n8n Workflows

### Arxiv Monitor & Process (`Arxiv_Monitor.json`)
This is the core automation workflow. It handles the "Fetch -> Summarize -> Store" pipeline.

1.  **Schedule Trigger**: Runs once a day (default: 9:00 AM) to check for new research.
2.  **Fetch Arxiv (HTTP Request)**: Calls the arXiv API (e.g., `https://export.arxiv.org/api/query?search_query=cat:cs.AI`) to get the latest 10 papers.
3.  **Process Results (Code Node)**: A simple pass-through or normalization step to ensure the data structure is ready for the LLM.
4.  **OpenAI Summarize (AI Node)**:
    - **Model**: GPT-4o.
    - **Prompt**: Each paper is processed by GPT-4o using a structured prompt that extracts the core contribution, methodology, and practical significance, then rates its relevance for developers working on LLMs, RAG systems, or AI automation pipelines.
5.  **Qdrant Store (Vector Store Node)**:
    - **Action**: Upserts the paper abstract and metadata into a Qdrant collection named `arxiv_papers`.
    - **Embeddings**: Uses OpenAI embeddings to turn text into vectors for semantic search.

### Arxiv Semantic Search (`Arxiv_Semantic_Search.json`)
This workflow enables "Chat with your Research" capabilities.

1.  **Webhook Trigger**: Listens for incoming POST requests (e.g., from a mobile app, Telegram, or a simple curl command).
2.  **AI Agent**: An orchestrator that interprets the user's question.
3.  **OpenAI Chat Model**: The "brain" (GPT-4o) that decides how to answer.
4.  **Qdrant Retriever**: Allows the agent to "look up" relevant papers from the vector database based on the query's meaning.
5.  **Window Buffer Memory**: Remembers the context of the conversation for follow-up questions.

---

## 3. Infrastructure

### `docker-compose.yml`
Defines the environment:
- **n8n**: The workflow engine.
- **Qdrant**: The vector database for storing/searching paper embeddings.
- **Volumes**: Ensures your data is persisted even if containers are restarted.
