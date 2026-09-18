# EVIDENCE.md

The technical spec and verification record for docpipe. Every claim here
comes from a real run. The README stays user-facing; this file holds the
receipts.

The run behind the current numbers is the 2026-09-18 re-run. 53 tests,
lint and type checks were all clean on that date.

## Full pipeline transcript

Clean build of the demo corpus, first run of the session (2026-09-18):

```
$ python tools/make_corpus.py --root corpus --seed 20260918

$ docpipe ingest corpus --out manifest.json --quarantine-dir quarantine
ingest: 17 files (16 unique, 1 duplicates), 0 quarantined, 0 skipped
ingest: manifest -> manifest.json

$ docpipe parse --manifest manifest.json --out chunks.jsonl --corpus corpus
parse: 16 documents, 22 chunks, 1 pdf, 0 pdf-skipped, 0 empty -> chunks.jsonl

$ docpipe index --manifest manifest.json --chunks chunks.jsonl --out index.sqlite
index: 16 documents, 22 chunks, 1746 terms, 22 vectors (hashed-ngram) -> index.sqlite

$ docpipe verify corpus --index index.sqlite --manifest manifest.json
verify: 16 documents, 22 chunks, 1746 terms
verify: OK - every index row traces to a source file and its hash
```

The demo corpus is a fictional widget platform (API references, specs,
changelogs, runbooks). The generator in `tools/make_corpus.py` is
committed; the files it emits are not (they are in `.gitignore`).

When the index is tampered with after the fact:

```
$ docpipe verify corpus --index index.sqlite --manifest manifest.json
verify: 16 documents, 22 chunks, 1746 terms
verify: FAILED - 2 provenance failure(s):
  - document README.md: doc_id 6e702aec... != stored sha256 000...000
  - document README.md: hash mismatch (stored 000...000, disk 6e702aec...)
```

Missing inputs exit 2; provenance failures exit 1; only a fully traced
index exits 0.

## What verify checks

`verify` requires three inputs: the corpus directory (positional), the
index (`--index`, default `index.sqlite`) and the manifest (`--manifest`,
default `manifest.json`). It checks:

- referential integrity inside the index (no orphaned chunks or terms, no
  duplicate rel_paths)
- forward provenance, meaning every document row maps to a real file whose
  on-disk hash matches the stored one
- completeness, meaning every manifest hash has exactly one canonical
  document row and no phantom rows exist
- the `runs` table is a single row whose recorded counts match the live
  tables

Any failure is printed with the offending rel_path and the exact mismatch.
The `--exemptions` option takes a JSON file of rel_paths allowed to skip
the disk check; exemptions are printed loudly, never hidden.

## Index schema

SQLite, schema version 1:

```
documents (doc_id PK, rel_path, sha256, size, kind)
chunks    (chunk_id PK, doc_id FK, chunk_index, text)
terms     (term, chunk_id FK, position)   -- PK (term, chunk_id, position)
runs      (run_id PK, manifest_sha256, chunks_sha256, doc_count, chunk_count, term_count)
meta      (key PK, value)
```

`doc_id` is the SHA-256 of the source file bytes. `chunk_id` is the SHA-256
of doc_id plus chunk index plus chunk text. `run_id` is the SHA-256 of the
manifest hash plus the chunks hash. Nothing is a sequence number or a
timestamp.

## Determinism

Build the index twice from the same manifest and chunk stream and hash the
SQLite dump of each. The two hashes match. One practical detail, because it
will bite anyone reproducing this: the dump can only be read with the
sqlite-vec extension loaded, since the index contains a vec0 virtual table.
Without it, `iterdump()` fails with `no such module: vec0`. With it loaded,
from the 2026-09-18 re-run:

```
/tmp/docpipe_a.sqlite 86e5ca6412fd76f26ca5e6a0095a795f5aabdece83ef8d9a1822da7228556598
/tmp/docpipe_b.sqlite 86e5ca6412fd76f26ca5e6a0095a795f5aabdece83ef8d9a1822da7228556598
```

Scope of the claim. The matching dump hashes cover the relational layers
of the index: documents, chunks, terms and the run record. The vector
blobs in the vec0 table are kept stable by the build's chunk_id insert
ordering rather than by this hash check, so vector-layer determinism is by
construction, not independently hashed. And one input is not deterministic
at all: regenerating the corpus from seed 20260918 reproduces the 14 text
and markdown files exactly, but `architecture.pdf` hashes differently on
every regeneration because pymupdf embeds metadata at save time. So a
clean-seed rebuild reproduces the term layer and every committed eval and
query number, but not the PDF document's own hash or its chunk's vector.

## Embedding layer

`index` and `query`/`eval` share one `Embedder` protocol: a backend needs a
`name`, a `dim`, and `embed`/`embed_one` methods. Two backends exist:

- `hashed-ngram` (default): a deterministic local lexical vectoriser. Word
  unigrams and character trigrams are feature-hashed through a fixed
  BLAKE2b projection into 384 dimensions, then L2 normalised. It is a
  lexical embedding, not a neural one: similar vectors mean surface word
  and n-gram overlap, not shared meaning. Texts that mean the same thing in
  different words get different vectors. That is the tradeoff that buys a
  build that runs anywhere: no network, no API key, no model download,
  identical vectors on every machine.
- `http`: an OpenAI-compatible `/embeddings` client, selected explicitly
  with `--embedder http --embedding-base-url ... --embedding-model ...` and
  a `--embedding-api-key` (or `DOCPIPE_EMBEDDING_API_KEY`). It never falls
  back to the local backend; a missing key or failed request is a hard
  error.

An index records which backend built its vectors in the `meta` table, and
query time rebuilds that same embedder. All committed numbers in this file
were produced by the `hashed-ngram` local backend.

## Search ranking details

`docpipe query search <term> [--mode term|vector|hybrid]`:

- `term`: BM25 (k1=1.5, b=0.75) over the positional term index. Default.
- `vector`: the query is embedded with the recorded embedder and sqlite-vec
  returns the nearest chunks by cosine. A hit's `score` is `1 - distance`.
- `hybrid`: both run over a candidate pool of the union of the top
  `4 * limit` chunks from each side, re-ranked by

  ```
  hybrid_score(c) = alpha * vec_norm(c) + (1 - alpha) * term_norm(c)
  ```

  with `alpha = 0.5` (fixed in `src/docpipe/search.py`, no CLI flag).
  `vec_norm` and `term_norm` are min-max normalised to `[0, 1]` over the
  pool. A chunk missing from one side gets the lowest score observed on
  that side, so a combined match ranks above a lexical-only one.

### Vector storage

Vectors live in the same `index.sqlite` file, in a sqlite-vec virtual
table:

```sql
CREATE VIRTUAL TABLE chunk_vectors USING vec0(
  embedding float[384] distance_metric=cosine,
  +chunk_id text
)
```

Vectors are inserted in `chunk_id` order so the build stays deterministic.
Before trusting the "sqlite-vec is a real dependency" claim, it was
verified directly on the committed demo index:

```
$ python - <<'EOF'
import sqlite3, sqlite_vec
conn = sqlite3.connect('index.sqlite')
conn.enable_load_extension(True)
sqlite_vec.load(conn)
print("vec_version:", conn.execute('select vec_version()').fetchone()[0])
print("vector rows:", conn.execute('SELECT count(*) FROM chunk_vectors').fetchone())
EOF
vec_version: v0.1.9
vector rows: (22,)

$ sqlite3 index.sqlite "SELECT key, value FROM meta WHERE key LIKE 'embed%';"
embed_dim|384
embedder|hashed-ngram
```

A real vector search at query time:

```
$ docpipe query search "widget authentication" --mode vector --limit 3
search 'widget authentication' [vector]: 3 hit(s)

api/widget_api.md  chunk 0  (doc 9daba2ff6d9e)
  score: 0.4924
  # Widget API reference

## Authentication

All widget endpoi... List widgets

```
GET /widgets?limit=50&cursor=<opaque>
```

runbooks/deploy-runbook.md  chunk 1  (doc 2e4d82d9e308)
  score: 0.4350
  Roll back in the reverse order: queue, then widget, then aut... be reversed by hand; restore the
database snapshot instead.

notes/glossary-copy.txt  chunk 0  (doc c2d812d121f2)
  score: 0.3218
  Glossary

Bearer token: a short lived credential passed in t...n: a queue delivery target with retry and backoff behaviour.
```

If the extension could not be loaded, the operation raises
`VectorUnavailableError` rather than falling back to something else.

## Retrieval eval

`docpipe eval` runs a labelled query set against the index and reports
recall@k, precision@k and mean reciprocal rank. Each labelled query maps to
exactly one expected `rel_doc`; a retrieved chunk is relevant when it comes
from that document. For one relevant document per query and a cut-off k:

- `recall@k` = fraction of queries whose expected document appears in the
  top-k chunks, i.e. hits / N.
- `precision@k` = mean over queries of relevant_retrieved / k, i.e. hits /
  (N * k).
- `mrr` = mean of 1 / rank of the first chunk from the expected document, 0
  when it never appears.

Reproduce the committed numbers:

```
python tools/make_corpus.py --root corpus --seed 20260918
docpipe ingest corpus --out manifest.json --quarantine-dir quarantine
docpipe parse --manifest manifest.json --out chunks.jsonl --corpus corpus
docpipe index --manifest manifest.json --chunks chunks.jsonl --out index.sqlite
docpipe eval --index index.sqlite --queries eval_queries.json --k 5 --mode hybrid
```

Real output of that run (the committed baseline in
`baseline_metrics.json`, produced by the local lexical embedder, hybrid
mode, k=5, 14 labelled queries):

```
eval: 14 queries, mode=hybrid, k=5
recall@5:    1.0
precision@5: 0.2
mrr:           0.910714
  [hit ] rank=1  How do I create a new widget? -> api/widget_api.md
  [hit ] rank=1  How are bearer tokens issued and validated? -> api/auth_api.md
  ...
  [hit ] rank=4  What does idempotent mean? -> notes/glossary-copy.txt
exit=0
```

About that 0.2: it is the metric definition working, not a retrieval
failure. The labelled set has one expected document per query, so at most
one of the five returned chunks can be relevant. `precision@5` is
therefore structurally capped at 1/5 = 0.2 per query even when ranking is
perfect, and the corpus meets that cap on every query. Read recall@k and
MRR for quality here; precision@k would only become meaningful with
multi-document relevance labels.

The regression gate:

```
$ docpipe eval --index index.sqlite --queries eval_queries.json --check
eval: gate PASS - metrics at or above baseline_metrics.json
```

`--check` compares the three metrics against `baseline_metrics.json`
(`--baseline` overrides the path). Any metric below baseline by more than a
tiny tolerance prints the regression and exits 1.

## Construction caveats

- `architecture.pdf` is not byte-reproducible from the seed (pymupdf
  metadata at save time). The seed rebuild reproduces the 14 text and
  markdown files exactly.
- Vector-layer determinism in the index build is by construction (chunk_id
  insert ordering), not independently hash-verified.
- The SQLite dump for determinism checks requires the sqlite-vec extension
  to be loaded or `iterdump()` fails with `no such module: vec0`.
- After regenerating the corpus from the seed, the `verify` disk check for
  `architecture.pdf` will fail against a previously built index because the
  file's hash changed. Rebuild the index after regenerating.

## Test and lint record

On 2026-09-18, from a clean state: 53 tests passed (`pytest -q`), ruff
check and format check were clean, and mypy reported no issues in 18
source files. CI runs these on push and pull request via
`.github/workflows/ci.yml`.
