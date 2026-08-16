import logging

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field, field_validator

from backend.app.services.usage import record_doc_upload, check_doc_limit
from backend.app.api.common import require_account
from backend.app.schemas import Doc
from backend.app.services.embeddings import embed_text, embedding_dim, server_embeddings_enabled
from backend.app.services.storage import add_doc, get_doc, list_docs, search_chunks_by_vector, search_docs, delete_doc
from backend.app.services.upload_limits import MAX_UPLOAD_BYTES

logger = logging.getLogger(__name__)

EMBEDDING_DIM = embedding_dim()
SERVER_EMBEDDING_ENABLED = server_embeddings_enabled()
_MAX_TAGS = 5

router = APIRouter(prefix="/docs", tags=["doc"])


def _strip_vectors(docs: list[Doc]) -> list[Doc]:
    for doc in docs:
        for chunk in doc.chunks:
            chunk.vector = None
    return docs


async def _extract_doc_tags(text: str) -> list[str]:
    """Extract up to 5 concise topic tags from document text via LLM."""
    try:
        from backend.app.services.llm_json import call_json_model
        result = await call_json_model(
            role="tagger",
            payload={
                "task": "Extract 3-5 concise topic tags",
                "text": text[:2000],
            },
            fallback={"tags": []},
            timeout=15,
        )
        tags = result.get("tags", [])
        if isinstance(tags, list):
            return [t.lower().strip()[:50] for t in tags if isinstance(t, str) and t.strip()][:_MAX_TAGS]
    except Exception:
        logger.debug("Tag extraction failed for doc", exc_info=True)
    return []


def _generate_embeddings_background(
    doc_id: str,
    chunk_ids: list[str],
    chunk_texts: list[str],
    account_id: str,
    user_dek: bytes | None,
    doc_tags: list[str] | None = None,
) -> None:
    """Generate embeddings for a document in the background."""
    try:
        from backend.app.services.storage import save_embeddings
        from backend.app.services.embeddings import embed_texts
        vectors = embed_texts(chunk_texts)
        embeddings_data = {"chunks": [
            {"chunk_id": c_id, "text": text, "vector": v}
            for c_id, text, v in zip(chunk_ids, chunk_texts, vectors)
        ]}
        save_embeddings(
            doc_id=doc_id,
            embeddings_data=embeddings_data,
            account_id=account_id,
            user_dek=user_dek,
            doc_tags=doc_tags,
        )
    except Exception as exc:
        logger.warning("Background embedding failed for doc %s: %s", doc_id, exc)


@router.get("", response_model=list[Doc])
def get_docs(
    auth: tuple[str, bytes | None] = Depends(require_account),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> list[Doc]:
    account_id, user_dek = auth
    docs = list_docs(account_id=account_id, user_dek=user_dek, include_content=False, limit=limit, offset=offset)
    return _strip_vectors(docs)


@router.get("/search", response_model=list[Doc])
def search_doc_by_text(
    q: str = Query(..., min_length=1),
    tags: str | None = Query(None, description="Comma-separated filter tags (OR match)"),
    auth: tuple[str, bytes | None] = Depends(require_account),
) -> list[Doc]:
    account_id, user_dek = auth
    filter_tags = [t.strip().lower() for t in tags.split(",") if t.strip()] if tags else None
    return _strip_vectors(search_docs(q, account_id=account_id, user_dek=user_dek, filter_tags=filter_tags))


class VectorSearchRequest(BaseModel):
    query_vector: list[float]
    top_k: int = Field(default=3, ge=1, le=50)
    threshold: float = Field(default=0.35, ge=0.0, le=1.0)
    filter_tags: list[str] = Field(default_factory=list)

    @field_validator("query_vector")
    @classmethod
    def validate_vector_length(cls, v: list[float]) -> list[float]:
        if len(v) != EMBEDDING_DIM:
            raise ValueError(f"query_vector must have {EMBEDDING_DIM} dimensions, got {len(v)}")
        return v


@router.post("/search")
def search_by_vector(
    payload: VectorSearchRequest,
    auth: tuple[str, bytes | None] = Depends(require_account),
) -> list[dict]:
    account_id, user_dek = auth
    return search_chunks_by_vector(
        query_vector=payload.query_vector,
        top_k=payload.top_k,
        threshold=payload.threshold,
        account_id=account_id,
        user_dek=user_dek,
        filter_tags=payload.filter_tags or None,
    )


class TextSearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = Field(default=3, ge=1, le=50)
    threshold: float = Field(default=0.35, ge=0.0, le=1.0)
    filter_tags: list[str] = Field(default_factory=list)


@router.post("/search/text")
def search_by_text(
    payload: TextSearchRequest,
    auth: tuple[str, bytes | None] = Depends(require_account),
) -> list[dict]:
    """Embed the query text server-side, then search for similar chunks."""
    if not SERVER_EMBEDDING_ENABLED:
        raise HTTPException(
            status_code=410,
            detail="Server-side text embedding is disabled. Use POST /docs/search with a client-generated query_vector.",
        )
    account_id, user_dek = auth
    query_vector = embed_text(payload.query)
    return search_chunks_by_vector(
        query_vector=query_vector,
        top_k=payload.top_k,
        threshold=payload.threshold,
        account_id=account_id,
        user_dek=user_dek,
        filter_tags=payload.filter_tags or None,
    )


@router.get("/{doc_id}", response_model=Doc)
def get_doc_by_id(
    doc_id: str,
    auth: tuple[str, bytes | None] = Depends(require_account),
) -> Doc:
    account_id, user_dek = auth
    doc = get_doc(doc_id, account_id=account_id, user_dek=user_dek)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    for chunk in doc.chunks:
        chunk.vector = None
    return doc


@router.post("/upload", response_model=Doc)
async def upload_doc(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    auth: tuple[str, bytes | None] = Depends(require_account),
) -> Doc:
    account_id, user_dek = auth
    chunks = []
    total = 0
    while True:
        chunk = await file.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=f"File exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit")
        chunks.append(chunk)
    content = b"".join(chunks)
    if not content:
        raise HTTPException(status_code=400, detail="Empty upload")

    check_doc_limit(account_id, len(content))
    doc = add_doc(
        name=file.filename or "uploaded.txt",
        content=content,
        account_id=account_id,
        user_dek=user_dek,
    )

    record_doc_upload(account_id, doc=1, bytes_added=len(content))

    if SERVER_EMBEDDING_ENABLED:
        chunk_texts = [chunk.text for chunk in (doc.chunks or [])]
        chunk_ids = [chunk.chunk_id for chunk in (doc.chunks or [])]
        if chunk_texts:
            doc_text = "\n".join(chunk_texts)
            doc_tags = await _extract_doc_tags(doc_text)
            background_tasks.add_task(
                _generate_embeddings_background,
                doc_id=doc.id,
                chunk_ids=chunk_ids,
                chunk_texts=chunk_texts,
                account_id=account_id,
                user_dek=user_dek,
                doc_tags=doc_tags,
            )

    return doc


@router.delete("/{doc_id}")
def delete_doc_endpoint(
    doc_id: str,
    auth: tuple[str, bytes | None] = Depends(require_account),
) -> dict:
    """Move a document to the bin (30-day TTL)."""
    account_id, user_dek = auth
    success = delete_doc(doc_id, account_id=account_id, user_dek=user_dek)
    if not success:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"status": "deleted", "doc_id": doc_id}
