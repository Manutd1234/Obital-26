from __future__ import annotations

import re
from pathlib import Path


_ACCOUNT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")


def normalize_account_id(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        raise RuntimeError("Account id cannot be empty.")
    if text != text.lower():
        raise RuntimeError("Account id must be lowercase.")
    if not _ACCOUNT_ID_PATTERN.fullmatch(text):
        raise RuntimeError(
            "Invalid account id. Use 2-64 chars: lowercase letters, digits, underscore, hyphen; must start with letter or digit."
        )
    return text


def account_root(*, base_dir: Path, account_id: str) -> Path:
    safe_id = normalize_account_id(account_id)
    return base_dir.resolve() / "engine" / "accounts" / safe_id


def account_documents_dir(*, base_dir: Path, account_id: str) -> Path:
    return account_root(base_dir=base_dir, account_id=account_id) / "documents"


def account_output_dir(*, base_dir: Path, account_id: str) -> Path:
    return account_root(base_dir=base_dir, account_id=account_id) / "output"


def account_cache_dir(*, base_dir: Path, account_id: str) -> Path:
    return account_root(base_dir=base_dir, account_id=account_id) / "cache"


def account_graph_dir(*, base_dir: Path, account_id: str) -> Path:
    return account_root(base_dir=base_dir, account_id=account_id) / "graph_db"


def account_qa_output_path(*, base_dir: Path, account_id: str) -> Path:
    return account_output_dir(base_dir=base_dir, account_id=account_id) / "output_curriculum_qa.json"
