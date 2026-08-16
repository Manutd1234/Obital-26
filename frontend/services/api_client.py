from __future__ import annotations

import json
from pathlib import Path
from typing import AsyncIterator
from urllib.parse import quote

import httpx


class ApiClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8000", account_id: str = "admin") -> None:
        self.base_url = base_url.rstrip("/")
        self.account_id = account_id
        self._unlock_token: str | None = None
        self._client_id: str = ""
        self._client = httpx.AsyncClient(timeout=30.0)

    async def close(self) -> None:
        await self._client.aclose()

    @property
    def unlock_token(self) -> str | None:
        return self._unlock_token

    @unlock_token.setter
    def unlock_token(self, value: str | None) -> None:
        self._unlock_token = value

    @property
    def client_id(self) -> str:
        return self._client_id

    @client_id.setter
    def client_id(self, value: str) -> None:
        self._client_id = value

    def _auth_headers(self) -> dict[str, str]:
        headers = {"X-Client-Id": self._client_id}
        if self._unlock_token:
            headers["X-Unlock-Token"] = self._unlock_token
        return headers

    async def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        merged = {"account_id": self.account_id}
        if params:
            merged.update(params)
        response = await self._client.get(
            f"{self.base_url}{path}",
            params=merged,
            headers=self._auth_headers(),
        )
        response.raise_for_status()
        return response

    async def _post(self, path: str, json_payload: dict | None = None, params: dict | None = None) -> httpx.Response:
        merged = {"account_id": self.account_id}
        if params:
            merged.update(params)
        response = await self._client.post(
            f"{self.base_url}{path}",
            json=json_payload,
            params=merged,
            headers=self._auth_headers(),
        )
        response.raise_for_status()
        return response

    async def _patch(self, path: str, json_payload: dict | None = None, params: dict | None = None) -> httpx.Response:
        merged = {"account_id": self.account_id}
        if params:
            merged.update(params)
        response = await self._client.patch(
            f"{self.base_url}{path}",
            json=json_payload,
            params=merged,
            headers=self._auth_headers(),
        )
        response.raise_for_status()
        return response

    async def _delete(self, path: str, params: dict | None = None) -> httpx.Response:
        merged = {"account_id": self.account_id}
        if params:
            merged.update(params)
        response = await self._client.delete(
            f"{self.base_url}{path}",
            params=merged,
            headers=self._auth_headers(),
        )
        response.raise_for_status()
        return response

    # ── Auth ──────────────────────────────────────────────

    async def unlock_account(self, passphrase: str) -> str:
        response = await self._post("/auth/unlock", json_payload={"passphrase": passphrase})
        token = response.json()["token"]
        self._unlock_token = token
        return token

    async def lock_account(self) -> None:
        await self._post("/auth/lock")
        self._unlock_token = None

    # ── Sync ──────────────────────────────────────────────

    async def sync_push(self) -> dict:
        return (await self._post("/sync/push")).json()

    async def sync_pull(self) -> dict:
        return (await self._post("/sync/pull")).json()

    # ── Lessons / Docs ────────────────────────────────────

    async def list_lessons(self, source: str = "account") -> list[dict]:
        return (await self._get("/lessons", params={"source": source})).json()

    async def start_lesson(self, topic: str, title: str | None = None) -> dict:
        payload = {"topic": topic}
        if title:
            payload["title"] = title
        return (await self._post("/lessons", json_payload=payload)).json()

    async def restart_lesson(self, lesson_id: str) -> dict:
        return (await self._post(f"/lessons/{quote(lesson_id, safe='')}/restart")).json()

    async def delete_lesson(self, lesson_id: str) -> dict:
        return (await self._delete(f"/lessons/{quote(lesson_id, safe='')}")).json()

    async def record_lesson_attempt(
        self, lesson_id: str, checkpoint_index: int, quiz_id: str, score_percent: float, passed: bool
    ) -> dict:
        payload = {
            "checkpoint_index": checkpoint_index,
            "quiz_id": quiz_id,
            "score_percent": score_percent,
            "passed": passed,
        }
        return (await self._post(f"/lessons/{quote(lesson_id, safe='')}/attempt", json_payload=payload)).json()

    async def build_quiz(
        self,
        topic: str | None = None,
        difficulty: str | None = None,
        question_mode: str = "mcq",
        num_questions: int = 5,
    ) -> dict:
        payload = {
            "topic": topic,
            "difficulty": difficulty,
            "question_mode": question_mode,
            "num_questions": num_questions,
        }
        return (await self._post("/quiz/build", json_payload=payload)).json()

    async def grade_quiz(self, quiz: dict, answers: list[dict[str, str]]) -> dict:
        payload = {"quiz": quiz, "answers": answers}
        return (await self._post("/quiz/grade", json_payload=payload)).json()

    async def list_docs(self) -> list[dict]:
        return (await self._get("/docs")).json()

    async def search_docs(self, query: str) -> list[dict]:
        return (await self._get("/docs/search", params={"q": query})).json()

    async def upload_doc(self, path: Path) -> dict:
        with path.open("rb") as f:
            files = {"file": (path.name, f)}
            response = await self._client.post(
                f"{self.base_url}/docs/upload",
                params={"account_id": self.account_id},
                files=files,
                headers=self._auth_headers(),
            )
        response.raise_for_status()
        return response.json()

    async def delete_doc(self, doc_id: str) -> dict:
        return (await self._delete(f"/docs/{quote(doc_id, safe='')}")).json()

    # ── Bin ────────────────────────────────────────────

    async def list_bin(self) -> list[dict]:
        return (await self._get("/bin")).json()

    async def restore_bin_item(self, bin_id: str) -> dict:
        return (await self._post(f"/bin/{quote(bin_id, safe='')}/restore")).json()

    async def permanent_delete_bin_item(self, bin_id: str) -> dict:
        return (await self._delete(f"/bin/{quote(bin_id, safe='')}")).json()

    # ── Chat ──────────────────────────────────────────────

    async def create_session(self, lesson_id: str | None = None) -> str:
        response = await self._post("/chat/sessions", json_payload={"lesson_id": lesson_id})
        return response.json()["session_id"]

    async def get_session(self, session_id: str) -> dict:
        return (await self._get(f"/chat/sessions/{session_id}")).json()

    async def send_message(
        self,
        session_id: str,
        message: str,
        lesson_id: str | None = None,
        doc_chunk_ids: list[str] | None = None,
    ) -> dict:
        payload = {
            "message": message,
            "lesson_id": lesson_id,
            "doc_chunk_ids": doc_chunk_ids or [],
        }
        return (await self._post(f"/chat/sessions/{session_id}/messages", json_payload=payload)).json()

    async def stream_message(
        self,
        session_id: str,
        message: str,
        lesson_id: str | None = None,
        doc_chunk_ids: list[str] | None = None,
    ) -> AsyncIterator[dict]:
        payload = {
            "message": message,
            "lesson_id": lesson_id,
            "doc_chunk_ids": doc_chunk_ids or [],
        }
        async with self._client.stream(
            "POST",
            f"{self.base_url}/chat/sessions/{session_id}/messages/stream",
            json=payload,
            params={"account_id": self.account_id},
            headers=self._auth_headers(),
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                try:
                    yield json.loads(line[6:])
                except json.JSONDecodeError:
                    continue

    # ── Settings ──────────────────────────────────────────

    async def get_settings(self) -> dict:
        return (await self._get("/settings")).json()

    async def patch_settings(self, payload: dict) -> dict:
        return (await self._patch("/settings", json_payload=payload)).json()

    # ── Rewards ───────────────────────────────────────────

    async def get_rewards_dashboard(self, limit: int = 20) -> dict:
        return (await self._get("/rewards/dashboard", params={"limit": limit})).json()

    async def start_reward_session(self) -> str:
        response = await self._post("/rewards/sessions")
        return response.json()["session_id"]

    async def append_reward_event(self, session_id: str, note: str = "") -> None:
        await self._post(f"/rewards/sessions/{session_id}/events", params={"note": note})
