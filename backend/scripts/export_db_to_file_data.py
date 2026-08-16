from __future__ import annotations

import argparse
import os

from backend.app.services.account_sync import pull_account_from_cloud
from backend.app.services.db import SessionLocal, db_enabled, init_db
from backend.app.services.unlock_guard import resolve_dek_from_passphrase


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export one account's cloud data back into local files.")
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
        raise RuntimeError("DATABASE_URL is not set. Configure cloud DB and rerun.")
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

    report = pull_account_from_cloud(account_id, dek)

    print(f"Synced chat sessions: {report.chat_sessions}")
    print(f"Synced reward sessions: {report.reward_sessions}")
    print(f"Downloaded account files: {report.downloaded_files}")
    print(f"Deleted local account files: {report.deleted_local_files}")
    print(f"Registry synced: {report.registry_synced}")


if __name__ == "__main__":
    main()
