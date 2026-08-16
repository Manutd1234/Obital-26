from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from backend.app.api.common import require_account
from backend.app.api.rate_limit import rate_limit
from backend.app.services.crypto_local import encrypt_bytes, require_dek
from backend.app.services.db import SessionLocal, is_stateless_db_mode
from backend.app.services.generation_jobs import create_qa_generation_job, get_generation_job
from engine.pipeline.models import Topic
from engine.shared.account_paths import account_qa_output_path

router = APIRouter(tags=["pipeline"])
logger = logging.getLogger(__name__)

_QA_ARTIFACT_PATH = "output/output_curriculum_qa.json"

_MAX_GLOBAL_CONTEXT_CHARS = 200_000
_MAX_CONTEXT_CHUNKS = 50
_MAX_CHUNK_CHARS = 10_000
_MAX_TOPICS = 12
_MAX_QA_PER_TOPIC = 10
_CURRICULUM_TIMEOUT = 120
_TOPIC_QA_TIMEOUT = 90


def _get_llm_config() -> tuple[str, str, str]:
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash").strip()
    base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()
    if not api_key:
        raise HTTPException(status_code=500, detail="DEEPSEEK_API_KEY is not configured.")
    return api_key, model, base_url


def _upsert_artifact(
    db_session, *, account_id: str, relative_path: str, content_bytes: bytes, user_dek: bytes | None
) -> None:
    from backend.app.services.models import AccountArtifactModel

    stored = encrypt_bytes(require_dek(user_dek, "pipeline artifact"), content_bytes)
    sha256_hash = hashlib.sha256(content_bytes).hexdigest()

    exists = (
        db_session.query(AccountArtifactModel)
        .where(
            AccountArtifactModel.account_id == account_id,
            AccountArtifactModel.relative_path == relative_path,
        )
        .first()
    )
    if exists:
        exists.content = stored
        exists.sha256 = sha256_hash
        exists.size_bytes = len(stored)
        exists.updated_at = datetime.now(UTC)
    else:
        db_session.add(
            AccountArtifactModel(
                account_id=account_id,
                relative_path=relative_path,
                content=stored,
                sha256=sha256_hash,
                size_bytes=len(stored),
                updated_at=datetime.now(UTC),
            )
        )
    db_session.commit()


# ── Schemas ──


class ChunkEmbedding(BaseModel):
    chunk_id: str
    text: str
    vector: list[float]


class SaveEmbeddingsRequest(BaseModel):
    chunks: list[ChunkEmbedding]
    model: str | None = None
    dimension: int | None = Field(default=None, ge=1, le=4096)
    provider: str | None = None
    runtime: str | None = None

    @field_validator("chunks")
    @classmethod
    def validate_chunk_vectors(cls, v: list[ChunkEmbedding]) -> list[ChunkEmbedding]:
        dims = {len(chunk.vector) for chunk in v}
        if len(dims) > 1:
            raise ValueError("all chunk vectors must have the same dimension")
        return v


class GenerateCurriculumRequest(BaseModel):
    global_context: str = Field(..., max_length=_MAX_GLOBAL_CONTEXT_CHARS)
    max_topics: int = Field(default=6, ge=1, le=_MAX_TOPICS)


class GenerateTopicQARequest(BaseModel):
    topic_id: str
    topic_title: str
    topic_summary: str
    context_chunks: list[str] = Field(..., max_length=_MAX_CONTEXT_CHUNKS)
    qa_per_topic: int = Field(default=5, ge=1, le=_MAX_QA_PER_TOPIC)

    @field_validator("context_chunks")
    @classmethod
    def validate_chunks(cls, v: list[str]) -> list[str]:
        for i, chunk in enumerate(v):
            if len(chunk) > _MAX_CHUNK_CHARS:
                v[i] = chunk[:_MAX_CHUNK_CHARS]
        return v


class SavePipelineResultRequest(BaseModel):
    curriculum_qa: dict[str, Any]


class GenerationJobResponse(BaseModel):
    job_id: str
    type: str
    status: str
    progress: int
    created_at: str
    updated_at: str
    error: str | None = None
    result: dict[str, Any] | None = None


# ── Endpoints ──


@router.put("/docs/{doc_id}/embeddings")
def save_doc_embeddings(
    doc_id: str,
    payload: SaveEmbeddingsRequest,
    auth: tuple[str, bytes | None] = Depends(require_account),
) -> dict:
    """Save client-generated document embeddings."""
    account_id, user_dek = auth
    try:
        from backend.app.services.storage import save_embeddings

        if payload.dimension is not None:
            for chunk in payload.chunks:
                if len(chunk.vector) != payload.dimension:
                    raise HTTPException(
                        status_code=400,
                        detail=f"chunk vector dimension {len(chunk.vector)} does not match declared dimension {payload.dimension}",
                    )
        embeddings_data = {
            "model": payload.model,
            "dimension": payload.dimension,
            "provider": payload.provider,
            "runtime": payload.runtime,
            "chunks": [chunk.model_dump() for chunk in payload.chunks],
        }
        save_embeddings(
            doc_id=doc_id,
            embeddings_data=embeddings_data,
            account_id=account_id,
            user_dek=user_dek,
        )
        return {"status": "ok", "chunks_embedded": len(payload.chunks)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to save embeddings for doc '%s'", doc_id)
        raise HTTPException(status_code=500, detail="Failed to save embeddings") from exc


@router.post("/pipeline/generate-curriculum")
async def generate_curriculum(
    payload: GenerateCurriculumRequest,
    auth: tuple[str, bytes | None] = Depends(require_account),
    _: None = Depends(rate_limit("pipeline")),
) -> dict:
    api_key, model, base_url = _get_llm_config()

    try:
        from engine.pipeline.qa_pipeline import CurriculumQAPipeline

        pipeline = CurriculumQAPipeline(
            api_key=api_key,
            model=model,
            base_url=base_url,
            max_topics=payload.max_topics,
        )

        chunk_texts = [c.strip() for c in payload.global_context.split("\n\n") if c.strip()]
        rows = [{"text": text} for text in chunk_texts]

        curriculum = await asyncio.wait_for(
            pipeline.generate_curriculum(rows),
            timeout=_CURRICULUM_TIMEOUT,
        )
        return curriculum.model_dump()
    except asyncio.TimeoutError:
        logger.exception("Curriculum generation timed out")
        raise HTTPException(status_code=504, detail="Curriculum generation timed out")
    except HTTPException:
        raise
    except Exception:
        logger.exception("Curriculum generation failed")
        raise HTTPException(status_code=500, detail="Curriculum generation failed")


@router.post("/pipeline/generate-topic-qa")
async def generate_topic_qa(
    payload: GenerateTopicQARequest,
    auth: tuple[str, bytes | None] = Depends(require_account),
    _: None = Depends(rate_limit("pipeline")),
) -> dict:
    api_key, model, base_url = _get_llm_config()

    try:
        from engine.pipeline.qa_pipeline import CurriculumQAPipeline

        pipeline = CurriculumQAPipeline(
            api_key=api_key,
            model=model,
            base_url=base_url,
            qa_per_topic=payload.qa_per_topic,
            retrieval_mode="keyword",
            retrieval_top_k=len(payload.context_chunks),
        )

        rows = [{"text": text} for text in payload.context_chunks]
        topic = Topic(
            id=payload.topic_id,
            title=payload.topic_title,
            summary=payload.topic_summary,
            prerequisites=[],
        )

        qa_data = await asyncio.wait_for(
            pipeline.generate_topic_qa(rows, topic),
            timeout=_TOPIC_QA_TIMEOUT,
        )
        return qa_data.model_dump()
    except asyncio.TimeoutError:
        logger.exception("Topic QA generation timed out")
        raise HTTPException(status_code=504, detail="Topic QA generation timed out")
    except HTTPException:
        raise
    except Exception:
        logger.exception("Topic QA generation failed")
        raise HTTPException(status_code=500, detail="Topic QA generation failed")


@router.post("/pipeline/save")
def save_pipeline_result(
    payload: SavePipelineResultRequest,
    auth: tuple[str, bytes | None] = Depends(require_account),
    _: None = Depends(rate_limit("pipeline")),
) -> dict:
    account_id, user_dek = auth
    try:
        content_bytes = json.dumps(payload.curriculum_qa).encode("utf-8")

        if is_stateless_db_mode():
            if user_dek is None:
                raise HTTPException(
                    status_code=400,
                    detail="user_dek is required for stateless E2EE saving",
                )
            assert SessionLocal is not None
            with SessionLocal() as db:
                _upsert_artifact(
                    db,
                    account_id=account_id,
                    relative_path=_QA_ARTIFACT_PATH,
                    content_bytes=content_bytes,
                    user_dek=user_dek,
                )
        else:
            from engine.shared.account_paths import account_qa_output_path

            output_path = account_qa_output_path(
                base_dir=Path(__file__).resolve().parents[3],
                account_id=account_id,
            )
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(payload.curriculum_qa, indent=2), encoding="utf-8"
            )

        return {"status": "ok", "saved_path": _QA_ARTIFACT_PATH}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to save pipeline result")
        raise HTTPException(
            status_code=500, detail="Failed to save pipeline result"
        )


@router.post("/pipeline/jobs/qa-generation", response_model=GenerationJobResponse)
def create_qa_job(
    auth: tuple[str, bytes | None] = Depends(require_account),
    _: None = Depends(rate_limit("pipeline")),
) -> dict:
    account_id, user_dek = auth
    return create_qa_generation_job(account_id=account_id, user_dek=user_dek)


@router.get("/pipeline/jobs/{job_id}", response_model=GenerationJobResponse)
def get_pipeline_job(
    job_id: str,
    auth: tuple[str, bytes | None] = Depends(require_account),
) -> dict:
    account_id, _user_dek = auth
    job = get_generation_job(job_id=job_id, account_id=account_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
