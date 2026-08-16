"""
Migrate pipeline JSONL vectors → per-doc .embeddings.json files + auto-tag.

Reads vector_index.jsonl for an account, groups chunks by source file,
writes per-doc .embeddings.json files in the account's documents/ dir,
then extracts tags via LLM for each doc.

Usage:
    python -m backend.scripts.migrate_jsonl_to_docs --account admin
    python -m backend.scripts.migrate_jsonl_to_docs --account admin --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR.parent))

from dotenv import load_dotenv

load_dotenv(_BACKEND_DIR.parent / ".env")

from engine.shared.account_paths import account_documents_dir

BASE_DIR = _BACKEND_DIR.parent

logger = logging.getLogger("migrate_jsonl")
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)

_MAX_TAGS = 5
_DELAY_SECONDS = 1.0


def _extract_doc_tags(text: str) -> list[str]:
    try:
        from backend.app.services.llm_json import call_json_model

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                call_json_model(
                    role="tagger",
                    payload={
                        "task": "Extract 3-5 concise topic tags",
                        "text": text[:2000],
                    },
                    fallback={"tags": []},
                    timeout=15,
                )
            )
            tags = result.get("tags", [])
            if isinstance(tags, list):
                return [
                    t.lower().strip()[:50]
                    for t in tags
                    if isinstance(t, str) and t.strip()
                ][:_MAX_TAGS]
            return []
        finally:
            loop.close()
    except Exception:
        logger.debug("Tag extraction failed", exc_info=True)
        return []


def _load_jsonl(account_id: str) -> list[dict]:
    jl_path = (
        BASE_DIR
        / "engine"
        / "accounts"
        / account_id
        / "graph_db"
        / "vector_index.jsonl"
    )
    if not jl_path.exists():
        logger.warning("No vector_index.jsonl for account %s", account_id)
        return []
    rows = []
    with jl_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _group_by_doc_stem(rows: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for row in rows:
        fp = row.get("file_path", "")
        stem = Path(fp).stem
        if stem not in groups:
            groups[stem] = []
        groups[stem].append(row)
    return groups


def migrate(account_id: str, dry_run: bool = False) -> dict:
    rows = _load_jsonl(account_id)
    if not rows:
        return {"groups": 0, "tagged": 0, "skipped": 0}

    groups = _group_by_doc_stem(rows)
    docs_dir = account_documents_dir(base_dir=BASE_DIR, account_id=account_id)
    docs_dir.mkdir(parents=True, exist_ok=True)

    summary = {"groups": len(groups), "tagged": 0, "skipped": 0}

    for stem, chunk_rows in groups.items():
        chunks = []
        for row in chunk_rows:
            chunks.append(
                {
                    "chunk_id": row.get("chunk_id", 0),
                    "text": row.get("text", ""),
                    "vector": row.get("vector", []),
                }
            )

        joined_text = "\n".join(c["text"] for c in chunks if c["text"])
        if not joined_text.strip():
            continue

        tags = _extract_doc_tags(joined_text)
        emb_data = {"tags": tags, "chunks": chunks}

        emb_name = f"{stem}.txt.embeddings.json"
        out_path = docs_dir / emb_name

        if dry_run:
            logger.info("[dry-run] %s  tags=%s", emb_name, tags)
        else:
            out_path.write_text(json.dumps(emb_data, ensure_ascii=True), encoding="utf-8")
            logger.info("WROTE %s  tags=%s", out_path.name, tags)

        summary["tagged" if tags else "skipped"] += 1
        time.sleep(_DELAY_SECONDS)

    logger.info("Placing stub text files for doc discovery...")
    for stem in groups:
        txt_path = docs_dir / f"{stem}.txt"
        if not txt_path.exists():
            txt_path.write_text(
                f"[migrated from pipeline: {stem}]\n", encoding="utf-8"
            )
            if not dry_run:
                logger.debug("  STUB %s", txt_path.name)

    return summary


def main():
    parser = argparse.ArgumentParser(
        description="Migrate JSONL vectors to per-doc embeddings + tag"
    )
    parser.add_argument(
        "--account", type=str, default="admin", help="Account ID"
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    summary = migrate(args.account, dry_run=args.dry_run)

    print()
    print("=" * 50)
    print(f"  MIGRATION COMPLETE ({'dry-run' if args.dry_run else 'done'})")
    print("=" * 50)
    print(f"  Doc groups:  {summary['groups']}")
    print(f"  Tagged:      {summary['tagged']}")
    print(f"  Skipped:     {summary['skipped']}")
    print("=" * 50)


if __name__ == "__main__":
    main()
