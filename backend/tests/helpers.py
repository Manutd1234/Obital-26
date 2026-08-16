from __future__ import annotations

import base64
import os

from backend.app.services.crypto_local import Argon2Params, derive_kek, generate_dek, serialize_params, wrap_dek
from backend.app.services.models import AccountKeyModel


def make_account_row(account_id: str = "admin", passphrase: str = "correct-passphrase") -> AccountKeyModel:
    salt = os.urandom(16)
    params = Argon2Params()
    dek = generate_dek()
    kek = derive_kek(passphrase, salt, params)
    return AccountKeyModel(
        account_id=account_id,
        salt_b64=base64.b64encode(salt).decode("ascii"),
        argon2_params_json=serialize_params(params),
        wrapped_dek_b64=wrap_dek(kek, dek),
        unlock_version=1,
        failed_unlock_attempts=0,
        consecutive_lockouts=0,
        locked_until=None,
        last_failed_unlock_at=None,
    )
