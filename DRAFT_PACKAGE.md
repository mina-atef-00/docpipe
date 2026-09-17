# Draft package review

This is the working draft of the `docpipe` repository as it would be packaged for
GitHub. Nothing here has been published. The commit history, README, evidence
file and CI workflow are all in place; the notes below flag what is real and
what a reviewer would challenge.

## File tree (committed)

```
.
├── .github/workflows/ci.yml
├── .gitignore
├── LICENSE
├── pyproject.toml
├── README.md
├── EVIDENCE.md
├── src/docpipe/
│   ├── __init__.py
│   ├── __main__.py
│   ├── api.py          # FastAPI service
│   ├── chunking.py     # deterministic chunking and tokenization
│   ├── cli.py          # typer CLI (ingest/parse/index/verify/query/api)
│   ├── hashing.py      # SHA-256 helpers
│   ├── indexer.py      # SQLite index build, schema, readonly connect
│   ├── ingest.py       # corpus walk, hash, dedupe, quarantine
│   ├── parse.py        # text/PDF extraction, chunking over the manifest
│   ├── pathsafety.py   # symlink and traversal guards
│   ├── query.py        # read-only search/fetch/context/stats
│   ├── schemas.py      # Pydantic response models
│   └── verify.py       # the provenance gate
├── tests/
│   ├── conftest.py
│   ├── test_api.py
│   ├── test_chunking.py
│   ├── test_index.py
│   ├── test_ingest.py
│   ├── test_query.py
│   └── test_verify.py
└── tools/make_corpus.py  # seeded corpus generator (committed; output is not)
```

Generated artifacts (`corpus/`, `manifest.json`, `chunks.jsonl`, `index.sqlite`,
`quarantine/`) are gitignored. Total source: 13 modules, 7 test files, 1 tool,
about 2,400 lines.

## The README

The full README as it will ship is reproduced below.

---

# docpipe

A deterministic document ingestion and retrieval pipeline with a provenance gate.

docpipe walks a corpus of text, markdown and PDF files, hashes every file,
chunks it, and builds a SQLite index. The part that matters is the last step:
a `verify` command that checks every row in the index against a real file on
disk and its content hash. If any row cannot be traced, `verify` prints the
exact failure and exits non-zero. There is no silent path.

### Why this exists

Most indexing pipelines fail quietly. A build script runs, prints a summary,
and exits 0 even when half the documents were never parsed or a file changed on
disk after it was indexed. You find out weeks later, when a search returns
stale or missing results, and by then you cannot tell what was indexed from
what was actually there.

docpipe is built around the opposite assumption: a completed index is not proof
of anything until it has been checked. The `verify` gate recomputes the SHA-256
of every source file and compares it to what the index recorded. It also checks
referential integrity inside the database, so an index with orphaned rows or a
mismatched run record fails the same way a tampered hash does.

False completion is the market's number one failure mode. An index that reports
success while silently dropping data is worse than an index that refuses to
build. docpipe makes the refusal explicit.

### What it does

1. `ingest` walks the corpus, hashes each file, dedupes byte-identical repeats,
   and quarantines anything unsafe (symlinks escaping the root, invalid UTF-8,
   unreadable files, unsupported types).
2. `parse` extracts text and splits it into deterministic chunks. PDFs go
   through pymupdf when it is installed.
3. `index` builds a SQLite database. Every identifier is content derived and
   every insert stream is sorted, so building the index twice from the same
   inputs yields a byte-identical file.
4. `verify` checks the index against the corpus and the ingest manifest, then
   exits 0 or non-zero.

There is also a read-only `query` subcommand (search, document fetch, listing,
chunk context, stats) and a small FastAPI service.

### Install

```
git clone <this repo>
cd docpipe
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Requires Python 3.10 or newer.

### Run it end to end

```
python tools/make_corpus.py --root corpus --seed 20260918
docpipe ingest corpus --out manifest.json --quarantine-dir quarantine
docpipe parse --manifest manifest.json --out chunks.jsonl --corpus corpus
docpipe index --manifest manifest.json --chunks chunks.jsonl --out index.sqlite
docpipe verify corpus --index index.sqlite --manifest manifest.json
```

Real output from a clean run:

```
$ docpipe ingest corpus --out manifest.json --quarantine-dir quarantine
ingest: 17 files (16 unique, 1 duplicates), 0 quarantined, 0 skipped

$ docpipe parse --manifest manifest.json --out chunks.jsonl --corpus corpus
parse: 16 documents, 22 chunks, 1 pdf, 0 pdf-skipped, 0 empty -> chunks.jsonl

$ docpipe index --manifest manifest.json --chunks chunks.jsonl --out index.sqlite
index: 16 documents, 22 chunks, 1746 terms -> index.sqlite

$ docpipe verify corpus --index index.sqlite --manifest manifest.json
verify: 16 documents, 22 chunks, 1746 terms
verify: OK - every index row traces to a source file and its hash
```

Tampered index:

```
$ docpipe verify corpus --index index.sqlite --manifest manifest.json
verify: FAILED - 2 provenance failure(s):
  - document README.md: doc_id 6e702aec... != stored sha256 0
  - document README.md: hash mismatch (stored 0, disk 6e702aec...)
```

The full transcript for every step is in `EVIDENCE.md`.

### Index schema

```
documents (doc_id PK, rel_path, sha256, size, kind)
chunks    (chunk_id PK, doc_id FK, chunk_index, text)
terms     (term, chunk_id FK, position)   -- PK (term, chunk_id, position)
runs      (run_id PK, manifest_sha256, chunks_sha256, doc_count, chunk_count, term_count)
meta      (key PK, value)
```

### License

MIT. See `LICENSE`.

---

## Verbatim evidence table

Every item below was run and its output is pasted in `EVIDENCE.md`.

| # | Claim | Command | Result |
|---|-------|---------|--------|
| 1 | CLI version and verify usage | `docpipe --version`, `docpipe verify --help` | 0.1.0; verify takes a `corpus` positional |
| 2 | Seeded corpus generation | `python tools/make_corpus.py --root corpus --seed 20260918` | 17 files on disk, 16 unique, 1 pdf |
| 3 | Ingest | `docpipe ingest corpus ...` | 17 files, 16 unique, 1 duplicate, 0 quarantined |
| 4 | Parse | `docpipe parse ...` | 16 documents, 22 chunks, 1 pdf |
| 5 | Index build | `docpipe index ...` | 16 documents, 22 chunks, 1746 terms |
| 6 | Verify, good index | `docpipe verify corpus ...` | exit 0, "OK" |
| 7 | Determinism | build twice, hash `.dump` | both `ba736496...ae57` |
| 8 | Verify, corrupted index | tamper `sha256`, then `verify` | exit 1, 2 failures listed |
| 9 | Query: search | `docpipe query search widget` | 5 hits with snippets and positions |
| 10 | Query: doc fetch | `docpipe query doc <sha256>` | metadata plus all chunks |
| 11 | Query: list / context / stats | `query list`, `query context`, `query stats` | 8 docs, chunk window, run_id |
| 12 | Tests | `pytest -q` | 35 passed, 2 upstream warnings |
| 13 | Lint | `ruff check .`, `ruff format --check .` | clean, 21 files formatted |
| 14 | Types | `mypy` | clean, 14 source files |
| 15 | HTTP service | `docpipe api` + curl `/health`, `/search`, `/stats` | real JSON responses |

## Verified working

- Full ingest -> parse -> index -> verify pipeline on the seeded corpus.
- The gate fails loudly: exit 1 with per-document mismatch detail on a tampered
  hash, and exit 0 on a clean index.
- Deterministic rebuild, proven by matching SQLite dump hashes.
- Read-only query layer (search, fetch, list, context, stats) and the FastAPI
  service, both serving real data.
- 35 passing tests; ruff and mypy clean.
- PDF extraction via pymupdf (the demo corpus includes one PDF).
- Symlink/traversal quarantine and UTF-8 validation (covered by tests).

## Not working / known gaps

- Vector search is not implemented. The allowlist mentions sqlite-vec, but the
  index is a plain term table with positional postings, no embeddings, no
  ranking beyond term positions. This is the largest missing capability for a
  retrieval project.
- The generator's summary prints "16 files" while 17 files land on disk (16
  unique hashes plus one duplicate pair). Cosmetic, but a reviewer will notice.
- Two pytest warnings come from upstream Starlette deprecations, not docpipe.
- `verify` treats the manifest as the source of truth but does not detect files
  added to the corpus after ingest. Those new files are simply absent from the
  manifest and the index, so a fresh file that should have been indexed is not
  flagged.
- The FastAPI service serves from the index without re-running `verify`. A
  tampered index still answers queries until someone runs the gate by hand.
- No incremental indexing; every change is a full rebuild.
- CI is written but has never run on GitHub Actions (nothing has been pushed).

## Proposed GitHub description

> docpipe: a deterministic document ingestion and retrieval pipeline with a
> provenance verification gate. Ingest, chunk and index a corpus into SQLite,
> then verify every row traces to a real file and its content hash. Fails
> loudly instead of silently.

## Proposed topics

`document-indexing`, `sqlite`, `retrieval`, `provenance`, `data-integrity`,
`deterministic-build`, `python`, `fastapi`, `cli`, `search`

## What a skeptical reviewer would attack

- "This is hashing plus grep." Fair. The retrieval side is a basic inverted
  term index with no ranking, no fuzzy match and no vector search, so the value
  proposition rests almost entirely on the verification gate. The gate is real,
  but a reviewer comparing this to a proper search system will call the query
  layer thin.
- The determinism claim is strong but narrow. It is proven by hashing the
  SQLite `.dump`, which is reproducible for the tested SQLite build but is not
  a spec-level guarantee across SQLite versions or platforms.
- The corpus is 16 synthetic documents. There is no benchmark, no large-corpus
  test, no performance number, so nothing demonstrates it scales past a toy.
- The completeness check is one-directional. It confirms every manifest hash is
  indexed, but not that every file on disk is in the manifest. A file added
  after ingest is silently ignored, which is exactly the class of silent
  failure the project claims to solve.
- `--exemptions` is a hole in the gate. It prints loudly, but a reviewer will
  ask why the "no silent path" guarantee has an opt-out at all.
- Verify and serve are decoupled. The API will happily serve a corrupt index
  because verification is a separate manual command, not a startup guard.
- Single contributor, alpha status, no published releases. The honest framing
  is "a focused demonstration of deterministic indexing with a loud provenance
  gate", not a general search engine.
