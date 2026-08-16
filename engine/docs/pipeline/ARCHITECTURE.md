# Engine Architecture

This document describes the technical architecture of the engine backend.

## Purpose

The pipeline ingests `.pdf` / `.txt` / `.md` documents, builds a local retrieval index, and optionally generates:

- a curriculum
- per-topic QA sets
- merged JSON output

The implementation is CLI-first and optimized for incremental reruns.

## Module Map

- `main.py`: orchestration and CLI entrypoint.
- `config.py`: environment and runtime settings loading.
- `constants.py`: defaults and shared configuration values.
- `extract_text.py`: file parsing and normalization.
- `rag_engine.py`: chunking, embeddings, ingestion cache, storage adapter.
- `models.py`: Pydantic data contracts for curriculum/QA output.
- `qa_pipeline.py`: DeepSeek synthesis workflow with schema retries.

## End-to-End Flow

```text
CLI args
  -> discover files (recursive)
  -> include/exclude filter
  -> optional dry-run
  -> text extraction
  -> ingestion (hash cache aware)
  -> optional QA generation
      -> curriculum synthesis
      -> topic extraction
      -> concurrent topic QA synthesis
  -> merged JSON output (+ metadata)
```

## Stage Details

### Stage 0: Input Discovery

- Source:
  - `--input` for single file
  - `--input-dir` for recursive discovery
- Filters:
  - `--include` (glob, repeatable)
  - `--exclude` (glob, repeatable)
  - `--max-documents`
- Safe preview:
  - `--dry-run` prints selected paths and exits.

### Stage 1: Config Load

`config.py` loads:

- required: `DEEPSEEK_API_KEY`
- optional: `DEEPSEEK_MODEL`, `PIPELINE_BASE_DIR`, `GRAPH_STORAGE_DIR`

### Stage 2: Text Extraction

`extract_text.py` supports:

- `.pdf` via `pypdf`
- `.txt` and `.md` via UTF-8 reads

Normalization:

- trim line whitespace
- remove empty lines

### Stage 3: Ingestion + Cache

`rag_engine.py`:

1. chunks normalized text
2. embeds chunks in parallel workers
3. writes vectors to the effective backend (`jsonl` or `lightrag`; `web` is reserved)
4. updates `ingestion_manifest.json`

#### Embedder Initialization and Fallback

The pipeline attempts to load `sentence-transformers` (model: `all-MiniLM-L6-v2`) for real semantic embeddings. If the import or model initialization fails, the system falls back to a deterministic pseudo-embedder that generates vectors via repeated SHA-256 hashing.

**Fallback triggers:**
- `sentence-transformers` not installed
- Model download fails (no internet, firewall, Hugging Face timeout)
- Any initialization exception

**Fallback behavior:**
- A warning is logged: `"SentenceTransformer loading failed (...). Falling back to mock/deterministic fallback embedder. Semantic similarity features will be disabled."`
- The fallback embedder produces deterministic vectors (same input always yields same output), but the vector coordinates are not semantically meaningful
- Semantic search and hybrid retrieval will return results based on pseudo-random vector proximity, not actual meaning
- The pipeline continues normally — it does not crash or abort

**Impact:** When the fallback is active, curriculum/QA generation still works (it uses LLM calls, not embeddings), but retrieval quality for topic context selection degrades to keyword-only mode since vector similarity rankings are meaningless.

Cache key:

- per-file SHA-256 hash of normalized text
- chunk SHA-256 list (`chunk_hashes`)
- effective backend identity

Behavior:

- unchanged hash + matching chunk hashes + backend row validation -> skip ingestion
- changed hash -> replace file rows in index

### Stage 4/5: Curriculum Synthesis + Validation

`qa_pipeline.py`:

- builds global context from indexed rows
- calls DeepSeek for structured curriculum JSON
- validates with Pydantic (`Curriculum`)
- retries invalid JSON up to `--schema-retry-limit`

### Stage 6/7: Topic Extraction + Concurrent QA

- extract topics from validated curriculum
- retrieve topic context using configured mode:
  - `keyword`: lexical overlap ranking
  - `vector`: embedding cosine similarity ranking
  - `hybrid`: weighted rank merge of keyword + vector
- generate QA per topic concurrently (`asyncio` + semaphore)
- print runtime progress indicators:
  - `[MODEL] ...` for generation attempts
  - `[PROGRESS] ...` for topic completion counters
- validate each QA payload (`TopicQASet`)
- retry invalid topic JSON up to `--schema-retry-limit`

Retrieval controls:

- `--retrieval-mode`
- `--retrieval-top-k`
- `--retrieval-alpha` (hybrid only)

### Stage 8: Merge Output

Writes `CurriculumQAOutput`:

- `curriculum`
- `qa_sets`
- `metadata` (optional)

Metadata fields currently used for QA cache decisions:

- `corpus_hash`
- `qa_signature`

Additional runtime metadata:

- `token_usage`

`qa_signature` includes:

- model
- schema retry limit
- backend requested + backend effective
- retrieval settings
- effective QA settings

## QA Skip Cache Logic

When `--changed-only-qa` is enabled:

1. Compute `corpus_hash` from selected file hashes in ingestion manifest.
2. Build `qa_signature` from:
   - model
   - schema retry limit
   - backend
   - effective QA settings
3. Read previous output metadata.
4. Skip QA when both values match.

Override:

- `--force-qa` always runs QA even if unchanged.

## Storage Contracts

### Manifest (`engine/cache/ingestion_manifest.json`)

- versioned dictionary
- file path -> hash and ingestion metadata
- ingestion metadata includes:
  - `sha256`
  - `chunk_count`
  - `chunk_hashes`
  - `backend`

### Vector Index

- JSONL rows keyed by `file_path` and `chunk_id`
- row includes `chunk_sha256`, vector payload, and timestamps

### Future Web Backend

`--backend web` is reserved for a future hosted vector store. It is intentionally
not implemented yet. In non-strict mode, requesting `web` falls back to `jsonl`;
in strict mode, the pipeline fails fast with a clear placeholder error.

The existing `VECTOR_BACKEND_*` settings are kept only to preserve the future
configuration shape.

### QA Output

- JSON object matching `CurriculumQAOutput`
- metadata included for skip cache
- metadata includes token accounting:
  - actual tokens when API usage is returned
  - estimated tokens when usage is unavailable

## Concurrency Model

- Embeddings: thread pool (`--workers`)
- Topic QA: async semaphore (`--qa-concurrency`)

Both are bounded and user-tunable.

## Failure Strategy

- Missing API key -> fail fast
- Unsupported extension -> explicit error
- Invalid model JSON -> bounded retries
- Missing vector rows before QA -> explicit error
- Corrupt JSONL lines -> skipped during read

## Runtime vs Source

Runtime artifacts are intentionally untracked:

- `engine/cache/`
- `engine/output/`
- `data/graph_db/`

Behavior:

- these folders are created automatically when you run the CLI
- they can be deleted safely between runs
- they are ignored by git, so they do not appear in commits unless force-added

Source-controlled assets should be code, docs, and input documents only.

## Extension Points

Most likely future changes:

1. retrieval quality:
   replace keyword context selection with vector similarity ranking
2. token controls:
   add token budget estimates and chunk-level deduplication
3. backend integration:
   replace guarded LightRAG adapter with pinned production API calls
4. output reproducibility:
   persist prompt version and generation parameters in metadata
