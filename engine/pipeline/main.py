from __future__ import annotations

import argparse
import asyncio
import fnmatch
import hashlib
import json
from pathlib import Path

from engine.pipeline.config import load_settings
from engine.pipeline.constants import (
    CHEAP_QA_DEFAULTS,
    DEFAULT_BACKEND,
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_RETRIEVAL_ALPHA,
    DEFAULT_RETRIEVAL_MODE,
    DEFAULT_RETRIEVAL_TOP_K,
    DEFAULT_RAW_TEXT_OUTPUT,
    DEFAULT_WORKERS,
    NORMAL_QA_DEFAULTS,
    SUPPORTED_INPUT_EXTENSIONS,
)
from engine.pipeline.extract_text import extract_text_from_path
from engine.pipeline.qa_pipeline import CurriculumQAPipeline
from engine.pipeline.rag_engine import IngestionResult, RAGIngestionService
from engine.shared.account_paths import (
    account_cache_dir,
    account_documents_dir,
    account_graph_dir,
    account_output_dir,
    account_qa_output_path,
    normalize_account_id,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="CLI backend flow for extraction, ingestion, and curriculum+QA generation."
    )
    parser.add_argument(
        "--input",
        help="Path to one .pdf, .txt, or .md input file.",
    )
    parser.add_argument(
        "--input-dir",
        default=None,
        help="Folder containing .pdf/.txt/.md files for batch ingestion.",
    )
    parser.add_argument(
        "--account-id",
        default="default",
        help="Account id used to isolate documents, cache, vectors, and outputs.",
    )
    parser.add_argument(
        "--include",
        action="append",
        default=[],
        help="Glob include filter for discovered paths (repeatable), e.g. --include \"CS20404S/*.md\".",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Glob exclude filter for discovered paths (repeatable), e.g. --exclude \"**/drafts/*\".",
    )
    parser.add_argument(
        "--max-documents",
        type=int,
        default=None,
        help="Limit number of discovered documents after filtering.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print discovered files and effective settings without extraction, ingestion, or QA calls.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_RAW_TEXT_OUTPUT),
        help="Output path for extracted text.",
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to env file containing DEEPSEEK_API_KEY and optional settings.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help="Parallel workers for chunk embedding.",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help="Characters per chunk before embedding.",
    )
    parser.add_argument(
        "--chunk-overlap",
        type=int,
        default=DEFAULT_CHUNK_OVERLAP,
        help="Overlapping characters between chunks.",
    )
    parser.add_argument(
        "--embedding-model",
        default=DEFAULT_EMBEDDING_MODEL,
        help="Embedding model id for sentence-transformers backend.",
    )
    parser.add_argument(
        "--backend",
        choices=["lightrag", "jsonl", "web"],
        default=DEFAULT_BACKEND,
        help="Vector storage backend.",
    )
    parser.add_argument(
        "--backend-strict",
        action="store_true",
        help="Fail instead of falling back when requested backend cannot be initialized.",
    )
    parser.add_argument(
        "--force-reingest",
        action="store_true",
        help="Bypass hash cache and re-ingest input file.",
    )
    parser.add_argument(
        "--purge-missing",
        action="store_true",
        help="Purge missing file entries from ingestion manifest before ingest.",
    )
    parser.add_argument(
        "--run-qa",
        action="store_true",
        help="Run Steps 6/7/8 after ingestion to generate curriculum + topic QA output JSON.",
    )
    parser.add_argument(
        "--changed-only-qa",
        action="store_true",
        help="Skip QA generation when corpus hash and QA settings are unchanged.",
    )
    parser.add_argument(
        "--force-qa",
        action="store_true",
        help="Force QA generation even when changed-only checks would skip.",
    )
    parser.add_argument(
        "--qa-output",
        default=None,
        help="Path for merged curriculum+QA JSON output (defaults to account-scoped output).",
    )
    parser.add_argument(
        "--qa-concurrency",
        type=int,
        default=None,
        help="Concurrent topic QA generation requests.",
    )
    parser.add_argument(
        "--qa-per-topic",
        type=int,
        default=None,
        help="Number of QA pairs per topic.",
    )
    parser.add_argument(
        "--max-global-context-chunks",
        type=int,
        default=None,
        help="Max retrieved chunks used to generate curriculum.",
    )
    parser.add_argument(
        "--max-topic-context-chunks",
        type=int,
        default=None,
        help="Max retrieved chunks used for each topic QA synthesis.",
    )
    parser.add_argument(
        "--retrieval-mode",
        choices=["keyword", "vector", "hybrid"],
        default=DEFAULT_RETRIEVAL_MODE,
        help="Topic context retrieval mode for QA generation.",
    )
    parser.add_argument(
        "--retrieval-top-k",
        type=int,
        default=DEFAULT_RETRIEVAL_TOP_K,
        help="Top-k chunks selected for topic context retrieval.",
    )
    parser.add_argument(
        "--retrieval-alpha",
        type=float,
        default=DEFAULT_RETRIEVAL_ALPHA,
        help="Hybrid retrieval weight for vector score (0..1).",
    )
    parser.add_argument(
        "--max-topics",
        type=int,
        default=None,
        help="Hard cap on number of curriculum topics.",
    )
    parser.add_argument(
        "--schema-retry-limit",
        type=int,
        default=3,
        help="Retry count when model JSON fails schema validation.",
    )
    parser.add_argument(
        "--cheap-mode",
        action="store_true",
        help="Low-cost preset for QA generation. Explicit flags still override these values.",
    )
    return parser


def run() -> None:
    parser = build_parser()
    args = parser.parse_args()

    settings = load_settings(args.env_file)
    account_id = normalize_account_id(args.account_id)
    scoped_docs_dir = account_documents_dir(base_dir=settings.base_dir, account_id=account_id)
    scoped_output_dir = account_output_dir(base_dir=settings.base_dir, account_id=account_id)
    scoped_cache_dir = account_cache_dir(base_dir=settings.base_dir, account_id=account_id)
    scoped_graph_dir = account_graph_dir(base_dir=settings.base_dir, account_id=account_id)
    scoped_default_qa_output = account_qa_output_path(base_dir=settings.base_dir, account_id=account_id)

    input_dir = Path(args.input_dir).expanduser().resolve() if args.input_dir else scoped_docs_dir
    qa_output_arg = Path(args.qa_output).expanduser().resolve() if args.qa_output else scoped_default_qa_output
    raw_output_arg = Path(args.output).expanduser().resolve()
    if args.output == str(DEFAULT_RAW_TEXT_OUTPUT):
        raw_output_arg = scoped_output_dir / "raw_text.txt"

    discovered_paths = _resolve_inputs(
        single_input=args.input,
        input_dir=input_dir,
    )
    input_paths = _filter_inputs(
        discovered_paths=discovered_paths,
        input_dir=input_dir,
        include_patterns=args.include,
        exclude_patterns=args.exclude,
        max_documents=args.max_documents,
    )
    if not input_paths:
        raise RuntimeError(
            f"No input files matched filters. Provide --input or add supported files to {input_dir.resolve()}"
        )

    service = RAGIngestionService(
        storage_dir=scoped_graph_dir,
        cache_dir=scoped_cache_dir,
        workers=args.workers,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
        embedding_model=args.embedding_model,
        storage_backend=args.backend,
        strict_backend=args.backend_strict,
        web_backend_url=settings.vector_backend_url,
        web_backend_api_key=settings.vector_backend_api_key,
        web_backend_timeout_sec=settings.vector_backend_timeout_sec,
    )

    print("Step 1 setup loaded.")
    print(f"Account id: {account_id}")
    print(f"Model: {settings.deepseek_model}")
    print(f"Graph storage dir: {scoped_graph_dir}")
    print(f"Discovered documents: {len(discovered_paths)}")
    print(f"Selected documents: {len(input_paths)}")
    if args.include:
        print(f"Include filters: {args.include}")
    if args.exclude:
        print(f"Exclude filters: {args.exclude}")
    if args.max_documents is not None:
        print(f"Max documents: {args.max_documents}")
    print(f"Documents queued: {len(input_paths)}")
    if args.backend != service.effective_backend:
        print(
            f"[WARN] Requested backend '{args.backend}' not available; "
            f"using '{service.effective_backend}'."
        )
    if args.dry_run:
        print("Dry-run enabled. No extraction, ingestion, or QA executed.")
        for path in input_paths:
            print(f"[PLAN] {path}")
        return

    output_base = raw_output_arg
    output_base.parent.mkdir(parents=True, exist_ok=True)
    ingestions = _ingest_documents(
        input_paths=input_paths,
        output_base=output_base,
        service=service,
        force_reingest=args.force_reingest,
        purge_missing=args.purge_missing,
    )

    print("Step 2 extraction complete.")
    print("Step 3 ingestion complete.")

    if args.run_qa:
        effective = _effective_qa_settings(args)
        corpus_hash = _compute_corpus_hash(
            cache_dir=scoped_cache_dir,
            input_paths=input_paths,
        )
        qa_output_path = qa_output_arg
        qa_signature = _build_qa_signature(
            model=settings.deepseek_model,
            effective_settings=effective,
            schema_retry_limit=args.schema_retry_limit,
            backend_requested=args.backend,
            backend_effective=service.effective_backend,
            retrieval_mode=args.retrieval_mode,
            retrieval_top_k=args.retrieval_top_k,
            retrieval_alpha=args.retrieval_alpha,
        )
        if _should_skip_qa(
            changed_only_qa=args.changed_only_qa,
            force_qa=args.force_qa,
            qa_output_path=qa_output_path,
            corpus_hash=corpus_hash,
            qa_signature=qa_signature,
        ):
            print("Steps 6/7/8 skipped.")
            print("Reason: changed-only-qa enabled and corpus/settings unchanged.")
            print(f"Existing output: {qa_output_path}")
            return
        _run_qa_pipeline(
            args=args,
            api_key=settings.deepseek_api_key,
            model=settings.deepseek_model,
            base_url=settings.deepseek_base_url,
            rows=service.load_rows_for_qa(),
            corpus_hash=corpus_hash,
            qa_signature=qa_signature,
            effective_settings=effective,
            qa_output_path=qa_output_path,
        )


def _resolve_inputs(*, single_input: str | None, input_dir: Path) -> list[Path]:
    if single_input:
        return [Path(single_input).expanduser().resolve()]
    input_dir = input_dir.expanduser().resolve()
    if not input_dir.exists():
        input_dir.mkdir(parents=True, exist_ok=True)
        return []
    files = [
        p
        for p in input_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_INPUT_EXTENSIONS
    ]
    return sorted(p.resolve() for p in files)


def _filter_inputs(
    *,
    discovered_paths: list[Path],
    input_dir: Path,
    include_patterns: list[str],
    exclude_patterns: list[str],
    max_documents: int | None,
) -> list[Path]:
    include = include_patterns or ["*"]
    selected: list[Path] = []
    for path in discovered_paths:
        key = _path_match_key(path=path, input_dir=input_dir)
        if not any(fnmatch.fnmatchcase(key, pattern) for pattern in include):
            continue
        if any(fnmatch.fnmatchcase(key, pattern) for pattern in exclude_patterns):
            continue
        selected.append(path)
    selected = sorted(selected)
    if max_documents is not None:
        selected = selected[: max(1, max_documents)]
    return selected


def _path_match_key(*, path: Path, input_dir: Path) -> str:
    try:
        return path.resolve().relative_to(input_dir).as_posix()
    except ValueError:
        return path.name


def _raw_output_for(*, path: Path, output_arg: Path) -> Path:
    # When output target points to a single text file, fan out per-document
    # files under sibling raw_text/ to avoid overwrite in folder mode.
    if output_arg.suffix.lower() == ".txt":
        safe_name = _safe_output_stem(path)
        return output_arg.parent / "raw_text" / f"{safe_name}.txt"
    return output_arg


def _safe_output_stem(path: Path) -> str:
    # Include the nearest parent folder to avoid collisions between files with the same name.
    parts = path.with_suffix("").parts[-2:]
    return "__".join(part.replace(" ", "_") for part in parts)


def _ingest_documents(
    *,
    input_paths: list[Path],
    output_base: Path,
    service: RAGIngestionService,
    force_reingest: bool,
    purge_missing: bool,
) -> list[IngestionResult]:
    ingestions: list[IngestionResult] = []
    errors: list[tuple[Path, str]] = []
    for path in input_paths:
        try:
            raw_text = extract_text_from_path(path)
            raw_output = _raw_output_for(path=path, output_arg=output_base)
            raw_output.parent.mkdir(parents=True, exist_ok=True)
            raw_output.write_text(raw_text, encoding="utf-8")
            ingestion = service.ingest(
                input_path=path,
                raw_text=raw_text,
                force_reingest=force_reingest,
                purge_missing=purge_missing,
            )
            ingestions.append(ingestion)
            _print_document_result(path=path, raw_output=raw_output, raw_text=raw_text, ingestion=ingestion)
        except Exception as exc:
            errors.append((path, str(exc)))
            print(f"[DOC] ERROR processing {path.resolve()}: {exc}")
    if errors:
        print(f"\n[SUMMARY] {len(errors)} file(s) failed out of {len(input_paths)} total.")
    return ingestions


def _print_document_result(
    *,
    path: Path,
    raw_output: Path,
    raw_text: str,
    ingestion: IngestionResult,
) -> None:
    print(f"[DOC] {path.resolve()}")
    print(f"  Raw text: {raw_output}")
    print(f"  Characters: {len(raw_text)}")
    print(f"  Ingestion skipped: {ingestion.skipped}")
    print(f"  Reason: {ingestion.reason}")
    print(f"  Chunks: {ingestion.chunks}")
    print(f"  Vectors written: {ingestion.vectors_written}")
    print(f"  Backend used: {ingestion.backend_used}")


def _run_qa_pipeline(
    *,
    args: argparse.Namespace,
    api_key: str,
    model: str,
    base_url: str | None,
    rows: list[dict[str, object]],
    corpus_hash: str,
    qa_signature: dict[str, object],
    effective_settings: dict[str, int],
    qa_output_path: Path,
) -> None:
    pipeline = CurriculumQAPipeline(
        api_key=api_key,
        model=model,
        base_url=base_url,
        max_global_context_chunks=effective_settings["max_global_context_chunks"],
        max_topic_context_chunks=effective_settings["max_topic_context_chunks"],
        qa_per_topic=effective_settings["qa_per_topic"],
        qa_concurrency=effective_settings["qa_concurrency"],
        schema_retry_limit=args.schema_retry_limit,
        max_topics=effective_settings["max_topics"],
        retrieval_mode=args.retrieval_mode,
        retrieval_top_k=args.retrieval_top_k,
        retrieval_alpha=args.retrieval_alpha,
        embedding_model=args.embedding_model,
    )
    merged = asyncio.run(
        pipeline.run(
            rows=rows,
            output_path=qa_output_path,
            metadata={
                "corpus_hash": corpus_hash,
                "qa_signature": qa_signature,
            },
        )
    )
    print("Steps 6/7/8 complete.")
    print(f"Cheap mode: {args.cheap_mode}")
    print(f"Max topics: {effective_settings['max_topics']}")
    print(f"QA per topic: {effective_settings['qa_per_topic']}")
    print(f"QA concurrency: {effective_settings['qa_concurrency']}")
    print(f"Retrieval mode: {args.retrieval_mode}")
    print(f"Retrieval top-k: {args.retrieval_top_k}")
    if args.retrieval_mode == "hybrid":
        print(f"Retrieval alpha: {args.retrieval_alpha}")
    print(f"Max global context chunks: {effective_settings['max_global_context_chunks']}")
    print(f"Max topic context chunks: {effective_settings['max_topic_context_chunks']}")
    print(f"Topics generated: {len(merged.curriculum.topics)}")
    print(f"QA sets generated: {len(merged.qa_sets)}")
    token_usage = (merged.metadata or {}).get("token_usage", {})
    if token_usage:
        print(f"API calls: {token_usage.get('api_calls', 0)}")
        print(f"Prompt tokens (actual): {token_usage.get('prompt_tokens', 0)}")
        print(f"Completion tokens (actual): {token_usage.get('completion_tokens', 0)}")
        print(f"Total tokens (actual): {token_usage.get('total_tokens', 0)}")
        print(f"Prompt cache hit tokens: {token_usage.get('prompt_cache_hit_tokens', 0)}")
        print(f"Prompt cache miss tokens: {token_usage.get('prompt_cache_miss_tokens', 0)}")
        print(f"Prompt tokens (estimated): {token_usage.get('estimated_prompt_tokens', 0)}")
        print(f"Completion tokens (estimated): {token_usage.get('estimated_completion_tokens', 0)}")
    prefix_fingerprint = (merged.metadata or {}).get("context_prefix_fingerprint", "")
    if prefix_fingerprint:
        print(f"Context prefix fingerprint: {prefix_fingerprint}")
    print(f"Merged output: {qa_output_path}")


def _effective_qa_settings(args: argparse.Namespace) -> dict[str, int]:
    base = CHEAP_QA_DEFAULTS if args.cheap_mode else NORMAL_QA_DEFAULTS
    resolved = base.copy()
    if args.qa_concurrency is not None:
        resolved["qa_concurrency"] = max(1, args.qa_concurrency)
    if args.qa_per_topic is not None:
        resolved["qa_per_topic"] = max(1, args.qa_per_topic)
    if args.max_global_context_chunks is not None:
        resolved["max_global_context_chunks"] = max(1, args.max_global_context_chunks)
    if args.max_topic_context_chunks is not None:
        resolved["max_topic_context_chunks"] = max(1, args.max_topic_context_chunks)
    if args.max_topics is not None:
        resolved["max_topics"] = max(1, args.max_topics)
    return resolved


def _compute_corpus_hash(*, cache_dir: Path, input_paths: list[Path]) -> str:
    manifest_path = cache_dir / "ingestion_manifest.json"
    if not manifest_path.exists():
        return ""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = manifest.get("files", {})
    parts: list[str] = []
    for path in sorted(str(p.resolve()) for p in input_paths):
        entry = files.get(path)
        if entry:
            parts.append(f"{path}:{entry.get('sha256', '')}")
    digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
    return digest


def _build_qa_signature(
    *,
    model: str,
    effective_settings: dict[str, int],
    schema_retry_limit: int,
    backend_requested: str,
    backend_effective: str,
    retrieval_mode: str,
    retrieval_top_k: int,
    retrieval_alpha: float,
) -> dict[str, object]:
    return {
        "model": model,
        "schema_retry_limit": schema_retry_limit,
        "backend": backend_effective,
        "backend_requested": backend_requested,
        "backend_effective": backend_effective,
        "retrieval_mode": retrieval_mode,
        "retrieval_top_k": retrieval_top_k,
        "retrieval_alpha": retrieval_alpha,
        "qa_settings": effective_settings,
    }


def _should_skip_qa(
    *,
    changed_only_qa: bool,
    force_qa: bool,
    qa_output_path: Path,
    corpus_hash: str,
    qa_signature: dict[str, object],
) -> bool:
    if not changed_only_qa or force_qa or not qa_output_path.exists():
        return False
    try:
        payload = json.loads(qa_output_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    metadata = payload.get("metadata", {})
    return (
        metadata.get("corpus_hash") == corpus_hash
        and metadata.get("qa_signature") == qa_signature
    )


if __name__ == "__main__":
    run()
