from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    deepseek_api_key: str
    deepseek_model: str
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_thinking_enabled: bool = False
    deepseek_reasoning_effort: str = "high"
    base_dir: Path = Path(".")
    graph_storage_dir: Path = Path("data/graph_db")
    vector_backend_url: str = ""
    vector_backend_api_key: str = ""
    vector_backend_timeout_sec: float = 20.0


def load_settings(env_file: str | Path = ".env") -> Settings:
    """Load runtime settings for Step 1 setup."""
    load_dotenv(dotenv_path=env_file)

    deepseek_api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not deepseek_api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not set. Add it to your .env file.")

    deepseek_model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat").strip()
    deepseek_base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()
    deepseek_thinking_enabled = os.environ.get("DEEPSEEK_THINKING_ENABLED", "false").strip().lower() in {"1", "true", "yes"}
    deepseek_reasoning_effort = os.environ.get("DEEPSEEK_REASONING_EFFORT", "high").strip()
    if not deepseek_model:
        deepseek_model = "deepseek-chat"

    base_dir = Path(os.environ.get("PIPELINE_BASE_DIR", ".")).resolve()
    graph_storage_dir = Path(
        os.environ.get("GRAPH_STORAGE_DIR", base_dir / "data" / "graph_db")
    ).resolve()
    vector_backend_url = os.environ.get("VECTOR_BACKEND_URL", "").strip()
    vector_backend_api_key = os.environ.get("VECTOR_BACKEND_API_KEY", "").strip()
    timeout_raw = os.environ.get("VECTOR_BACKEND_TIMEOUT_SEC", "20").strip()
    try:
        vector_backend_timeout_sec = max(1.0, float(timeout_raw))
    except ValueError:
        vector_backend_timeout_sec = 20.0

    return Settings(
        deepseek_api_key=deepseek_api_key,
        deepseek_model=deepseek_model,
        deepseek_base_url=deepseek_base_url,
        deepseek_thinking_enabled=deepseek_thinking_enabled,
        deepseek_reasoning_effort=deepseek_reasoning_effort,
        base_dir=base_dir,
        graph_storage_dir=graph_storage_dir,
        vector_backend_url=vector_backend_url,
        vector_backend_api_key=vector_backend_api_key,
        vector_backend_timeout_sec=vector_backend_timeout_sec,
    )
