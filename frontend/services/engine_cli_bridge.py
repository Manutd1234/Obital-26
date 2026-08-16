from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from collections.abc import Callable


class EngineCliBridge:
    def __init__(self, on_output: Callable[[str], None], on_exit: Callable[[int | None], None] | None = None) -> None:
        self.on_output = on_output
        self.on_exit = on_exit
        self.proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._stopping = False

    async def start(self) -> None:
        if self.proc is not None:
            return
        self._stopping = False
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        self.proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-u",
            "-m",
            "engine.quiz.quiz_chatbot_cli",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        self._reader_task = asyncio.create_task(self._read_output())

    async def send(self, text: str) -> None:
        if self.proc is None or self.proc.stdin is None:
            return
        self.proc.stdin.write((text + "\n").encode("utf-8"))
        await self.proc.stdin.drain()

    async def stop(self) -> None:
        if self.proc is None:
            return
        self._stopping = True
        if self.proc.stdin:
            with contextlib.suppress(Exception):
                self.proc.stdin.close()
        with contextlib.suppress(Exception):
            self.proc.terminate()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self.proc.wait(), timeout=2.0)
        if self._reader_task is not None:
            self._reader_task.cancel()
            with contextlib.suppress(Exception):
                await self._reader_task
        self.proc = None
        self._reader_task = None

    @property
    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def _read_output(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        buffer = ""
        while True:
            try:
                if not buffer:
                    chunk = await self.proc.stdout.read(1)
                else:
                    chunk = await asyncio.wait_for(self.proc.stdout.read(1), timeout=0.2)
            except asyncio.TimeoutError:
                # Flush partial prompts emitted by input(...) which do not end with newline.
                if buffer:
                    self.on_output(buffer.rstrip("\r\n"))
                    buffer = ""
                continue

            if not chunk:
                if buffer:
                    self.on_output(buffer.rstrip("\r\n"))
                break

            text = chunk.decode("utf-8", errors="replace")
            buffer += text
            if "\n" in buffer:
                parts = buffer.split("\n")
                complete = parts[:-1]
                buffer = parts[-1]
                for line in complete:
                    cleaned = line.rstrip("\r\n")
                    if cleaned:
                        self.on_output(cleaned)
        if self.on_exit is not None and not self._stopping:
            self.on_exit(self.proc.returncode if self.proc is not None else None)
