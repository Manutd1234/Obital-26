"""Re-encrypt plaintext rows stored before E2EE enforcement (docs/reviews/CODE_REVIEW.md §5.3).

Before the E2EE enforcement fix, many write-sites used
``encrypt_text(user_dek, data) if user_dek is not None else data`` fallbacks that
silently stored plaintext when ``user_dek`` was ``None``.

This script scans the database for such rows and re-encrypts them using the
correct DEK (resolved from active unlock sessions).

Tables & columns scanned:
  - ``chat_messages.text``        — re-encrypt if not ``enc:v1:``-prefixed
  - ``reward_events.note``        — re-encrypt if not ``enc:v1:``-prefixed
  - ``account_registry_records.profile_json``  — re-encrypt embedded
    ``quiz_history`` / ``lesson_progress`` sub-fields
  - ``account_artifacts.content`` — **skipped** (no prefix on ``encrypt_bytes``
    output; plaintext detection requires heuristic or manual audit)

The script only processes accounts that have at least one active unlock session
(because the DEK is encrypted with a server-side Fernet key).  Accounts with
no active session are logged and skipped.

Usage (requires the app's Python environment):
    python scripts/migrate_plaintext_rows.py
"""

from __future__ import annotations

import json
import logging
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path

_log = logging.getLogger("migrate_plaintext")
_log.setLevel(logging.INFO)

_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s"))
_log.addHandler(_handler)

# ── Add project root to sys.path  ──────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

# ── Imports that depend on the project path  ───────────────────────────────
from backend.app.services.db import (
    SessionLocal as EngineSessionLocal,
    is_stateless_db_mode,
    is_sqlite_mode,
)
from backend.app.services.models import (
    ChatMessageModel,
    ChatSessionModel,
    RewardEventModel,
    RewardSessionModel,
    AccountKeyModel,
    AccountRegistryRecordModel,
)
from backend.app.services.crypto_local import encrypt_text
from backend.app.services.unlock_session import resolve_dek_for_account


# ── Constants  ─────────────────────────────────────────────────────────────

_ENC_PREFIX = "enc:v1:"
_ENCRYPTED_SUB_FIELDS = ("quiz_history", "lesson_progress")

_REPORT_TEMPLATE = """\
────────────────────────────────────────────────────────────────────────────────
Migration complete.
────────────────────────────────────────────────────────────────────────────────

  Accounts scanned:          {accounts_scanned}
  Accounts with active DEK:  {accounts_with_dek}
  Messages re-encrypted:     {messages_reencrypted}
  Reward notes re-encrypted: {reward_notes_reencrypted}
  Profile sub-fields fixed:  {profile_sub_fields_fixed}
  Errors encountered:        {errors}

  Limitations:
    - account_artifacts.content was NOT scanned (encrypt_bytes has no prefix).
      Audit manually if needed.
    - Only accounts with an active unlock session could be migrated.
"""


# ── Helpers  ───────────────────────────────────────────────────────────────


def _collect_accounts_with_activity(db) -> set[str]:
    """Return accounts that have data in the encrypted tables."""
    accounts: set[str] = set()

    for row in db.query(ChatSessionModel.account_id).distinct().all():
        accounts.add(row.account_id)
    for row in db.query(RewardSessionModel.account_id).distinct().all():
        accounts.add(row.account_id)
    for row in db.query(AccountRegistryRecordModel.account_id).distinct().all():
        accounts.add(row.account_id)

    return accounts


def _resolve_dek(account_id: str, db) -> bytes | None:
    """Return the DEK for *account_id* if an active unlock session exists."""
    return resolve_dek_for_account(account_id, db)


# ── Per-account migration steps  ───────────────────────────────────────────


def _migrate_chat_messages(
    db, account_id: str, dek: bytes,
) -> int:
    """Re-encrypt ``chat_messages.text`` for *account_id*.

    Returns number of rows updated.
    """
    count = 0
    # Find sessions belonging to this account
    session_ids = (
        db.query(ChatSessionModel.id)
        .where(ChatSessionModel.account_id == account_id)
        .subquery()
    )

    rows = (
        db.query(ChatMessageModel)
        .where(
            ChatMessageModel.session_id.in_(session_ids),
            ~ChatMessageModel.text.startswith(_ENC_PREFIX),
        )
        .all()
    )

    for msg in rows:
        try:
            msg.text = encrypt_text(dek, msg.text)
            count += 1
        except Exception:
            _log.exception("Failed encrypting chat_message %d", msg.id)

    return count


def _migrate_reward_notes(
    db, account_id: str, dek: bytes,
) -> int:
    """Re-encrypt ``reward_events.note`` for *account_id*.

    Returns number of rows updated.
    """
    count = 0
    session_ids = (
        db.query(RewardSessionModel.session_id)
        .where(RewardSessionModel.account_id == account_id)
        .subquery()
    )

    rows = (
        db.query(RewardEventModel)
        .where(
            RewardEventModel.session_id.in_(session_ids),
            ~RewardEventModel.note.startswith(_ENC_PREFIX),
        )
        .all()
    )

    for ev in rows:
        try:
            ev.note = encrypt_text(dek, ev.note)
            count += 1
        except Exception:
            _log.exception("Failed encrypting reward_event %d", ev.id)

    return count


def _migrate_profile_json(
    db, account_id: str, dek: bytes,
) -> int:
    """Re-encrypt embedded sub-fields in ``profile_json``.

    Returns number of sub-fields fixed.
    """
    fixed = 0
    rows = db.query(AccountRegistryRecordModel).where(
        AccountRegistryRecordModel.account_id == account_id,
    ).all()

    for row in rows:
        try:
            profile = json.loads(row.profile_json)
            if not isinstance(profile, dict):
                continue

            changed = False
            for key in _ENCRYPTED_SUB_FIELDS:
                val = profile.get(key)
                if val is None:
                    continue
                if isinstance(val, str) and val.startswith(_ENC_PREFIX):
                    continue  # already encrypted
                try:
                    profile[key] = encrypt_text(dek, json.dumps(val))
                    changed = True
                    fixed += 1
                except Exception:
                    _log.exception(
                        "Failed encrypting %s for account %s",
                        key, account_id,
                    )

            if changed:
                row.profile_json = json.dumps(profile, ensure_ascii=True)
        except json.JSONDecodeError:
            _log.warning("Invalid JSON in profile_json for %s", account_id)
        except Exception:
            _log.exception("Failed processing profile_json for %s", account_id)

    return fixed


def _migrate_account(
    db, account_id: str, dek: bytes | None,
) -> dict:
    """Migrate one account. Return summary counters."""
    result = {
        "messages": 0,
        "reward_notes": 0,
        "profile_fixes": 0,
    }

    if dek is None:
        _log.warning("  └─ No active unlock session — skipping")
        return result

    _log.info("  ├─ Chat messages ...")
    result["messages"] = _migrate_chat_messages(db, account_id, dek)

    _log.info("  ├─ Reward notes ...")
    result["reward_notes"] = _migrate_reward_notes(db, account_id, dek)

    _log.info("  └─ Profile sub-fields ...")
    result["profile_fixes"] = _migrate_profile_json(db, account_id, dek)

    return result


# ── Main  ──────────────────────────────────────────────────────────────────


def main() -> None:
    _log.info("=" * 60)
    _log.info("  E2EE plaintext row migration")
    _log.info("=" * 60)

    if not EngineSessionLocal:
        _log.error("No database configured. Set DATABASE_URL or LOCAL_DATABASE_URL.")
        sys.exit(1)

    if is_sqlite_mode():
        _log.warning("SQLite mode: data is intentionally plaintext. Nothing to migrate.")
        print(
            "\nSQLite stores data as plaintext by design (see SECURITY.md).\n"
            "To encrypt existing data, connect to a Postgres instance and\n"
            "  1. sync the data up (cloud sync), then\n"
            "  2. run this script against the cloud database.\n"
        )
        sys.exit(0)

    _log.info("Non-SQLite (cloud) mode detected — scanning for plaintext rows.\n")

    total = {
        "accounts_scanned": 0,
        "accounts_with_dek": 0,
        "messages": 0,
        "reward_notes": 0,
        "profile_fixes": 0,
        "errors": 0,
    }

    assert EngineSessionLocal is not None
    with EngineSessionLocal() as db:
        accounts = _collect_accounts_with_activity(db)
        total["accounts_scanned"] = len(accounts)
        _log.info("Found %d accounts with data in encrypted tables.\n", len(accounts))

        for i, account_id in enumerate(sorted(accounts), 1):
            _log.info("[%d/%d]  Account: %s", i, len(accounts), account_id)
            dek = _resolve_dek(account_id, db)
            if dek is not None:
                total["accounts_with_dek"] += 1
            try:
                result = _migrate_account(db, account_id, dek)
                total["messages"] += result["messages"]
                total["reward_notes"] += result["reward_notes"]
                total["profile_fixes"] += result["profile_fixes"]
            except Exception:
                total["errors"] += 1
                _log.exception("Migration failed for account %s", account_id)

            db.commit()

    # ── Report ──────────────────────────────────────────────────────────
    print(
        _REPORT_TEMPLATE.format(
            accounts_scanned=total["accounts_scanned"],
            accounts_with_dek=total["accounts_with_dek"],
            messages_reencrypted=total["messages"],
            reward_notes_reencrypted=total["reward_notes"],
            profile_sub_fields_fixed=total["profile_fixes"],
            errors=total["errors"],
        )
    )


if __name__ == "__main__":
    main()
