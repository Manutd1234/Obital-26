from __future__ import annotations

import argparse
import os

from backend.app.services.account_sync import push_account_to_cloud
from backend.app.services.db import SessionLocal, db_enabled, init_db
from backend.app.services.unlock_guard import resolve_dek_from_passphrase


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Push one account's local data into the cloud database.")
    parser.add_argument("--account-id", required=True, help="Account id to sync.")
    parser.add_argument(
        "--passphrase",
        default=os.getenv("ACCOUNT_SYNC_PASSPHRASE", ""),
        help="Account sync passphrase (admin mode).",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    account_id = str(args.account_id).strip()
    passphrase = str(args.passphrase or "").strip()
    if not db_enabled():
        raise RuntimeError("DATABASE_URL is not set. Configure Postgres and rerun.")
    if not passphrase:
        raise RuntimeError("Account sync passphrase is required. Set ACCOUNT_SYNC_PASSPHRASE or pass --passphrase.")

    init_db()
    assert SessionLocal is not None
    from backend.app.services.models import AccountKeyModel
    with SessionLocal() as db:
        row = db.query(AccountKeyModel).where(AccountKeyModel.account_id == account_id).first()
        if row is None:
            from backend.app.services.crypto_local import Argon2Params, derive_kek, generate_dek, serialize_params, wrap_dek
            import base64
            
            params = Argon2Params()
            salt = base64.b64encode(os.urandom(16)).decode("ascii")
            kek = derive_kek(passphrase, base64.b64decode(salt.encode("ascii")), params)
            dek = generate_dek()
            row = AccountKeyModel(
                account_id=account_id,
                salt_b64=salt,
                argon2_params_json=serialize_params(params),
                wrapped_dek_b64=wrap_dek(kek, dek),
            )
            db.add(row)
            db.commit()
            print(f"Bootstrapped fresh cloud key material envelope for account '{account_id}'.")
        else:
            dek = resolve_dek_from_passphrase(db, account_id, passphrase)

    report = push_account_to_cloud(account_id, dek, delete_stale=True)

    print(f"Synced chat sessions: {report.chat_sessions}")
    print(f"Synced reward sessions: {report.reward_sessions}")
    print(f"Uploaded account files: {report.uploaded_files}")
    print(f"Deleted remote account files: {report.deleted_remote_files}")
    print(f"Registry synced: {report.registry_synced}")


if __name__ == "__main__":
    main()
