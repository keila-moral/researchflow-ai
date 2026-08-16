#!/usr/bin/env python3
"""
migrate_qdrant_schema.py
─────────────────────────────────────────────────────────────────
One-off migration: normalise all points in the arxiv_papers
Qdrant collection so the paper URL is stored under metadata.url
rather than the legacy top-level url field.

Background
──────────
Early versions of Arxiv_Monitor stored the arXiv paper ID/URL at
the top-level payload key `url`. Later versions moved it to
`metadata.url`. The Check If Exists node previously worked around
this with a `should` (OR) filter querying both paths. This script
migrates all legacy points so Check If Exists can use a single
`must` filter on `metadata.url`.

Usage
─────
Run this once after deploying the Day 21 workflow update:

    # From inside the Docker network (e.g. via docker exec):
    docker exec -it n8n python3 /files/migrate_qdrant_schema.py

    # Or from the host if you temporarily expose Qdrant on 6333:
    QDRANT_HOST=localhost python3 migrate_qdrant_schema.py

Environment variables
─────────────────────
    QDRANT_HOST      Qdrant hostname (default: qdrant)
    QDRANT_PORT      Qdrant port     (default: 6333)
    QDRANT_API_KEY   Qdrant API key  (default: empty — set if auth is enabled)
    COLLECTION       Collection name (default: arxiv_papers)
    DRY_RUN          Set to "true" to preview changes without writing (default: false)
"""

import os
import sys
import json
import time
import urllib.request
import urllib.error
from typing import Any

# ── Config ────────────────────────────────────────────────────────
QDRANT_HOST  = os.getenv("QDRANT_HOST", "qdrant")
QDRANT_PORT  = os.getenv("QDRANT_PORT", "6333")
QDRANT_KEY   = os.getenv("QDRANT_API_KEY", "")
COLLECTION   = os.getenv("COLLECTION", "arxiv_papers")
DRY_RUN      = os.getenv("DRY_RUN", "false").lower() == "true"
BASE_URL     = f"http://{QDRANT_HOST}:{QDRANT_PORT}"
PAGE_SIZE    = 100

HEADERS: dict[str, str] = {"Content-Type": "application/json"}
if QDRANT_KEY:
    HEADERS["api-key"] = QDRANT_KEY


# ── Helpers ───────────────────────────────────────────────────────
def request(method: str, path: str, body: Any = None) -> Any:
    url  = f"{BASE_URL}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req  = urllib.request.Request(url, data=data, headers=HEADERS, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} {method} {path}: {e.read().decode()}") from e


def scroll_all(collection: str) -> list[dict]:
    """Paginate through the entire collection and return all points."""
    points: list[dict] = []
    offset = None
    page   = 0
    while True:
        body: dict = {"limit": PAGE_SIZE, "with_payload": True, "with_vectors": False}
        if offset:
            body["offset"] = offset
        result = request("POST", f"/collections/{collection}/points/scroll", body)
        batch  = result.get("result", {}).get("points", [])
        points.extend(batch)
        offset = result.get("result", {}).get("next_page_offset")
        page  += 1
        print(f"  Scrolled page {page}: {len(batch)} points (total so far: {len(points)})")
        if not offset:
            break
        time.sleep(0.05)   # be gentle
    return points


def needs_migration(payload: dict) -> bool:
    """
    A point needs migration if it has a top-level 'url' key but
    either has no 'metadata' sub-object or 'metadata.url' is missing/empty.
    """
    top_url  = payload.get("url", "")
    meta     = payload.get("metadata", {})
    meta_url = meta.get("url", "") if isinstance(meta, dict) else ""
    return bool(top_url) and not meta_url


def build_normalised_payload(payload: dict) -> dict:
    """
    Move top-level 'url' into metadata.url.
    Preserve all other existing metadata fields.
    """
    top_url = payload.get("url", "")
    meta    = dict(payload.get("metadata", {}) or {})
    meta["url"] = top_url

    new_payload = {k: v for k, v in payload.items() if k != "url"}
    new_payload["metadata"] = meta
    return new_payload


# ── Main ──────────────────────────────────────────────────────────
def main() -> None:
    print(f"ResearchFlow AI — Qdrant schema migration")
    print(f"Collection : {COLLECTION}")
    print(f"Qdrant     : {BASE_URL}")
    print(f"Dry run    : {DRY_RUN}")
    print()

    # Verify collection exists
    try:
        info = request("GET", f"/collections/{COLLECTION}")
        count = info.get("result", {}).get("points_count", "?")
        print(f"Collection '{COLLECTION}' found — {count} points total\n")
    except RuntimeError as e:
        print(f"ERROR: Cannot reach collection: {e}")
        sys.exit(1)

    print("Scrolling all points...")
    all_points = scroll_all(COLLECTION)
    print(f"\nTotal points retrieved: {len(all_points)}")

    # Identify points needing migration
    to_migrate = [
        p for p in all_points
        if needs_migration(p.get("payload", {}))
    ]
    already_ok  = len(all_points) - len(to_migrate)

    print(f"Already normalised (metadata.url present): {already_ok}")
    print(f"Needs migration   (top-level url only)   : {len(to_migrate)}")

    if not to_migrate:
        print("\nNothing to migrate. Schema is already consistent.")
        return

    if DRY_RUN:
        print("\nDRY RUN — no changes written. First 5 that would be migrated:")
        for p in to_migrate[:5]:
            pl  = p["payload"]
            old = pl.get("url", "")
            print(f"  id={p['id']}  url={old!r}")
        return

    # Batch-update in groups of 100
    print(f"\nMigrating {len(to_migrate)} points...")
    batch_size = 100
    migrated   = 0
    errors     = 0

    for i in range(0, len(to_migrate), batch_size):
        batch  = to_migrate[i:i + batch_size]
        points = [
            {
                "id":      p["id"],
                "payload": build_normalised_payload(p["payload"])
            }
            for p in batch
        ]
        try:
            request("PUT", f"/collections/{COLLECTION}/points/payload",
                    {"points": [p["id"] for p in batch],
                     "payload": {}})
            # Overwrite payloads individually so we don't lose existing fields.
            # Use the upsert endpoint with the full normalised payload.
            upsert_body = {
                "points": [
                    {"id": p["id"], "vector": [], "payload": build_normalised_payload(batch[j]["payload"])}
                    for j, p in enumerate(points)
                ]
            }
            request("PUT", f"/collections/{COLLECTION}/points", upsert_body)
            migrated += len(batch)
            print(f"  Migrated batch {i // batch_size + 1}: {len(batch)} points (total: {migrated})")
        except RuntimeError as e:
            errors += len(batch)
            print(f"  ERROR on batch {i // batch_size + 1}: {e}")
        time.sleep(0.1)

    print(f"\nMigration complete.")
    print(f"  Migrated : {migrated}")
    print(f"  Errors   : {errors}")

    if errors == 0:
        print("\nAll points now use metadata.url. Safe to remove the legacy 'url' field filter.")
    else:
        print("\nSome batches failed. Re-run the script to retry — it is safe to run multiple times.")


if __name__ == "__main__":
    main()
