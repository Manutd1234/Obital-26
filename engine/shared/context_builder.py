from __future__ import annotations

import hashlib
import json


class HybridContextBuilder:
    """
    Shared cache-friendly context builder:
    - stable system prefix (deterministic bytes)
    - variable user payload (canonicalized deterministic JSON)
    """

    def __init__(self, *, policy_payload: dict[str, object], system_preamble: str) -> None:
        self._stable_system = (
            f"{system_preamble.strip()}\n"
            f"{json.dumps(policy_payload, ensure_ascii=True, sort_keys=True, separators=(',', ':'))}"
        )
        self.prefix_fingerprint = hashlib.sha256(self._stable_system.encode("utf-8")).hexdigest()

    @staticmethod
    def canonical_json(payload: dict[str, object]) -> str:
        return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))

    def build_messages(self, *, user_payload: dict[str, object]) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": self._stable_system},
            {"role": "user", "content": self.canonical_json(user_payload)},
        ]
