"""Shared fixtures for browser automation tests using Playwright."""
from __future__ import annotations

import socket
import threading
import time

import pytest
import uvicorn


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def browser_ctx():
    """Launch a headless Chromium browser for the test session."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture(scope="session")
def app_url():
    """Start the FastAPI app on a random port and yield the base URL."""
    from backend.app.main import app

    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                break
        except OSError:
            time.sleep(0.1)
    else:
        raise RuntimeError("Server did not start in time")

    yield f"http://127.0.0.1:{port}"

    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture()
def page(browser_ctx):
    """Blank page for testing pure JS functions (no app loaded)."""
    pg = browser_ctx.new_page()
    pg.set_content("<html><body></body></html>")
    yield pg
    pg.close()


@pytest.fixture()
def app_page(browser_ctx, app_url):
    """Page navigated to the live Web TUI app for integration tests."""
    pg = browser_ctx.new_page()
    pg.goto(app_url, wait_until="domcontentloaded")
    yield pg
    pg.close()
