from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
import httpx

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Footer, Header

from frontend.services.api_client import ApiClient
from frontend.services.engine_cli_bridge import EngineCliBridge
from frontend.state import AppState
from frontend.ui.chat_pane import ChatPane, SendChatMessage, RetrySessionRequested
from frontend.ui.doc_pane import DocChunkSelected, DocPane
from frontend.ui.dashboard_screen import DashboardScreen
from frontend.ui.lesson_pane import LessonPane, LessonSelected
from frontend.ui.settings_screen import SettingsScreen
from frontend.ui.unlock_modal import UnlockModal

_SIGNED_IN_RE = re.compile(r"Signed in as:\s*(?P<user>[a-z0-9_\-]+)\s*\|\s*Account id:\s*(?P<account>[a-z0-9_\-]+)")

#test
class ObitalTextualApp(App):
    TITLE = "Obital Frontend (Textual)"
    BINDINGS = [
        ("ctrl+s", "open_settings", "Settings"),
        ("ctrl+d", "open_dashboard", "Dashboard"),
        ("f6", "check_backend", "Check"),
        ("f7", "sync_cloud", "Sync"),
        ("f8", "pull_cloud", "Pull"),
        ("f9", "upload_file", "Upload"),
    ]
    CSS = """
    Screen { layout: vertical; }
    #main { height: 1fr; }
    #lesson-pane, #doc-pane { width: 1fr; min-width: 28; border: round #666666; padding: 1; }
    #chat-pane { width: 2fr; min-width: 56; border: round #666666; padding: 1; }
    #chat-transcript, #lesson-detail, #doc-detail { height: 1fr; }
    #lesson-detail, #doc-detail { overflow-y: auto; }
    #lesson-list { height: 10; }
    #doc-list, #doc-chunks { height: 7; }
    """

    def __init__(self, base_url: str = "http://127.0.0.1:8000", account_id: str = "admin") -> None:
        super().__init__()
        self.state = AppState(account_id=account_id)
        self.api = ApiClient(base_url=base_url, account_id=account_id)
        self.api.client_id = self.state.client_id
        self.lesson_pane = LessonPane()
        self.chat_pane = ChatPane()
        self.doc_pane = DocPane()
        self.bridge = EngineCliBridge(on_output=self._on_cli_output, on_exit=self._on_cli_exit)
        self._account_ready = False
        self._side_refresh_task: asyncio.Task | None = None
        self._ops_task: asyncio.Task | None = None
        self._lessons_snapshot = ""
        self._docs_snapshot = ""
        self._pending_quiz: dict | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main"):
            yield self.lesson_pane
            yield self.chat_pane
            yield self.doc_pane
        yield Footer()

    async def on_mount(self) -> None:
        self.chat_pane.append_line("[ui] starting CLI bridge...")
        self.chat_pane.append_line(f"[ops] sync target account: {self.state.account_id} (F7 push, F8 pull, /sync, /pull)")
        try:
            await self.bridge.start()
            self.chat_pane.append_line("[ui] CLI bridge ready")
        except Exception as exc:
            self.chat_pane.append_line(f"[ui] CLI startup failed: {exc}")
        self._focus_chat_input()

    async def on_unmount(self) -> None:
        if self._side_refresh_task is not None:
            self._side_refresh_task.cancel()
        # Auto-push before exit
        self.chat_pane.append_line("[ops] auto-pushing to cloud before exit...")
        await self._auto_sync_push()
        await self.bridge.stop()
        await self.api.close()

    def _on_cli_output(self, line: str) -> None:
        self.call_after_refresh(self.chat_pane.append_line, line)
        match = _SIGNED_IN_RE.search(line)
        if match:
            account_id = match.group("account")
            asyncio.create_task(self._set_account_context(account_id))

    def _on_cli_exit(self, return_code: int | None) -> None:
        code_text = "unknown" if return_code is None else str(return_code)
        self.call_after_refresh(self.chat_pane.append_line, f"[ui] CLI process exited (code {code_text})")

    async def _set_account_context(self, account_id: str) -> None:
        self.state.account_id = account_id
        self.api.account_id = account_id
        self.api.client_id = self.state.client_id
        if self.api.unlock_token:
            self.api.unlock_token = None
            self.state.unlock_token = None
            self.chat_pane.append_line("[ui] signed into new account — previous unlock cleared")
        self._account_ready = True
        try:
            self.state.reward_session_id = await self.api.start_reward_session()
        except Exception as exc:
            self.chat_pane.append_line(f"[ui] reward session start failed: {exc}")
        # Auto-pull latest from cloud on login
        self.chat_pane.append_line("[ops] auto-pulling latest data from cloud...")
        asyncio.create_task(self._auto_sync_pull())
        await self._refresh_side_panes()
        if self._side_refresh_task is None:
            self._side_refresh_task = asyncio.create_task(self._periodic_refresh())

    async def _periodic_refresh(self) -> None:
        while True:
            await asyncio.sleep(5)
            await self._refresh_side_panes()

    async def _refresh_side_panes(self) -> None:
        try:
            lessons, docs = await asyncio.gather(
                self.api.list_lessons(source="account"),
                self.api.list_docs(),
            )
            lessons_snapshot = json.dumps(lessons, sort_keys=True, ensure_ascii=True)
            docs_snapshot = json.dumps(docs, sort_keys=True, ensure_ascii=True)
            if lessons_snapshot != self._lessons_snapshot:
                self.lesson_pane.set_lessons(lessons)
                self._lessons_snapshot = lessons_snapshot
            if docs_snapshot != self._docs_snapshot:
                self.doc_pane.set_docs(docs)
                self._docs_snapshot = docs_snapshot
        except Exception as exc:
            self.chat_pane.append_line(f"[ui] side refresh failed: {exc}")

    async def _open_settings_screen(self) -> None:
        label = self.state.account_id if self._account_ready else "not signed in"
        screen = SettingsScreen(account_id=label)
        self.push_screen(screen)
        if not self._account_ready:
            screen.set_status("Sign in first to access account settings.")
            return
        try:
            payload = await self.api.get_settings()
            screen.set_prompt(payload.get("chat", {}).get("system_prompt", ""))
            screen.set_status(f"Loaded settings for account '{self.state.account_id}'.")
        except Exception as exc:
            screen.set_status(f"Settings load failed: {exc}")

    async def _open_dashboard_screen(self) -> None:
        label = self.state.account_id if self._account_ready else "not signed in"
        screen = DashboardScreen(account_id=label)
        self.push_screen(screen)
        if not self._account_ready:
            screen.set_status("Sign in first to access reward dashboard.")
            return
        try:
            payload = await self.api.get_rewards_dashboard(limit=30)
            screen.set_data(payload)
            screen.set_status(f"Loaded dashboard for account '{self.state.account_id}'.")
        except Exception as exc:
            screen.set_status(f"Dashboard load failed: {exc}")

    def _focus_chat_input(self) -> None:
        if self.chat_pane.input.screen is not None:
            self.chat_pane.input.focus()

    @staticmethod
    def _format_http_error(exc: Exception) -> str:
        response = getattr(exc, "response", None)
        if response is None:
            return str(exc)
        try:
            detail = response.json().get("detail")
        except Exception:
            detail = None
        if detail:
            return str(detail)
        return f"HTTP {response.status_code}: {response.reason_phrase}"

    # ── Lesson helpers ────────────────────────────────────

    @staticmethod
    def _parse_lesson_objective(lesson: dict) -> tuple[str, int, int]:
        """Parse status, current checkpoint, and total from lesson objective string."""
        status = "not_started"
        cur = 0
        total = len(lesson.get("checkpoints", []))
        objective = lesson.get("objective", "")
        if objective.startswith("status="):
            sm = re.search(r"status=([^,\s]+)", objective)
            cm = re.search(r"checkpoint=(\d+)/(\d+)", objective)
            if sm:
                status = sm.group(1)
            if cm:
                cur = int(cm.group(1))
                total = int(cm.group(2))
        return status, cur, total

    async def _handle_lessons_list(self) -> None:
        try:
            lessons = await self.api.list_lessons(source="account")
        except Exception as exc:
            self.chat_pane.append_line(f"failed to list lessons: {exc}")
            return
        if not lessons:
            self.chat_pane.append_line("No lessons. Use /lesson start <topic> to create one.")
            return
        self.chat_pane.append_line(f"Lessons ({len(lessons)}):")
        for i, l in enumerate(lessons):
            status, cur, total = self._parse_lesson_objective(l)
            self.chat_pane.append_line(
                f"  {i + 1}. {l.get('title', l.get('id', '?'))} "
                f"[{status}] checkpoint {cur}/{total}"
            )

    async def _handle_lesson_start(self, topic: str) -> None:
        try:
            result = await self.api.start_lesson(topic)
            self.chat_pane.append_line(f"Started lesson: {result['title']} ({result['lesson_id']})")
            await self._refresh_side_panes()
            lessons = await self.api.list_lessons(source="account")
            for i, l in enumerate(lessons):
                if l.get("id") == result["lesson_id"]:
                    self.lesson_pane.select_index(i)
                    self.state.lesson_id = result["lesson_id"]
                    self.chat_pane.append_line(f"Lesson {i + 1} selected as active.")
                    break
        except Exception as exc:
            self.chat_pane.append_line(f"failed to start lesson: {exc}")

    async def _handle_lesson_status(self) -> None:
        try:
            lessons = await self.api.list_lessons(source="account")
        except Exception as exc:
            self.chat_pane.append_line(f"failed to list lessons: {exc}")
            return
        if not lessons:
            self.chat_pane.append_line("No lessons.")
            return
        for l in lessons:
            if l.get("id") == self.state.lesson_id:
                status, cur, total = self._parse_lesson_objective(l)
                self.chat_pane.append_line(f"Active lesson: {l.get('title', l.get('id', '?'))}")
                self.chat_pane.append_line(f"  Status: {status} | Checkpoint: {cur}/{total}")
                for i, c in enumerate(l.get("checkpoints", [])):
                    marker = "x" if i < cur else ">" if i == cur else " "
                    self.chat_pane.append_line(
                        f"  [{marker}] {i + 1}. {c.get('title', '?')} "
                        f"({c.get('difficulty', '?')}, {c.get('num_questions', 0)}q, "
                        f"pass {c.get('pass_threshold_percent', 0)}%)"
                    )
                return
        self.chat_pane.append_line(f"No active lesson (id={self.state.lesson_id}).")

    async def _handle_lesson_continue(self) -> None:
        try:
            lessons = await self.api.list_lessons(source="account")
        except Exception as exc:
            self.chat_pane.append_line(f"failed to list lessons: {exc}")
            return
        in_progress = []
        for i, l in enumerate(lessons):
            status, _, _ = self._parse_lesson_objective(l)
            if status in ("in_progress", "active"):
                in_progress.append((i, l))
        if len(in_progress) == 0:
            self.chat_pane.append_line("No in-progress lessons. Use /lesson start <topic>.")
            return
        if len(in_progress) > 1:
            self.chat_pane.append_line("Multiple in-progress lessons:")
            for i, l in in_progress:
                self.chat_pane.append_line(f"  {i + 1}. {l.get('title', l.get('id', '?'))}")
            self.chat_pane.append_line("Use /lesson <index> to select one.")
            return
        idx, l = in_progress[0]
        self.lesson_pane.select_index(idx)
        self.state.lesson_id = l.get("id")
        self.chat_pane.append_line(f"Lesson {idx + 1} selected: {l.get('title', l.get('id', '?'))}")

    async def _handle_lesson_restart(self, selector: str) -> None:
        lesson_id = None
        try:
            lessons = await self.api.list_lessons(source="account")
            if selector.isdigit():
                idx = int(selector) - 1
                if 0 <= idx < len(lessons):
                    lesson_id = lessons[idx].get("id")
            if not lesson_id:
                lesson_id = selector
            await self.api.restart_lesson(lesson_id)
            self.chat_pane.append_line(f"Lesson restarted: {lesson_id}")
            await self._refresh_side_panes()
        except Exception as exc:
            self.chat_pane.append_line(f"failed to restart lesson: {exc}")

    async def _handle_lesson_quiz(self) -> None:
        if not self.state.lesson_id:
            self.chat_pane.append_line("No active lesson. Use /lesson <index> to select one.")
            return
        try:
            lessons = await self.api.list_lessons(source="account")
        except Exception as exc:
            self.chat_pane.append_line(f"failed to load lessons: {exc}")
            return
        lesson = None
        for l in lessons:
            if l.get("id") == self.state.lesson_id:
                lesson = l
                break
        if not lesson:
            self.chat_pane.append_line("Active lesson not found.")
            return
        cp = lesson.get("checkpoints", [])
        if not cp:
            self.chat_pane.append_line("Lesson has no checkpoints.")
            return
        _, cur, _ = self._parse_lesson_objective(lesson)
        if cur >= len(cp):
            self.chat_pane.append_line("All checkpoints completed.")
            return
        checkpoint = cp[cur]
        topic = ""
        for b in lesson.get("content_blocks", []):
            if b.startswith("topic: "):
                topic = b[7:]
                break
        if not topic:
            topic = lesson.get("title", "").replace("Lesson: ", "")
        self.chat_pane.append_line(
            f"Running checkpoint: {checkpoint.get('title', '?')} "
            f"({checkpoint.get('difficulty', '?')}, {checkpoint.get('num_questions', 3)}q)..."
        )
        try:
            quiz = await self.api.build_quiz(
                topic=topic or None,
                difficulty=checkpoint.get("difficulty") or None,
                question_mode=checkpoint.get("question_mode", "mcq"),
                num_questions=checkpoint.get("num_questions", 3),
            )
            if quiz.get("build_indicator") == "failed":
                self.chat_pane.append_line(f"Quiz build failed: {quiz.get('failure_reason', '?')}")
                return
            self.chat_pane.append_line(f"Quiz built: {len(quiz.get('questions', []))} questions.")
            for i, q in enumerate(quiz.get("questions", [])):
                opts = q.get("options", [])
                self.chat_pane.append_line(f"  Q{i + 1}: {q.get('question', '?')}")
                for j, opt in enumerate(opts):
                    self.chat_pane.append_line(f"    {chr(65 + j)}. {opt}")
            self.chat_pane.append_line("Submit answers via: /lesson answer <A,B,C,...>")
            self._pending_quiz = quiz
        except Exception as exc:
            self.chat_pane.append_line(f"quiz build failed: {self._format_http_error(exc)}")
            self.chat_pane.append_line("Tip: upload/generate lesson QA first, or run this after cloud pull has synced QA data.")

    async def _handle_lesson_answer(self, answers_str: str) -> None:
        if not self._pending_quiz:
            self.chat_pane.append_line("No active quiz. Use /lesson quiz first.")
            return
        quiz = self._pending_quiz
        questions = quiz.get("questions", [])
        parts = [a.strip().upper() for a in answers_str.split(",")]
        if len(parts) != len(questions):
            self.chat_pane.append_line(
                f"Expected {len(questions)} answers, got {len(parts)}. "
                f"Format: /lesson answer A,B,C,..."
            )
            return
        answers = []
        for i, letter in enumerate(parts):
            q = questions[i]
            opts = q.get("options", [])
            idx = ord(letter) - 65 if len(letter) == 1 and letter.isalpha() else -1
            if 0 <= idx < len(opts):
                answers.append({"question_id": q["question_id"], "answer": opts[idx]})
            else:
                answers.append({"question_id": q["question_id"], "answer": letter})
        self.chat_pane.append_line("Grading...")
        try:
            result = await self.api.grade_quiz(quiz, answers)
            self._pending_quiz = None
            pct = round(result.get("score_percent", 0))
            correct = result.get("correct_answers", 0)
            total = result.get("total_questions", 0)
            self.chat_pane.append_line(f"Score: {pct}% ({correct}/{total} correct)")
            passed, cur = await self._record_lesson_attempt_from_quiz(result)
            if passed is not None:
                if passed:
                    self.chat_pane.append_line("Checkpoint passed.")
                else:
                    self.chat_pane.append_line("Checkpoint not passed. Use /lesson quiz to retry.")
                lesson_data = await self._get_active_lesson_data()
                if lesson_data:
                    _, new_cur, total_cp = self._parse_lesson_objective(lesson_data)
                    if new_cur < total_cp:
                        next_cp = lesson_data.get("checkpoints", [])[new_cur] if new_cur < len(lesson_data.get("checkpoints", [])) else None
                        if next_cp:
                            self.chat_pane.append_line(
                                f"Next checkpoint: {next_cp.get('title', '?')} "
                                f"({next_cp.get('difficulty', '?')}). Use /lesson quiz to continue."
                            )
                    else:
                        self.chat_pane.append_line("Lesson completed!")
        except Exception as exc:
            self.chat_pane.append_line(f"grading failed: {self._format_http_error(exc)}")

    async def _get_active_lesson_data(self) -> dict | None:
        try:
            lessons = await self.api.list_lessons(source="account")
            for l in lessons:
                if l.get("id") == self.state.lesson_id:
                    return l
        except Exception:
            pass
        return None

    async def _record_lesson_attempt_from_quiz(self, result: dict) -> tuple[bool | None, int]:
        """Record lesson attempt after quiz grading. Returns (passed, current_checkpoint)."""
        if not self.state.lesson_id:
            return None, 0
        lesson_data = await self._get_active_lesson_data()
        if not lesson_data:
            return None, 0
        _, cur, _ = self._parse_lesson_objective(lesson_data)
        cp = lesson_data.get("checkpoints", [])
        if cur >= len(cp):
            return None, cur
        checkpoint = cp[cur]
        passed = result.get("score_percent", 0) >= checkpoint.get("pass_threshold_percent", 100)
        try:
            attempt_result = await self.api.record_lesson_attempt(
                lesson_id=self.state.lesson_id,
                checkpoint_index=cur,
                quiz_id=result.get("quiz_id", ""),
                score_percent=result.get("score_percent", 0),
                passed=passed,
            )
            await self._refresh_side_panes()
            return passed, attempt_result.get("current_checkpoint_index", cur)
        except Exception as exc:
            self.chat_pane.append_line(f"Warning: failed to record attempt: {exc}")
            return passed, cur

    # ── Sync / Unlock helpers ─────────────────────────────

    async def _ensure_unlocked(self, purpose: str) -> bool:
        """Ensure the app has a valid unlock token. Returns True if unlocked."""
        if self.api.unlock_token:
            return True
        if not self.state.account_id.strip():
            self.chat_pane.append_line(f"[ops] no account selected for {purpose}")
            return False

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                db_health = await client.get(f"{self.api.base_url}/health/db")
            if not db_health.json().get("enabled"):
                self.chat_pane.append_line(
                    "[ops] cloud sync requires DB mode. Run the backend with: .\\scripts\\dev\\run_backend_cloud.ps1"
                )
                return False
        except Exception:
            self.chat_pane.append_line("[ops] cannot reach backend for health check")
            return False

        modal = UnlockModal(self.state.account_id, purpose=purpose)
        result = await self.push_screen(modal)
        if result and self.api.unlock_token:
            self.chat_pane.append_line("[ui] account unlocked")
            return True
        return False

    async def _run_sync_push(self) -> None:
        if not await self._ensure_unlocked("sync push"):
            return
        self.chat_pane.append_line("[ops] pushing account data to cloud...")
        try:
            result = await self.api.sync_push()
            self.chat_pane.append_line(
                f"[ops] push complete: {result['uploaded_files']} uploaded, "
                f"{result['deleted_remote_files']} deleted remote, "
                f"registry={'ok' if result['registry_synced'] else 'skip'}"
            )
        except Exception as exc:
            self._log_sync_error("sync push", exc)

    async def _run_sync_pull(self) -> None:
        if not await self._ensure_unlocked("sync pull"):
            return
        self.chat_pane.append_line("[ops] pulling account data from cloud...")
        try:
            result = await self.api.sync_pull()
            self.chat_pane.append_line(
                f"[ops] pull complete: chat={result['chat_sessions']}, rewards={result['reward_sessions']}, "
                f"{result['downloaded_files']} downloaded, {result['deleted_local_files']} deleted local, "
                f"registry={'ok' if result['registry_synced'] else 'skip'}"
            )
            await self._refresh_side_panes()
        except Exception as exc:
            self._log_sync_error("sync pull", exc)

    def _log_sync_error(self, label: str, exc: Exception) -> None:
        try:
            status = exc.response.status_code
            detail = exc.response.json().get("detail", str(exc))
            if status == 401:
                self.api.unlock_token = None
                self.state.unlock_token = None
                self.chat_pane.append_line(f"[ops] {label} failed: session expired. Re-unlock and try again.")
            elif status == 429:
                self.chat_pane.append_line(f"[ops] {label} failed: account temporarily locked. Wait and retry.")
            else:
                self.chat_pane.append_line(f"[ops] {label} failed ({status}): {detail}")
        except Exception:
            self.chat_pane.append_line(f"[ops] {label} failed: {exc}")

    async def _auto_sync_pull(self) -> None:
        if not self.api.unlock_token:
            self.chat_pane.append_line(
                "[ops] cloud sync is locked. Press F8 or type /pull to unlock and pull."
            )
            return
        try:
            result = await self.api.sync_pull()
            self.chat_pane.append_line(
                f"[ops] auto-pull: chat={result.get('chat_sessions', 0)}, "
                f"files={result.get('downloaded_files', 0)}, "
                f"registry={'ok' if result.get('registry_synced') else 'skip'}"
            )
            await self._refresh_side_panes()
        except Exception as exc:
            self.chat_pane.append_line(f"[ops] auto-pull failed: {exc}")

    async def _auto_sync_push(self) -> None:
        if not self._account_ready or not self.api.unlock_token:
            return
        try:
            result = await self.api.sync_push()
            self.chat_pane.append_line(
                f"[ops] auto-push: {result.get('uploaded_files', 0)} files, "
                f"registry={'ok' if result.get('registry_synced') else 'skip'}"
            )
        except Exception as exc:
            self.chat_pane.append_line(f"[ops] auto-push failed: {exc}")

    async def _handle_lesson_delete(self, selector: str) -> None:
        lesson_id = None
        try:
            lessons = await self.api.list_lessons(source="account")
            if selector.isdigit():
                idx = int(selector) - 1
                if 0 <= idx < len(lessons):
                    lesson_id = lessons[idx].get("id")
            if not lesson_id:
                lesson_id = selector
            await self.api.delete_lesson(lesson_id)
            if self.state.lesson_id == lesson_id:
                self.state.lesson_id = None
            self.chat_pane.append_line(f"Lesson moved to bin (30 days): {lesson_id}")
            await self._refresh_side_panes()
        except Exception as exc:
            self.chat_pane.append_line(f"failed to delete lesson: {exc}")

    async def _handle_doc_delete(self, selector: str) -> None:
        doc_id = None
        try:
            docs = await self.api.list_docs()
            if selector.isdigit():
                idx = int(selector) - 1
                if 0 <= idx < len(docs):
                    doc_id = docs[idx].get("id")
            if not doc_id:
                doc_id = selector
            await self.api.delete_doc(doc_id)
            if doc_id in (self.state.selected_doc_chunk_ids or []):
                self.state.selected_doc_chunk_ids = []
            self.chat_pane.append_line(f"Document moved to bin (30 days): {doc_id}")
            await self._refresh_side_panes()
        except Exception as exc:
            self.chat_pane.append_line(f"failed to delete document: {exc}")

    async def _handle_bin_list(self) -> None:
        try:
            items = await self.api.list_bin()
            if not items:
                self.chat_pane.append_line("bin is empty")
                return
            self.chat_pane.append_line(f"bin ({len(items)} items):")
            for item in items:
                days = item.get("days_remaining", 0)
                self.chat_pane.append_line(
                    f"  [{item['id'][:8]}] {item['item_type']}: {item['name']} ({days:.0f}d left)"
                )
            self.chat_pane.append_line("use /bin restore <id> to restore")
        except Exception as exc:
            self.chat_pane.append_line(f"failed to list bin: {exc}")

    async def _handle_bin_restore(self, bin_id: str) -> None:
        try:
            result = await self.api.restore_bin_item(bin_id)
            self.chat_pane.append_line(f"restored: {result.get('item_type', '?')} {result.get('item_id', '?')}")
            await self._refresh_side_panes()
        except Exception as exc:
            self.chat_pane.append_line(f"failed to restore: {exc}")

    # ── Event handlers ────────────────────────────────────

    @on(LessonSelected)
    def on_lesson_selected(self, event: LessonSelected) -> None:
        self.state.lesson_id = event.lesson.get("id")

    @on(DocChunkSelected)
    def on_doc_chunk_selected(self, event: DocChunkSelected) -> None:
        self.state.selected_doc_chunk_ids = event.chunk_ids

    @on(UnlockModal.Submitted)
    async def on_unlock_modal_submitted(self, event: UnlockModal.Submitted) -> None:
        event.modal.set_status("Verifying passphrase...")
        try:
            token = await self.api.unlock_account(event.passphrase)
        except Exception as exc:
            try:
                detail = exc.response.json().get("detail", str(exc))
            except Exception:
                detail = str(exc)
            event.modal.set_status(f"Unlock failed: {detail}")
            return

        self.state.unlock_token = token
        self.chat_pane.append_line("[ui] account unlocked")
        event.modal.dismiss(token)

    @on(RetrySessionRequested)
    async def on_retry_session_requested(self, event: RetrySessionRequested) -> None:
        self.state.chat_session_id = None
        self.chat_pane.append_line("[ui] Chat session reset. A new session will be created on your next message.")
        self._focus_chat_input()

    @on(SendChatMessage)
    async def on_send_chat_message(self, event: SendChatMessage) -> None:
        text = event.text.strip()
        if not text:
            return
        if text.startswith("/lessons"):
            parts = text.split(maxsplit=1)
            sub = parts[1].strip() if len(parts) > 1 else ""
            if not sub:
                await self._handle_lessons_list()
            elif sub == "start":
                self.chat_pane.append_line("usage: /lessons start <topic>")
            elif sub.startswith("start "):
                topic = sub[6:].strip()
                await self._handle_lesson_start(topic) if topic else self.chat_pane.append_line("usage: /lessons start <topic>")
            elif sub.startswith("open "):
                idx_str = sub[5:].strip()
                if idx_str.isdigit():
                    ok = self.lesson_pane.select_index(int(idx_str) - 1)
                    self.chat_pane.append_line("lesson selected" if ok else "lesson index out of range")
                else:
                    self.chat_pane.append_line("usage: /lessons open <index>")
            elif sub == "continue":
                await self._handle_lesson_continue()
            elif sub.startswith("restart "):
                selector = sub[8:].strip()
                await self._handle_lesson_restart(selector) if selector else self.chat_pane.append_line("usage: /lessons restart <index>")
            elif sub.startswith("delete "):
                selector = sub[7:].strip()
                await self._handle_lesson_delete(selector) if selector else self.chat_pane.append_line("usage: /lessons delete <index>")
            elif sub == "status":
                await self._handle_lesson_status()
            elif sub == "clear":
                self.chat_pane.append_line("Lesson context cleared.")
            else:
                self.chat_pane.append_line("usage: /lessons [start|open|delete|continue|restart|status|clear]")
            self._focus_chat_input()
            return
        if text.startswith("/lesson "):
            arg = text.split(maxsplit=1)[1].strip()
            if arg.isdigit():
                ok = self.lesson_pane.select_index(int(arg) - 1)
                self.chat_pane.append_line("lesson selected" if ok else "lesson index out of range")
            elif arg == "status":
                await self._handle_lesson_status()
            elif arg == "quiz":
                await self._handle_lesson_quiz()
            elif arg == "continue":
                await self._handle_lesson_continue()
            elif arg.startswith("start "):
                topic = arg[6:].strip()
                if topic:
                    await self._handle_lesson_start(topic)
                else:
                    self.chat_pane.append_line("usage: /lesson start <topic>")
            elif arg.startswith("restart "):
                selector = arg[8:].strip()
                if selector:
                    await self._handle_lesson_restart(selector)
                else:
                    self.chat_pane.append_line("usage: /lesson restart <index>")
            elif arg.startswith("delete "):
                selector = arg[7:].strip()
                if selector:
                    await self._handle_lesson_delete(selector)
                else:
                    self.chat_pane.append_line("usage: /lesson delete <index>")
            elif arg.startswith("answer "):
                answers = arg[7:].strip()
                if answers:
                    await self._handle_lesson_answer(answers)
                else:
                    self.chat_pane.append_line("usage: /lesson answer A,B,C,...")
            else:
                self.chat_pane.append_line("usage: /lesson <index> | start | delete | status | quiz | answer | continue | restart")
            self._focus_chat_input()
            return
        if text.startswith("/doc "):
            arg = text.split(maxsplit=1)[1].strip()
            if arg.isdigit():
                ok = self.doc_pane.select_doc_index(int(arg) - 1)
                self.chat_pane.append_line("doc selected" if ok else "doc index out of range")
            elif arg.startswith("delete "):
                selector = arg[7:].strip()
                if selector:
                    await self._handle_doc_delete(selector)
                else:
                    self.chat_pane.append_line("usage: /doc delete <index>")
            else:
                self.chat_pane.append_line("usage: /doc <index> | delete <index>")
            self._focus_chat_input()
            return
        if text.startswith("/chunk "):
            arg = text.split(maxsplit=1)[1].strip()
            if arg.isdigit():
                ok = self.doc_pane.select_chunk_index(int(arg) - 1)
                self.chat_pane.append_line("chunk selected" if ok else "chunk index out of range")
            else:
                self.chat_pane.append_line("usage: /chunk <index>")
            self._focus_chat_input()
            return
        if text.startswith("/upload"):
            arg = text.split(maxsplit=1)[1].strip() if " " in text else ""
            if not arg:
                self.run_worker(self.action_upload_file())
                self._focus_chat_input()
                return
            file_path = Path(arg)
            if not file_path.exists() or not file_path.is_file():
                self.chat_pane.append_line(f"invalid file path: {file_path}")
                self._focus_chat_input()
                return
            try:
                await self.api.upload_doc(file_path)
                await self._refresh_side_panes()
                self.chat_pane.append_line("uploaded")
            except Exception as exc:
                self.chat_pane.append_line(f"upload failed: {exc}")
            self._focus_chat_input()
            return
        if text == "/sync":
            await self._start_ops_action("sync_cloud")
            self._focus_chat_input()
            return
        if text == "/pull":
            await self._start_ops_action("pull_cloud")
            self._focus_chat_input()
            return
        if text.startswith("/bin"):
            arg = text.split(maxsplit=1)[1].strip() if " " in text else ""
            if not arg:
                await self._handle_bin_list()
            elif arg.startswith("restore "):
                bin_id = arg[8:].strip()
                if bin_id:
                    await self._handle_bin_restore(bin_id)
                else:
                    self.chat_pane.append_line("usage: /bin restore <id>")
            else:
                self.chat_pane.append_line("usage: /bin | /bin restore <id>")
            self._focus_chat_input()
            return

        self.chat_pane.append_line(f"You: {text}")
        if not self._account_ready:
            if not self.bridge.is_alive:
                self.chat_pane.append_line("[ui] CLI bridge is not running. Restart the app.")
                self._focus_chat_input()
                return
            await self.bridge.send(text)
            self._focus_chat_input()
            return

        self.chat_pane.input.disabled = True
        try:
            if not self.state.chat_session_id:
                self.state.chat_session_id = await self.api.create_session(lesson_id=self.state.lesson_id)
            async for stream_event in self.api.stream_message(
                session_id=self.state.chat_session_id,
                message=text,
                lesson_id=self.state.lesson_id,
                doc_chunk_ids=self.state.selected_doc_chunk_ids,
            ):
                event_type = stream_event.get("event")
                event_data = str(stream_event.get("data", ""))
                if event_type == "token":
                    self.chat_pane.append_assistant_chunk(event_data)
                elif event_type == "tool":
                    self.chat_pane.append_line(f"[tool] {event_data}")
                elif event_type == "thinking":
                    continue
                elif event_type == "chat_status":
                    if isinstance(stream_event.get("data"), dict):
                        detail = str(stream_event["data"].get("detail") or "")
                    else:
                        detail = event_data
                    if detail:
                        self.chat_pane.append_line(f"[status] {detail}")
                elif event_type == "error":
                    self.chat_pane.append_line(f"[ui] chat stream error: {event_data}")
                elif event_type == "done":
                    break
            self.chat_pane.finish_assistant_stream()
        except Exception as exc:
            self.chat_pane.finish_assistant_stream()
            self.chat_pane.append_line(f"[ui] chat request failed: {exc}")
        finally:
            self.chat_pane.input.disabled = False
            self._focus_chat_input()

        if self.state.reward_session_id:
            try:
                await self.api.append_reward_event(self.state.reward_session_id, note="chat_message")
            except Exception as exc:
                self.chat_pane.append_line(f"[ui] reward log failed: {exc}")

    async def _start_ops_action(self, action: str) -> None:
        if self._ops_task is not None and not self._ops_task.done():
            self.chat_pane.append_line("[ops] command already running")
            return
        if action in {"sync_cloud", "pull_cloud"} and not self.state.account_id.strip():
            self.chat_pane.append_line("[ops] no account selected for sync")
            return
        if action == "check_backend":
            self._ops_task = asyncio.create_task(self._run_check_backend())
        elif action == "sync_cloud":
            self._ops_task = asyncio.create_task(self._run_sync_push())
        elif action == "pull_cloud":
            self._ops_task = asyncio.create_task(self._run_sync_pull())

    async def _run_check_backend(self) -> None:
        self.chat_pane.append_line("[ops] checking backend health...")
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                health = await client.get(f"{self.api.base_url}/health")
                db_health = await client.get(f"{self.api.base_url}/health/db")
            self.chat_pane.append_line(f"[ops] /health {health.status_code}: {health.text}")
            self.chat_pane.append_line(f"[ops] /health/db {db_health.status_code}: {db_health.text}")
        except Exception as exc:
            self.chat_pane.append_line(f"[ops] backend check failed: {exc}")

    async def action_open_settings(self) -> None:
        await self._open_settings_screen()

    async def action_open_dashboard(self) -> None:
        await self._open_dashboard_screen()

    async def action_check_backend(self) -> None:
        await self._start_ops_action("check_backend")

    async def action_sync_cloud(self) -> None:
        await self._start_ops_action("sync_cloud")

    async def action_pull_cloud(self) -> None:
        await self._start_ops_action("pull_cloud")

    @on(SettingsScreen.RefreshRequested)
    async def on_refresh_settings_requested(self, event: SettingsScreen.RefreshRequested) -> None:
        if not self._account_ready:
            event.screen.set_status("Sign in first to access account settings.")
            return
        try:
            payload = await self.api.get_settings()
            event.screen.set_prompt(payload.get("chat", {}).get("system_prompt", ""))
            event.screen.set_status(f"Loaded settings for account '{self.state.account_id}'.")
        except Exception as exc:
            event.screen.set_status(f"Settings load failed: {exc}")

    @on(SettingsScreen.SaveRequested)
    async def on_save_settings_requested(self, event: SettingsScreen.SaveRequested) -> None:
        if not self._account_ready:
            event.screen.set_status("Sign in first to access account settings.")
            return
        try:
            await self.api.patch_settings({"chat": {"system_prompt": event.system_prompt}})
            event.screen.set_status(f"Saved settings for account '{self.state.account_id}'.")
        except Exception as exc:
            event.screen.set_status(f"Settings save failed: {exc}")

    async def action_upload_file(self) -> None:
        self.chat_pane.append_line("[ui] Opening file selection dialog...")
        try:
            file_path_str = await asyncio.to_thread(self._open_file_dialog)
            if not file_path_str:
                self.chat_pane.append_line(
                    "[ui] File selection cancelled or graphical file dialog is unavailable. "
                    "You can still upload using: /upload <absolute_path>"
                )
                self._focus_chat_input()
                return

            file_path = Path(file_path_str)
            if not file_path.exists() or not file_path.is_file():
                self.chat_pane.append_line(f"[ui] Invalid file path: {file_path}")
                self._focus_chat_input()
                return

            self.chat_pane.append_line(f"[ui] Uploading file: {file_path.name}...")
            await self.api.upload_doc(file_path)
            await self._refresh_side_panes()
            self.chat_pane.append_line(f"[ui] Successfully uploaded: {file_path.name}")
        except Exception as exc:
            self.chat_pane.append_line(f"[ui] Upload failed: {exc}")
        self._focus_chat_input()

    @staticmethod
    def _open_file_dialog() -> str | None:
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            file_path = filedialog.askopenfilename(
                title="Select File to Upload",
                filetypes=[
                    ("Text Files", "*.txt"),
                    ("Markdown Files", "*.md"),
                    ("All Files", "*.*")
                ]
            )
            root.destroy()
            return file_path or None
        except Exception:
            return None


if __name__ == "__main__":
    import os
    import sys
    base_url = os.environ.get("OBITAL_BACKEND_URL", "http://127.0.0.1:8000")
    if len(sys.argv) > 1:
        base_url = sys.argv[1]
    ObitalTextualApp(base_url=base_url).run()
