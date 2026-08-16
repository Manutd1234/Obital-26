from pathlib import Path
from dotenv import load_dotenv
import os
import logging

load_dotenv()

from contextlib import asynccontextmanager
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.responses import Response
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from backend.app.api.chat import router as chat_router
from backend.app.api.doc import router as doc_router
from backend.app.api.lesson import router as lesson_router
from backend.app.api.rewards import router as rewards_router
from backend.app.api.settings import router as settings_router
from backend.app.api.auth import router as auth_router
from backend.app.api.sync import router as sync_router
from backend.app.api.quiz import router as quiz_router
from backend.app.api.pipeline import router as pipeline_router
from backend.app.services.db import SessionLocal, db_enabled, db_healthcheck, init_db
from backend.app.services.unlock_session import cleanup_expired_unlock_sessions
from backend.app.api.usage import router as usage_router
from backend.app.api.bin import router as bin_router
from backend.app.api.friends import router as friends_router
from backend.app.api.presence import router as presence_router
from backend.app.api.leaderboard import router as leaderboard_router
from backend.app.api.workspace import router as workspace_router
from backend.app.api.shop import router as shop_router
from backend.app.services.upload_limits import MAX_MULTIPART_REQUEST_BYTES, MAX_UPLOAD_BYTES

LOG_LEVEL = os.getenv("OBITAL_LOG_LEVEL", "INFO").strip().upper()
LOG_FILE = Path(os.getenv("OBITAL_LOG_FILE", "backend/data/logs/backend.log"))


def _configure_logging() -> None:
    level = getattr(logging, LOG_LEVEL, logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if not any(getattr(handler, "_obital_console", False) for handler in root.handlers):
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        console.setLevel(level)
        console._obital_console = True  # type: ignore[attr-defined]
        root.addHandler(console)

    if not any(getattr(handler, "_obital_file", False) for handler in root.handlers):
        try:
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
        except OSError as exc:
            logging.getLogger(__name__).warning(
                "File logging disabled; could not write to %s: %s",
                LOG_FILE,
                exc,
            )
            return
        file_handler.setFormatter(formatter)
        file_handler.setLevel(level)
        file_handler._obital_file = True  # type: ignore[attr-defined]
        root.addHandler(file_handler)


_configure_logging()


class UploadSizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: StarletteRequest, call_next) -> Response:
        content_length = request.headers.get("content-length")
        if content_length and request.method in ("POST", "PUT", "PATCH"):
            # Content-Length is the complete multipart body, not just the
            # uploaded file. Let /docs/upload account for multipart framing;
            # its route-level streaming check enforces the exact file limit.
            request_limit = MAX_MULTIPART_REQUEST_BYTES if (
                request.url.path == "/docs/upload"
                and request.headers.get("content-type", "").lower().startswith("multipart/form-data")
            ) else MAX_UPLOAD_BYTES
            if int(content_length) > request_limit:
                return Response(
                    content="Upload too large. Maximum file size is "
                    f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
                    status_code=413,
                )
        return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if db_enabled():
        assert SessionLocal is not None
        with SessionLocal() as db:
            cleanup_expired_unlock_sessions(db)
    from backend.app.services.sync_scheduler import start_scheduler, stop_scheduler
    start_scheduler()
    from backend.app.services.bin_cleanup import start_bin_cleanup, stop_bin_cleanup
    start_bin_cleanup()
    yield
    stop_scheduler()
    stop_bin_cleanup()
    from backend.app.services.db import dispose_db_engines
    dispose_db_engines()

app = FastAPI(
    title="Obital Backend",
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(UploadSizeLimitMiddleware)

cors_origins_str = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:3000")
if cors_origins_str:
    cors_origins = [o.strip() for o in cors_origins_str.split(",") if o.strip()]
else:
    cors_origins = []

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/db")
def health_db() -> dict[str, object]:
    payload = db_healthcheck()
    payload["status"] = "ok" if payload.get("ok") else "error"
    return payload


templates_dir = Path(__file__).parent / "templates"
assets_dir = templates_dir / "assets"
if assets_dir.exists():
    app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="static_assets")


@app.get("/", response_class=HTMLResponse)
def root() -> HTMLResponse:
    template_path = templates_dir / "index.html"
    if template_path.exists():
        try:
            with open(template_path, "r", encoding="utf-8") as f:
                return HTMLResponse(content=f.read())
        except Exception as exc:
            return HTMLResponse(content=f"<h1>Error loading template</h1><p>{exc}</p>", status_code=500)
    return HTMLResponse(content="<h1>Template not found</h1><p>Place your UI at backend/app/templates/index.html</p>", status_code=404)


app.include_router(lesson_router)
app.include_router(chat_router)
app.include_router(doc_router)
app.include_router(settings_router)
app.include_router(rewards_router)
app.include_router(auth_router)
app.include_router(sync_router)
app.include_router(quiz_router)
app.include_router(pipeline_router)
app.include_router(usage_router)
app.include_router(bin_router)
app.include_router(friends_router)
app.include_router(presence_router)
app.include_router(leaderboard_router)
app.include_router(workspace_router)
app.include_router(shop_router)
