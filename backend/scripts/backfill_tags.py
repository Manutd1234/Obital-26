"""
Phase 3 — Tag backfill script.

Scans all existing doc embeddings files across all accounts,
extracts tags via LLM for any doc missing them, and rewrites
the embeddings JSON with tags attached.

Usage:
    python -m backend.scripts.backfill_tags                  # all accounts
    python -m backend.scripts.backfill_tags --account admin  # single account
    python -m backend.scripts.backfill_tags --dry-run        # report only, no writes
    python -m backend.scripts.backfill_tags --limit 5        # max 5 docs per account
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR.parent))

from dotenv import load_dotenv
load_dotenv(_BACKEND_DIR.parent / ".env")

from backend.app.services.storage import (
    BASE_DIR,
    _is_db_storage_mode,
    is_sqlite_mode,
    list_docs,
    save_embeddings,
)
from backend.app.services.db import SessionLocal, is_sqlite_mode as db_is_sqlite
from backend.app.services.models import AccountArtifactModel
from engine.shared.account_paths import (
    account_documents_dir,
    account_output_dir,
)

logger = logging.getLogger("backfill_tags")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

_MAX_TAGS = 5
_DELAY_SECONDS = 1.0  # rate limit between LLM calls


def _extract_doc_tags(text: str) -> list[str]:
    """Extract up to 5 concise topic tags from document text via LLM."""
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


def _load_embeddings_file(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to load embeddings file %s: %s", path, exc)
        return None


def _load_db_embeddings(account_id: str, embeddings_path: str) -> dict | None:
    assert SessionLocal is not None
    with SessionLocal() as db:
        row = (
            db.query(AccountArtifactModel)
            .where(
                AccountArtifactModel.account_id == account_id,
                AccountArtifactModel.relative_path == embeddings_path,
            )
            .first()
        )
        if row is None:
            return None
        try:
            if db_is_sqlite():
                return json.loads(bytes(row.content).decode("utf-8"))
            else:
                logger.warning(
                    "Cannot load encrypted embeddings for %s without DEK. Skipping.",
                    embeddings_path,
                )
                return None
        except Exception as exc:
            logger.warning(
                "Failed to decode DB embeddings %s: %s", embeddings_path, exc
            )
            return None


def _discover_accounts() -> list[str]:
    """Discover all known account IDs."""
    accounts: set[str] = set()

    # From accounts.json
    accounts_json = (
        BASE_DIR / "engine" / "accounts" / "accounts.json"
    )
    if accounts_json.exists():
        try:
            data = json.loads(accounts_json.read_text(encoding="utf-8"))
            users = data.get("users", {})
            accounts.update(str(k) for k in users)
        except Exception as exc:
            logger.warning("Failed to read accounts.json: %s", exc)

    # From account directories on disk
    accounts_root = BASE_DIR / "engine" / "accounts"
    if accounts_root.exists():
        for entry in accounts_root.iterdir():
            if entry.is_dir() and (entry / "documents").exists():
                accounts.add(entry.name)

    # From DB
    if _is_db_storage_mode() and SessionLocal is not None:
        try:
            with SessionLocal() as db:
                rows = (
                    db.query(AccountArtifactModel.account_id)
                    .distinct()
                    .all()
                )
                for r in rows:
                    accounts.add(r.account_id)
        except Exception as exc:
            logger.warning("Failed to query DB accounts: %s", exc)

    return sorted(accounts)


def _needs_tags(embeddings_data: dict) -> bool:
    """Check if embeddings data lacks meaningful tags."""
    tags = embeddings_data.get("tags")
    if not isinstance(tags, list) or len(tags) == 0:
        return True
    return False


def _get_chunk_texts(embeddings_data: dict) -> str:
    """Extract joined chunk text from embeddings data."""
    chunks = embeddings_data.get("chunks", [])
    texts = []
    for chunk in chunks:
        if isinstance(chunk, dict):
            text = chunk.get("text", "")
            if text:
                texts.append(text)
    return "\n".join(texts)


def _find_embeddings_file(doc_name: str, account_id: str) -> Path | None:
    """Locate the .embeddings.json file for a document by name."""
    docs_dir = account_documents_dir(base_dir=BASE_DIR, account_id=account_id)
    raw_dir = account_output_dir(base_dir=BASE_DIR, account_id=account_id) / "raw_text"

    for base_dir in (docs_dir, raw_dir):
        if not base_dir.exists():
            continue
        emb = base_dir / f"{doc_name}.embeddings.json"
        if emb.exists():
            return emb

    return None


def backfill_tags(
    account_id: str | None = None,
    dry_run: bool = False,
    limit: int | None = None,
) -> dict:
    """Run tag backfill for one or all accounts.

    Returns summary dict with counts.
    """
    accounts = [account_id] if account_id else _discover_accounts()
    summary = {
        "accounts_scanned": 0,
        "docs_scanned": 0,
        "docs_tagged": 0,
        "docs_skipped": 0,
        "errors": 0,
    }

    for acct in accounts:
        logger.info("Backfilling account: %s", acct)
        try:
            docs = list_docs(account_id=acct, include_content=False)
        except Exception as exc:
            logger.warning("Failed to list docs for %s: %s", acct, exc)
            summary["errors"] += 1
            continue

        summary["accounts_scanned"] += 1
        tagged_in_acct = 0

        for doc in docs:
            if limit is not None and tagged_in_acct >= limit:
                break

            summary["docs_scanned"] += 1

            # Load raw embeddings data
            embeddings_data = None
            emb_path = None

            if _is_db_storage_mode():
                from backend.app.services.storage import _doc_id_for_relative_path

                assert SessionLocal is not None
                with SessionLocal() as db:
                    rows = (
                        db.query(AccountArtifactModel)
                        .where(
                            AccountArtifactModel.account_id == acct,
                            AccountArtifactModel.relative_path.like("documents/%")
                            | AccountArtifactModel.relative_path.like(
                                "output/raw_text/%"
                            ),
                            ~AccountArtifactModel.relative_path.like(
                                "%.embeddings.json"
                            ),
                        )
                        .all()
                    )
                    for row in rows:
                        prefix = (
                            "upload"
                            if row.relative_path.startswith("documents/")
                            else "raw"
                        )
                        if (
                            _doc_id_for_relative_path(prefix, row.relative_path)
                            == doc.id
                        ):
                            emb_path_str = f"{row.relative_path}.embeddings.json"
                            embeddings_data = _load_db_embeddings(
                                acct, emb_path_str
                            )
                            break
                # Fallback: check filesystem when DB lookup misses
                if embeddings_data is None:
                    emb_path = _find_embeddings_file(doc.name, acct)
                    if emb_path:
                        embeddings_data = _load_embeddings_file(emb_path)
            else:
                emb_path = _find_embeddings_file(doc.name, acct)
                if emb_path:
                    embeddings_data = _load_embeddings_file(emb_path)

            if embeddings_data is None:
                logger.info(
                    "  SKIP %s — no embeddings file found", doc.name
                )
                summary["docs_skipped"] += 1
                continue

            if not _needs_tags(embeddings_data):
                logger.info(
                    "  SKIP %s — already has %d tags",
                    doc.name,
                    len(embeddings_data.get("tags", [])),
                )
                summary["docs_skipped"] += 1
                continue

            chunk_text = _get_chunk_texts(embeddings_data)
            if not chunk_text.strip():
                logger.info("  SKIP %s — no chunk text", doc.name)
                summary["docs_skipped"] += 1
                continue

            logger.info("  TAGGING %s...", doc.name)
            tags = _extract_doc_tags(chunk_text)
            logger.info("    -> tags: %s", tags)

            if not tags:
                summary["docs_skipped"] += 1
                continue

            if dry_run:
                logger.info("    [dry-run] would save tags for %s", doc.id)
            else:
                try:
                    save_embeddings(
                        doc_id=doc.id,
                        embeddings_data=embeddings_data,
                        account_id=acct,
                        doc_tags=tags,
                    )
                    logger.info("    SAVED tags for %s", doc.id)
                except Exception as exc:
                    logger.warning(
                        "    FAILED to save tags for %s: %s", doc.id, exc
                    )
                    summary["errors"] += 1
                    continue

            summary["docs_tagged"] += 1
            tagged_in_acct += 1

            # Rate limit LLM calls
            time.sleep(_DELAY_SECONDS)

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill doc tags from embeddings")
    parser.add_argument(
        "--account", type=str, default=None, help="Single account ID"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Report only, no writes"
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="Max docs per account"
    )
    args = parser.parse_args()

    summary = backfill_tags(
        account_id=args.account,
        dry_run=args.dry_run,
        limit=args.limit,
    )

    print()
    print("=" * 50)
    print("  TAGS BACKFILL COMPLETE")
    print("=" * 50)
    print(f"  Accounts scanned: {summary['accounts_scanned']}")
    print(f"  Docs scanned:     {summary['docs_scanned']}")
    print(f"  Docs tagged:      {summary['docs_tagged']}")
    print(f"  Docs skipped:     {summary['docs_skipped']}")
    print(f"  Errors:           {summary['errors']}")
    print("=" * 50)


if __name__ == "__main__":
    main()
