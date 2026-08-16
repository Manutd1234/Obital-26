from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import requests
from dotenv import load_dotenv


DEFAULT_WEB_RESULTS = 5
MAX_WEB_RESULTS = 5
MAX_WIKI_RESULTS = 3
WIKI_API_URL = "https://en.wikipedia.org/w/api.php"


@dataclass(frozen=True)
class SearchBudget:
    remaining: int = 3

    def consume(self, requested: int) -> tuple["SearchBudget", int]:
        allowed = max(0, min(self.remaining, requested))
        return SearchBudget(remaining=self.remaining - allowed), allowed


def search_limit_payload(*, requested: int, remaining: int) -> dict[str, Any]:
    return {
        "error": "search_limit_exceeded",
        "requested_requests": requested,
        "remaining_requests": remaining,
        "message": "External search is limited to 3 provider requests per message.",
        "results": [],
    }


def provider_request_count(tool_name: str) -> int:
    if tool_name == "search_web":
        return 2
    if tool_name == "search_wikipedia":
        return 1
    return 0


@lru_cache(maxsize=1)
def _load_exa_client():
    load_dotenv()
    api_key = os.getenv("EXA_API_KEY", "").strip()
    if not api_key:
        return None
    from exa_py import Exa

    return Exa(api_key)


def _clean_snippet(value: object) -> str:
    text = str(value or "")
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _read_attr(item: object, name: str, default: object = "") -> object:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _normalize_exa_result(item: object, rank: int) -> dict[str, Any]:
    highlights = _read_attr(item, "highlights", []) or []
    if isinstance(highlights, str):
        highlights = [highlights]
    snippet = " ".join(_clean_snippet(part) for part in list(highlights)[:2]).strip()
    if not snippet:
        snippet = _clean_snippet(_read_attr(item, "text", "") or _read_attr(item, "summary", ""))
    return {
        "source": "web",
        "rank": rank,
        "title": str(_read_attr(item, "title", "") or ""),
        "url": str(_read_attr(item, "url", "") or ""),
        "snippet": snippet[:1200],
        "highlights": [_clean_snippet(part) for part in list(highlights)[:3] if _clean_snippet(part)],
    }


def _exa_results_from_response(response: object) -> list[object]:
    results = _read_attr(response, "results", [])
    return list(results or [])


def search_exa(query: str, *, num_results: int = DEFAULT_WEB_RESULTS) -> dict[str, Any]:
    client = _load_exa_client()
    if client is None:
        return {
            "error": "exa_not_configured",
            "message": "EXA_API_KEY is not configured.",
            "results": [],
        }
    limit = max(1, min(MAX_WEB_RESULTS, int(num_results or DEFAULT_WEB_RESULTS)))
    response = client.search(
        query,
        num_results=limit,
        type="auto",
        contents={"highlights": True},
    )
    return {
        "results": [
            _normalize_exa_result(item, rank)
            for rank, item in enumerate(_exa_results_from_response(response), 1)
        ]
    }


def search_wikipedia(query: str, *, num_results: int = MAX_WIKI_RESULTS) -> dict[str, Any]:
    limit = max(1, min(MAX_WIKI_RESULTS, int(num_results or MAX_WIKI_RESULTS)))
    response = requests.get(
        WIKI_API_URL,
        params={
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": limit,
            "format": "json",
        },
        headers={"User-Agent": "Obital/1.0"},
        timeout=5,
    )
    response.raise_for_status()
    hits = response.json().get("query", {}).get("search", [])
    results = []
    for rank, hit in enumerate(hits[:limit], 1):
        title = str(hit.get("title", "") or "")
        results.append({
            "source": "wikipedia",
            "rank": rank,
            "title": title,
            "url": f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}" if title else "",
            "snippet": _clean_snippet(hit.get("snippet", ""))[:1200],
            "highlights": [],
        })
    return {"results": results}


def search_web_and_wikipedia(query: str, *, num_results: int = DEFAULT_WEB_RESULTS) -> dict[str, Any]:
    with ThreadPoolExecutor(max_workers=2) as executor:
        web_future = executor.submit(search_exa, query, num_results=num_results)
        wiki_future = executor.submit(search_wikipedia, query, num_results=MAX_WIKI_RESULTS)
        web_payload = _safe_result(web_future)
        wiki_payload = _safe_result(wiki_future)

    web_results = list(web_payload.get("results", []))
    wiki_results = list(wiki_payload.get("results", []))
    payload: dict[str, Any] = {
        "query": query,
        "provider_requests": 2,
        "results": web_results + wiki_results,
        "sources": {
            "web": _source_status(web_payload),
            "wikipedia": _source_status(wiki_payload),
        },
    }
    return payload


def _safe_result(future) -> dict[str, Any]:
    try:
        result = future.result()
        return result if isinstance(result, dict) else {"results": []}
    except Exception as exc:
        return {"error": "search_failed", "message": str(exc), "results": []}


def _source_status(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("error"):
        return {"ok": False, "error": payload.get("error"), "message": payload.get("message", "")}
    return {"ok": True, "count": len(payload.get("results", []))}


def clear_exa_client_cache() -> None:
    _load_exa_client.cache_clear()
