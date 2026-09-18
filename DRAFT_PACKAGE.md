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
│   ├── cli.py          # typer CLI (ingest/parse/index/verify/query/eval/api)
│   ├── embed.py        # Embedder protocol, local lexical + HTTP backends
│   ├── eval.py         # recall@k, precision@k, MRR and the baseline gate
│   ├── hashing.py      # SHA-256 helpers
│   ├── indexer.py      # SQLite index build, schema, readonly connect
│   ├── ingest.py       # corpus walk, hash, dedupe, quarantine
│   ├── parse.py        # text/PDF extraction, chunking over the manifest
│   ├── pathsafety.py   # symlink and traversal guards
│   ├── query.py        # read-only search/fetch/context/stats
│   ├── schemas.py      # Pydantic response models
│   ├── search.py       # term (BM25), vector and hybrid ranking
│   ├── vectors.py      # sqlite-vec storage and KNN search
│   └── verify.py       # the provenance gate
├── tests/
│   ├── conftest.py
│   ├── test_api.py
│   ├── test_chunking.py
│   ├── test_index.py
│   ├── test_ingest.py
│   ├── test_query.py
│   ├── test_retrieval.py
│   ├── test_embed.py
│   └── test_verify.py
└── tools/make_corpus.py  # seeded corpus generator (committed; output is not)
```

Generated artifacts (`corpus/`, `manifest.json`, `chunks.jsonl`, `index.sqlite`,
`quarantine/`) are gitignored. Total source: 17 modules, 9 test files, 1 tool,
about 3,700 lines.

A note on the README copy below: the plain asset count above is current for the
whole package, but the README text quoted in the next section is the committed
one, and the committed README now also documents the embedding layer, the three
search modes, the sqlite-vec vector table, and the retrieval eval command.

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
| 12 | Tests | `pytest -q` | 53 passed, 2 upstream warnings |
| 13 | Lint | `ruff check .`, `ruff format --check .` | clean, 21 files formatted |
| 14 | Types | `mypy` | clean, 14 source files |
| 15 | HTTP service | `docpipe api` + curl `/health`, `/search`, `/stats` | real JSON responses |
| 16 | sqlite-vec load | `sqlite_vec.load(conn)` + `vec_version()` | v0.1.9, 22 rows in `chunk_vectors` |
| 17 | Eval metrics | `docpipe eval --k 5 --mode hybrid` | recall 1.0, precision 0.2, MRR 0.910714 |
| 18 | Eval gate pass | `docpipe eval --check` | exit 0, "gate PASS" |
| 19 | Eval gate fail | `--baseline` with an inflated mrr | exit 1, "gate FAIL - mrr: 0.910714 < baseline 0.99" |

## Verified working

- Full ingest -> parse -> index -> verify pipeline on the seeded corpus.
- The gate fails loudly: exit 1 with per-document mismatch detail on a tampered
  hash, and exit 0 on a clean index.
- Deterministic rebuild, proven by matching SQLite dump hashes.
- Read-only query layer (search, fetch, list, context, stats) and the FastAPI
  service, both serving real data.
- sqlite-vec 0.1.9 loads for real (verified with `vec_version()` returning
  v0.1.9 and 22 rows present in `chunk_vectors`). It is used by the vector and
  hybrid search modes, not just declared.
- Three search modes (term BM25, vector, hybrid) with the hybrid re-ranking
  formula alpha * vec_norm + (1 - alpha) * term_norm over a min-max normalised
  candidate pool.
- Retrieval eval harness runs 14 labelled queries at k=5: recall@5 1.0,
  precision@5 0.2, MRR 0.910714. The `--check` flag gates against the committed
  `baseline_metrics.json` and exits 1 on regression; the fail path was also
  tested by hand with an inflated baseline.
- 53 passing tests; ruff and mypy clean.
- PDF extraction via pymupdf (the demo corpus includes one PDF).
- Symlink/traversal quarantine and UTF-8 validation (covered by tests).

## Not working / known gaps

- The eval scores are high partly by construction, and this is the big one.
  The corpus and the labelled query set come from the same seeded generator,
  so every query shares vocabulary and phrasing with the document it targets.
  The numbers (recall@5 1.0, precision@5 0.2, MRR 0.910714, all real and
  reproducible) prove the harness, the ranking pipeline and the regression
  gate work end to end. They do not demonstrate retrieval quality on real
  documents or real user questions. The honest next step is an independently
  written query set, ideally human-written, against a real public corpus.
- The local deterministic embedder is lexical, not semantic. It hashes word
  unigrams and character trigrams into a fixed-width projection, so it will
  miss paraphrases a neural embedding would catch. A query that describes a
  concept without the document's exact vocabulary will not rank well on the
  vector side. The HTTP embedder exists as the semantic option but no
  committed number was produced with it.
- precision@5 is structurally capped at 0.2 because each query has exactly one
  expected document. The metric is not wrong, but it carries almost no ranking
  information in this setup; recall@5 and MRR are the informative ones.
- The hybrid alpha is hardcoded to 0.5 with no CLI flag or tuning story. The
  min-max normalisation over a small candidate pool also makes the merged
  score unstable when the pool is small.
- Vector and hybrid modes require the sqlite-vec extension at query time and
  raise rather than degrading to term search. That is deliberate, but it means
  an index built on a machine without the extension is unusable for those
  modes.
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
> provenance verification gate. Ingest, chunk and index a corpus into SQLite
> with BM25, vector (sqlite-vec) and hybrid search, plus a labelled-query
> retrieval evaluation harness with a regression gate. Fails loudly instead of
> silently.

## Proposed topics

`document-indexing`, `sqlite`, `retrieval`, `retrieval-evaluation`,
`vector-search`, `embeddings`, `bm25`, `sqlite-vec`, `provenance`,
`data-integrity`, `deterministic-build`, `python`, `fastapi`, `cli`, `search`

## What a skeptical reviewer would attack

- "The eval numbers are too good to be true." They are, partly. The corpus and
  the labelled query set come from the same seeded generator, so the queries
  share vocabulary and phrasing with the documents they target. Recall@5 1.0
  and MRR 0.910714 prove the harness, the ranking pipeline and the regression
  gate work end to end; they do not prove retrieval quality on real documents
  or real user queries. An independent, human-written query set against a real
  public corpus is the honest next step, and nothing in the current repo can
  substitute for it.
- "This is hashing plus grep." Now a weaker attack, but still fair in spirit.
  The term side is BM25, there is sqlite-vec with 384-dim chunk embeddings, and
  hybrid re-ranks a normalised candidate pool. But the embedder is a lexical
  feature hasher, not a neural model, so the vector side captures surface
  overlap, not meaning. A reviewer with real IR experience will call the
  ranking setup introductory-level, and there is no result to show otherwise
  without an independent benchmark.
- The determinism claim is strong but narrow. It is proven by hashing the
  SQLite `.dump`, which is reproducible for the tested SQLite build but is not
  a spec-level guarantee across SQLite versions or platforms. The vector table
  adds a second surface to this: sqlite-vec behaviour would need to hold too.
- The corpus is 16 synthetic documents, 14 labelled queries. There is no
  benchmark, no large-corpus test, no performance number, so nothing
  demonstrates it scales past a toy.
- precision@k is structurally capped at 0.2 here because each query has one
  expected document. Presenting it as a headline metric without that context
  would mislead a reader; the draft keeps it but explains it.
- Verify and serve are decoupled. The API will happily serve a corrupt index
  because verification is a separate manual command, not a startup guard.
- `--exemptions` is a hole in the gate. It prints loudly, but a reviewer will
  ask why the "no silent path" guarantee has an opt-out at all.
- The completeness check is one-directional. It confirms every manifest hash is
  indexed, but not that every file on disk is in the manifest. A file added
  after ingest is silently ignored, which is exactly the class of silent
  failure the project claims to solve.
- Single contributor, alpha status, no published releases. The honest framing
  is "a focused demonstration of deterministic indexing, vector retrieval and
  an eval harness, with a loud provenance gate", not a production search
  engine.
