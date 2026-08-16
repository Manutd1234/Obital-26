from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import dataclass

from argon2.low_level import Type, hash_secret_raw
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)

#link to auth.py

@dataclass(frozen=True)
class Argon2Params:
    time_cost: int = 3
    memory_cost: int = 65536
    parallelism: int = 2
    hash_len: int = 32


def derive_kek(passphrase: str, salt: bytes, params: Argon2Params | None = None) -> bytes:
    p = params or Argon2Params()
    return hash_secret_raw(
        secret=passphrase.encode("utf-8"),
        salt=salt,
        time_cost=p.time_cost,
        memory_cost=p.memory_cost,
        parallelism=p.parallelism,
        hash_len=p.hash_len,
        type=Type.ID,
    )


def generate_dek() -> bytes:
    return os.urandom(32)


def _aead_encrypt(key: bytes, plaintext: bytes) -> bytes:
    aes = AESGCM(key)
    nonce = os.urandom(12)
    ct = aes.encrypt(nonce, plaintext, None)
    return nonce + ct


def _aead_decrypt(key: bytes, payload: bytes) -> bytes:
    aes = AESGCM(key)
    nonce = payload[:12]
    ct = payload[12:]
    return aes.decrypt(nonce, ct, None)


def wrap_dek(kek: bytes, dek: bytes) -> str:
    return base64.b64encode(_aead_encrypt(kek, dek)).decode("ascii")


def unwrap_dek(kek: bytes, wrapped_dek_b64: str) -> bytes:
    return _aead_decrypt(kek, base64.b64decode(wrapped_dek_b64.encode("ascii")))


def require_dek(dek: bytes | None, context: str) -> bytes:
    """Raise ValueError if DEK is absent in non-SQLite (E2EE) mode.

    Call at every write site that stores user data outside SQLite mode.
    This enforces the invariant that encryption-at-rest is never silently skipped.
    """
    if dek is None:
        raise ValueError(f"user_dek is required for E2EE {context}")
    return dek


def encrypt_text(dek: bytes, text: str) -> str:
    payload = _aead_encrypt(dek, text.encode("utf-8"))
    return "enc:v1:" + base64.b64encode(payload).decode("ascii")


def encrypt_bytes(dek: bytes, payload: bytes) -> bytes:
    return _aead_encrypt(dek, payload)


def decrypt_text(dek: bytes | None, value: str) -> str:
    if dek is None:
        return value
    if not value.startswith("enc:v1:"):
        logger.warning("decrypt_text: plaintext passthrough — value lacks enc:v1: prefix")
        return value
    try:
        raw = base64.b64decode(value[len("enc:v1:") :].encode("ascii"))
        return _aead_decrypt(dek, raw).decode("utf-8")
    except Exception as exc:
        logger.warning("decrypt_text failed (key mismatch or corrupt data): %s", exc)
        return ""


def decrypt_text_strict(dek: bytes | None, value: str, *, field_name: str = "encrypted text") -> str:
    if dek is None:
        raise ValueError(f"{field_name} requires a DEK")
    if not value.startswith("enc:v1:"):
        raise ValueError(f"{field_name} is missing enc:v1 prefix")
    try:
        raw = base64.b64decode(value[len("enc:v1:") :].encode("ascii"))
        return _aead_decrypt(dek, raw).decode("utf-8")
    except Exception as exc:
        raise ValueError(f"{field_name} decrypt failed") from exc


def decrypt_bytes(dek: bytes, payload: bytes) -> bytes:
    return _aead_decrypt(dek, payload)


def serialize_params(params: Argon2Params) -> str:
    return json.dumps(
        {
            "time_cost": params.time_cost,
            "memory_cost": params.memory_cost,
            "parallelism": params.parallelism,
            "hash_len": params.hash_len,
        },
        separators=(",", ":"),
    )


def parse_params(raw: str) -> Argon2Params:
    doc = json.loads(raw)
    return Argon2Params(
        time_cost=int(doc["time_cost"]),
        memory_cost=int(doc["memory_cost"]),
        parallelism=int(doc["parallelism"]),
        hash_len=int(doc["hash_len"]),
    )
