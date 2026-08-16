from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from engine.shared.account_paths import account_root

from backend.app.schemas import ChatSettings, SettingsPatchRequest, SettingsResponse

BASE_DIR = Path(__file__).resolve().parents[3]
GLOBAL_SYSTEM_PROMPT_PATH = BASE_DIR / "backend" / "prompts" / "chat_system_prompt.md"
MAX_PROMPT_CHARS = 12000
DEFAULT_SYSTEM_PROMPT = (
    "You are a patient and rigorous tutor. Use the provided lesson and document context when available. "
    "If the student asks about other topics, answer them directly and concisely, relating it back to the lesson if helpful."
)
logger = logging.getLogger(__name__)


# ── Helpers ──


def _account_config_dir(account_id: str) -> Path:
    return account_root(base_dir=BASE_DIR, account_id=account_id) / "config"


def _account_settings_path(account_id: str) -> Path:
    return _account_config_dir(account_id) / "settings.json"


def _account_system_prompt_path(account_id: str) -> Path:
    return _account_config_dir(account_id) / "chat_system_prompt.md"


def _load_json(path: Path) -> dict[str, object]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_json(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _read_prompt(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def _coerce_prompt(text: str) -> str:
    value = text.strip()
    if not value:
        raise ValueError("system_prompt must not be empty")
    if len(value) > MAX_PROMPT_CHARS:
        raise ValueError(f"system_prompt exceeds max length ({MAX_PROMPT_CHARS} chars)")
    return value


def _is_db_storage_mode() -> bool:
    try:
        from backend.app.services.db import is_db_mode
        return is_db_mode()
    except Exception:
        return False


def _resolve_dek(user_dek: bytes | None) -> bytes | None:
    """Resolve the DEK from the parameter, falling back to the request context."""
    if user_dek is not None:
        return user_dek
    try:
        from backend.app.api.common import active_user_dek
        return active_user_dek.get()
    except Exception:
        return None


def _upsert_artifact(
    db, *, account_id: str, relative_path: str, content_bytes: bytes, dek: bytes | None
) -> None:
    """Create or update an AccountArtifactModel row with optional E2EE."""
    from backend.app.services.db import is_sqlite_mode
    from backend.app.services.models import AccountArtifactModel
    from backend.app.services.crypto_local import encrypt_bytes

    if is_sqlite_mode():
        ciphertext = content_bytes
    elif dek is not None:
        ciphertext = encrypt_bytes(dek, content_bytes)
    else:
        raise ValueError("user_dek is required for E2EE storage")

    sha256_hash = hashlib.sha256(content_bytes).hexdigest()

    row = db.query(AccountArtifactModel).where(
        AccountArtifactModel.account_id == account_id,
        AccountArtifactModel.relative_path == relative_path,
    ).first()

    if row is None:
        db.add(AccountArtifactModel(
            account_id=account_id,
            relative_path=relative_path,
            content=ciphertext,
            sha256=sha256_hash,
            size_bytes=len(content_bytes),
            updated_at=datetime.now(UTC),
        ))
    else:
        row.content = ciphertext
        row.sha256 = sha256_hash
        row.size_bytes = len(content_bytes)
        row.updated_at = datetime.now(UTC)


def _artifact_plaintext_bytes(
    *,
    content_bytes: bytes,
    stored_sha256: str | None,
    dek: bytes | None,
    sqlite_mode: bool,
    account_id: str,
    relative_path: str,
) -> bytes:
    """Return plaintext artifact bytes, accepting verified legacy plaintext rows."""
    if sqlite_mode or dek is None:
        return content_bytes

    from backend.app.services.crypto_local import decrypt_bytes

    try:
        return decrypt_bytes(dek, content_bytes)
    except Exception:
        digest = hashlib.sha256(content_bytes).hexdigest()
        if stored_sha256 and digest == stored_sha256:
            logger.warning(
                "Using verified legacy plaintext artifact %s for account %s; it will be re-encrypted on next save",
                relative_path,
                account_id,
            )
            return content_bytes
        raise


def _read_artifact(db, *, account_id: str, relative_path: str, dek: bytes | None) -> dict | None:
    """Read and decrypt an AccountArtifactModel row, returning parsed JSON or None."""
    from backend.app.services.db import is_sqlite_mode
    from backend.app.services.models import AccountArtifactModel

    row = db.query(AccountArtifactModel).where(
        AccountArtifactModel.account_id == account_id,
        AccountArtifactModel.relative_path == relative_path,
    ).first()
    if row is None:
        return None
    content_bytes = bytes(row.content)
    try:
        content_bytes = _artifact_plaintext_bytes(
            content_bytes=content_bytes,
            stored_sha256=str(row.sha256 or ""),
            dek=dek,
            sqlite_mode=is_sqlite_mode(),
            account_id=account_id,
            relative_path=relative_path,
        )
    except Exception:
        return None
    try:
        return json.loads(content_bytes.decode("utf-8"))
    except Exception:
        return None


# ── Public API ──


def get_effective_system_prompt(account_id: str, user_dek: bytes | None = None) -> str:
    """Return the system prompt for this account, checking DB, account dir, then global default."""
    if _is_db_storage_mode():
        from backend.app.services.db import SessionLocal, is_sqlite_mode
        from backend.app.services.models import AccountArtifactModel

        dek = _resolve_dek(user_dek)
        assert SessionLocal is not None

        with SessionLocal() as db:
            row = db.query(AccountArtifactModel).where(
                AccountArtifactModel.account_id == account_id,
                AccountArtifactModel.relative_path == "config/chat_system_prompt.md",
            ).first()
            if row is not None:
                content_bytes = bytes(row.content)
                try:
                    content_bytes = _artifact_plaintext_bytes(
                        content_bytes=content_bytes,
                        stored_sha256=str(row.sha256 or ""),
                        dek=dek,
                        sqlite_mode=is_sqlite_mode(),
                        account_id=account_id,
                        relative_path="config/chat_system_prompt.md",
                    )
                except Exception:
                    logger.warning(
                        "Failed to decrypt system prompt for account %s, falling back to default",
                        account_id,
                    )
                    return DEFAULT_SYSTEM_PROMPT
                try:
                    text = content_bytes.decode("utf-8").strip()
                except Exception:
                    logger.warning(
                        "Failed to decode system prompt for account %s, falling back to default",
                        account_id,
                    )
                    return DEFAULT_SYSTEM_PROMPT
                return text or DEFAULT_SYSTEM_PROMPT
            return DEFAULT_SYSTEM_PROMPT

    account_prompt = _read_prompt(_account_system_prompt_path(account_id))
    if account_prompt:
        return account_prompt
    global_prompt = _read_prompt(GLOBAL_SYSTEM_PROMPT_PATH)
    if global_prompt:
        return global_prompt
    return DEFAULT_SYSTEM_PROMPT


def get_settings(account_id: str, user_dek: bytes | None = None) -> SettingsResponse:
    """Return the current settings (wraps get_effective_system_prompt)."""
    return SettingsResponse(chat=ChatSettings(
        system_prompt=get_effective_system_prompt(account_id, user_dek=user_dek)
    ))


def update_settings(account_id: str, patch: SettingsPatchRequest, user_dek: bytes | None = None) -> SettingsResponse:
    """Update account settings. Currently only supports patching system_prompt."""
    if patch.chat.system_prompt is None:
        return get_settings(account_id, user_dek=user_dek)

    system_prompt = _coerce_prompt(patch.chat.system_prompt)

    if _is_db_storage_mode():
        from backend.app.services.db import SessionLocal

        dek = _resolve_dek(user_dek)
        assert SessionLocal is not None

        with SessionLocal() as db:
            # Save the prompt
            prompt_bytes = (system_prompt + "\n").encode("utf-8")
            _upsert_artifact(
                db,
                account_id=account_id,
                relative_path="config/chat_system_prompt.md",
                content_bytes=prompt_bytes,
                dek=dek,
            )

            # Update settings.json to point to the prompt file
            settings_doc = _read_artifact(
                db,
                account_id=account_id,
                relative_path="config/settings.json",
                dek=dek,
            ) or {}
            chat_doc = settings_doc.get("chat") if isinstance(settings_doc, dict) else {}
            if not isinstance(chat_doc, dict):
                chat_doc = {}
            chat_doc["system_prompt_path"] = "config/chat_system_prompt.md"
            settings_doc["chat"] = chat_doc
            settings_bytes = json.dumps(settings_doc, indent=2).encode("utf-8")
            _upsert_artifact(
                db,
                account_id=account_id,
                relative_path="config/settings.json",
                content_bytes=settings_bytes,
                dek=dek,
            )

            db.commit()
    else:
        prompt_path = _account_system_prompt_path(account_id)
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(system_prompt + "\n", encoding="utf-8")

        settings_path = _account_settings_path(account_id)
        settings_doc = _load_json(settings_path)
        chat_doc = settings_doc.get("chat")
        if not isinstance(chat_doc, dict):
            chat_doc = {}
        chat_doc["system_prompt_path"] = str(prompt_path.relative_to(BASE_DIR))
        settings_doc["chat"] = chat_doc
        _save_json(settings_path, settings_doc)

    return get_settings(account_id, user_dek=user_dek)
