"""Reset unrecoverable encrypted account state in one cloud account.

This is an intentional, destructive migration for deployments where old
encrypted account fields cannot be recovered. It preserves documents, chat
data, cached points, and leaderboard counters.

The migration does not need a DEK because it does not attempt to decrypt the
discarded fields. It always writes a local JSON backup before applying changes.

Usage:
    python scripts/sync/reset_cloud_learning_state.py --account-id admin --dry-run
    python scripts/sync/reset_cloud_learning_state.py --account-id admin --apply

The script refuses to run against SQLite. Set the production DATABASE_URL (and
unset LOCAL_DATABASE_URL, or use OBITAL_FORCE_CLOUD_DB=1) when running it.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))


def _load_dotenv() -> None:
    """Load the same simple project .env format used by run_backend.ps1."""
    env_path = _ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if name:
            os.environ.setdefault(name, value)


# This script is explicitly for the cloud registry. Force the same selection
# that the backend uses when it is launched in cloud mode, even if a local
# SQLite URL is also present in the environment.
_load_dotenv()
os.environ["OBITAL_FORCE_CLOUD_DB"] = "1"

from backend.app.services.db import (  # noqa: E402
    CloudSessionLocal,
    SessionLocal,
    is_sqlite_mode,
    is_stateless_db_mode,
)
from backend.app.services.models import AccountRegistryRecordModel  # noqa: E402


log = logging.getLogger("reset_cloud_learning_state")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

_RESET_VALUES = {
    "user_profile": "",
    "agent_profile": "",
    "friends": [],
    "quiz_history": [],
    "lesson_progress": {"active_lesson_id": None, "lessons": {}},
    "active_quiz": None,
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Report affected accounts without writing")
    mode.add_argument("--apply", action="store_true", help="Write the reset after creating a backup")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument(
        "--account-id",
        help="The single cloud account whose unrecoverable encrypted state should be reset",
    )
    target.add_argument(
        "--all",
        action="store_true",
        help="Reset every cloud account (always run --dry-run first)",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=None,
        help="Directory for the JSON backup (default: backups/cloud-learning-state-<UTC timestamp>)",
    )
    return parser.parse_args()


def _backup_path(backup_dir: Path | None) -> Path:
    if backup_dir is not None:
        return backup_dir / "account_registry_records.json"
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return _ROOT / "backups" / f"cloud-learning-state-{stamp}" / "account_registry_records.json"


def main() -> int:
    args = _parse_args()
    account_id = args.account_id.strip().lower() if args.account_id else None
    if not is_stateless_db_mode() or is_sqlite_mode():
        raise SystemExit(
            "Refusing to run: DATABASE_URL is not configured as cloud Postgres. "
            "Set DATABASE_URL in .env or the process environment."
        )

    session_factory = SessionLocal if not is_sqlite_mode() else CloudSessionLocal
    if session_factory is None:
        raise SystemExit("No cloud database session is configured.")

    backup_file = _backup_path(args.backup_dir)
    with session_factory() as db:
        query = db.query(AccountRegistryRecordModel)
        if account_id is not None:
            query = query.where(AccountRegistryRecordModel.account_id == account_id)
        rows = query.order_by(AccountRegistryRecordModel.account_id).all()
        backup: list[dict[str, str]] = []
        affected: list[str] = []
        for row in rows:
            try:
                profile = json.loads(row.profile_json)
            except (TypeError, json.JSONDecodeError) as exc:
                log.error("Skipping %s: invalid profile_json (%s)", row.account_id, exc)
                continue
            if not isinstance(profile, dict):
                log.error("Skipping %s: profile_json is not an object", row.account_id)
                continue

            backup.append({"account_id": str(row.account_id), "profile_json": row.profile_json})
            if any(profile.get(field) != value for field, value in _RESET_VALUES.items()):
                affected.append(str(row.account_id))
                if args.apply:
                    profile.update(json.loads(json.dumps(_RESET_VALUES)))
                    row.profile_json = json.dumps(profile, ensure_ascii=True, indent=2)
                    row.updated_at = datetime.now(UTC)

        log.info("Cloud accounts scanned: %d (target=%s)", len(rows), account_id or "all")
        log.info("Accounts requiring reset: %d", len(affected))
        if args.dry_run:
            log.info("Dry run: no database changes made")
            return 0

        backup_file.parent.mkdir(parents=True, exist_ok=True)
        backup_file.write_text(json.dumps(backup, ensure_ascii=True, indent=2), encoding="utf-8")
        db.commit()
        log.info("Reset applied to %d accounts", len(affected))
        log.info("Backup written to %s", backup_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
