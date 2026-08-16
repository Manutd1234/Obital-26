from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.services.crypto_local import decrypt_bytes, encrypt_bytes
from backend.app.services.db import SessionLocal, db_enabled, init_db, is_sqlite_mode
from backend.app.services.embeddings import embed_texts
from backend.app.services.models import AccountArtifactModel, AccountKeyModel
from backend.app.services.storage import _chunk_text, _doc_id_for_relative_path, _extract_text_from_bytes
from backend.app.services.unlock_guard import resolve_dek_from_passphrase


def _load_dotenv() -> None:
    env_path = Path(".env")
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip())


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill missing document embedding artifacts so metadata-only /docs can show chunk counts."
    )
    parser.add_argument("--account-id", required=True, help="Account id to repair.")
    parser.add_argument(
        "--passphrase",
        default=os.getenv("ACCOUNT_SYNC_PASSPHRASE", ""),
        help="Account sync passphrase. Defaults to ACCOUNT_SYNC_PASSPHRASE.",
    )
    parser.add_argument(
        "--counts-only",
        action="store_true",
        help="Write chunk text/count metadata without generating vectors. Faster, but vector search will still skip these chunks.",
    )
    parser.add_argument("--force", action="store_true", help="Rewrite existing embedding artifacts.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum docs to repair; 0 means no limit.")
    return parser.parse_args()


def _is_doc_path(relative_path: str) -> bool:
    if relative_path.endswith(".embeddings.json"):
        return False
    return relative_path.startswith("documents/") or relative_path.startswith("output/raw_text/")


def main() -> None:
    _load_dotenv()
    args = _parse_args()
    account_id = str(args.account_id).strip()
    passphrase = str(args.passphrase or "").strip()
    if not db_enabled():
        raise RuntimeError("DATABASE_URL is not set. Configure cloud DB and rerun.")
    if not is_sqlite_mode() and not passphrase:
        raise RuntimeError("Passphrase required for encrypted cloud docs.")

    init_db()
    assert SessionLocal is not None
    with SessionLocal() as db:
        dek = None
        if not is_sqlite_mode():
            key_row = db.query(AccountKeyModel).where(AccountKeyModel.account_id == account_id).first()
            if key_row is None:
                raise RuntimeError(f"No account key row found for {account_id!r}.")
            dek = resolve_dek_from_passphrase(db, account_id, passphrase)

        rows = (
            db.query(AccountArtifactModel)
            .where(AccountArtifactModel.account_id == account_id)
            .order_by(AccountArtifactModel.relative_path.asc())
            .all()
        )
        by_path = {str(row.relative_path): row for row in rows}
        doc_rows = [row for row in rows if _is_doc_path(str(row.relative_path)) and row.deleted_at is None]

        repaired = 0
        skipped_existing = 0
        skipped_unavailable = 0
        failed = 0
        for row in doc_rows:
            relative_path = str(row.relative_path)
            embeddings_path = f"{relative_path}.embeddings.json"
            existing = by_path.get(embeddings_path)
            if existing is not None and not args.force:
                skipped_existing += 1
                continue
            if args.limit and repaired >= args.limit:
                break
            if getattr(row, "hydration_status", "full") != "full" or not row.content:
                skipped_unavailable += 1
                continue

            try:
                if is_sqlite_mode():
                    plain_bytes = bytes(row.content)
                    stored_dek = None
                else:
                    plain_bytes = decrypt_bytes(dek, row.content)
                    stored_dek = dek
                if hashlib.sha256(plain_bytes).hexdigest() != str(row.sha256):
                    raise RuntimeError("sha256 mismatch")

                name = Path(relative_path).name
                prefix = "upload" if relative_path.startswith("documents/") else "raw"
                doc_id = _doc_id_for_relative_path(prefix, relative_path)
                text = _extract_text_from_bytes(name, plain_bytes)
                chunks = _chunk_text(doc_id, text)
                if args.counts_only:
                    vectors = [[] for _ in chunks]
                else:
                    vectors = embed_texts([chunk.text for chunk in chunks])
                payload = {
                    "chunks": [
                        {"chunk_id": chunk.chunk_id, "text": chunk.text, "vector": vector}
                        for chunk, vector in zip(chunks, vectors)
                    ]
                }
                payload_bytes = json.dumps(payload).encode("utf-8")
                stored_content = payload_bytes if is_sqlite_mode() else encrypt_bytes(stored_dek, payload_bytes)
                digest = hashlib.sha256(payload_bytes).hexdigest()
                if existing is None:
                    db.add(
                        AccountArtifactModel(
                            account_id=account_id,
                            relative_path=embeddings_path,
                            content=stored_content,
                            sha256=digest,
                            size_bytes=len(stored_content),
                        )
                    )
                else:
                    existing.content = stored_content
                    existing.sha256 = digest
                    existing.size_bytes = len(stored_content)
                db.commit()
                repaired += 1
                print(f"repaired {relative_path}: {len(chunks)} chunks")
            except Exception as exc:
                db.rollback()
                failed += 1
                print(f"failed {relative_path}: {exc}")

    print(
        "done: "
        f"repaired={repaired}, skipped_existing={skipped_existing}, "
        f"skipped_unavailable={skipped_unavailable}, failed={failed}"
    )


if __name__ == "__main__":
    main()
