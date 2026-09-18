# docpipe

A deterministic document ingestion and retrieval pipeline with a provenance gate.

docpipe walks a corpus of text, markdown and PDF files, hashes every file,
chunks it, and builds a SQLite index. The part that matters is the last step:
a `verify` command that checks every row in the index against a real file on
disk and its content hash. If any row cannot be traced, `verify` prints the
exact failure and exits non-zero. There is no silent path.

## Why this exists

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

## What it does

The pipeline runs in four steps:

1. `ingest` walks the corpus, hashes each file, dedupes byte-identical repeats,
   and quarantines anything unsafe (symlinks escaping the root, invalid UTF-8,
   unreadable files, unsupported types).
2. `parse` extracts text and splits it into deterministic chunks. PDFs go
   through pymupdf when it is installed.
4. `index` builds a SQLite database. Every identifier is content derived and
   every insert stream is sorted, so building the index twice from the same
   inputs yields a byte-identical file. The same step also embeds every chunk
   and writes the vectors into a sqlite-vec table inside the same SQLite file.
5. `verify` checks the index against the corpus and the ingest manifest, then
   exits 0 or non-zero.

There is also a read-only `query` subcommand (search, document fetch, listing,
chunk context, stats), a retrieval `eval` subcommand, and a small FastAPI
service.

## Install

```
git clone <this repo>
cd docpipe
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Requires Python 3.10 or newer. The `[dev]` extra pulls pytest, ruff and mypy.

## Run it end to end

```
# generate the seeded demo corpus
python tools/make_corpus.py --root corpus --seed 20260918

# ingest, parse, index
docpipe ingest corpus --out manifest.json --quarantine-dir quarantine
docpipe parse --manifest manifest.json --out chunks.jsonl --corpus corpus
docpipe index --manifest manifest.json --chunks chunks.jsonl --out index.sqlite

# the gate: this must exit 0
docpipe verify corpus --index index.sqlite --manifest manifest.json
```

The demo corpus is a fictional widget platform: API references, specs,
changelogs and runbooks. It has no real credentials or endpoints. The generator
is committed; the files it emits are not (they are in `.gitignore`).

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

And when the index is tampered with after the fact:

```
$ docpipe verify corpus --index index.sqlite --manifest manifest.json
verify: 16 documents, 22 chunks, 1746 terms
verify: FAILED - 2 provenance failure(s):
  - document README.md: doc_id 6e702aec... != stored sha256 0
  - document README.md: hash mismatch (stored 0, disk 6e702aec...)
```

The full transcript for every step is in `EVIDENCE.md`.

## Determinism

Build the index twice from the same manifest and chunk stream and hash the
SQLite dump of each. The hashes match, which is the concrete proof that the
build has no wall-clock or insertion-order dependence:

```
ba7364962c827b9de52125a76887cf390f965b90b945855586cbecb8aee1ae57
ba7364962c827b9de52125a76887cf390f965b90b945855586cbecb8aee1ae57
```

## The verification gate

`verify` requires three inputs: the corpus directory (positional), the index
(`--index`, default `index.sqlite`) and the manifest (`--manifest`, default
`manifest.json`). It checks:

- referential integrity inside the index (no orphaned chunks or terms, no
  duplicate rel_paths)
- forward provenance, meaning every document row maps to a real file whose
  on-disk hash matches the stored one
- completeness, meaning every manifest hash has exactly one canonical document
  row and no phantom rows exist
- the `runs` table is a single row whose recorded counts match the live tables

Any failure is printed with the offending rel_path and the exact mismatch, then
the process exits 1. Missing inputs exit 2. Only a fully traced index exits 0.

The `--exemptions` option takes a JSON file of rel_paths that are allowed to
skip the disk check, for sources that are legitimately absent at verify time.
Exemptions are printed loudly, never hidden.

## Index schema

The database is SQLite, version 1 of the schema:

```
documents (doc_id PK, rel_path, sha256, size, kind)
chunks    (chunk_id PK, doc_id FK, chunk_index, text)
terms     (term, chunk_id FK, position)   -- PK (term, chunk_id, position)
runs      (run_id PK, manifest_sha256, chunks_sha256, doc_count, chunk_count, term_count)
meta      (key PK, value)
```

`doc_id` is the SHA-256 of the source file bytes. `chunk_id` is the SHA-256 of
doc_id plus chunk index plus chunk text. `run_id` is the SHA-256 of the manifest
hash plus the chunks hash. Nothing is a sequence number or a timestamp.

## The embedding layer

`index` and `query`/`eval` share one `Embedder` protocol: a backend needs a
`name`, a `dim`, and `embed`/`embed_one` methods. Two backends exist:

- `hashed-ngram` (default). A deterministic local lexical vectoriser. Word
  unigrams and character trigrams are feature-hashed through a fixed BLAKE2b
  projection into 384 dimensions, then L2 normalised. It is a lexical
  embedding, not a neural one: similar vectors mean surface word and n-gram
  overlap, not shared meaning. Texts that mean the same thing in different
  words get different vectors. That is the tradeoff that buys you a build
  that runs anywhere: no network, no API key, no model download, identical
  vectors on every machine, so CI and the 53 tests always run.
- `http`. An OpenAI-compatible `/embeddings` client, selected explicitly with
  `--embedder http --embedding-base-url ... --embedding-model ...` and a
  `--embedding-api-key` (or `DOCPIPE_EMBEDDING_API_KEY`). It never falls back
  to the local backend; a missing key or failed request is a hard error.

An index records which backend built its vectors in the `meta` table, and
query time rebuilds that same embedder. All committed numbers in this README,
including the eval metrics below, were produced by the `hashed-ngram` local
backend.

## Search modes

`docpipe query search <term> [--mode term|vector|hybrid]` offers three modes:

- `term`: BM25 (k1=1.5, b=0.75) over the positional term index. This is the
  default.
- `vector`: the query is embedded with the recorded embedder and sqlite-vec
  returns the nearest chunks by cosine. A hit's `score` is `1 - distance`.
- `hybrid`: both run, over a candidate pool of the union of the top
  `4 * limit` chunks from each side, and the pool is re-ranked by

  ```
  hybrid_score(c) = alpha * vec_norm(c) + (1 - alpha) * term_norm(c)
  ```

  with `alpha = 0.5`. `vec_norm` and `term_norm` are min-max normalised to
  `[0, 1]` over the pool. A chunk missing from one side gets the lowest score
  observed on that side, so a combined match ranks above a lexical-only one.

There is no `--alpha` CLI flag; the value is fixed in `src/docpipe/search.py`.

## Vector storage

Vectors live in the same `index.sqlite` file, in a sqlite-vec virtual table:

```sql
CREATE VIRTUAL TABLE chunk_vectors USING vec0(
  embedding float[384] distance_metric=cosine,
  +chunk_id text
)
```

Vectors are inserted in `chunk_id` order so the build stays deterministic. The
`embedder` and `embed_dim` meta rows record the backend (the committed demo
index reads `hashed-ngram`, `384`).

An honest verification note: before you trust the "sqlite-vec is a real
dependency" line, run it yourself. On the committed demo index this was
verified directly:

```
$ python - <<'EOF'
import sqlite3, sqlite_vec
from importlib.metadata import version
conn = sqlite3.connect('index.sqlite')
conn.enable_load_extension(True)
sqlite_vec.load(conn)
print(version('sqlite-vec'), sqlite_vec and conn.execute('SELECT count(*) FROM chunk_vectors').fetchone())
EOF
pip package sqlite-vec: 0.1.9
vector rows: (22,)
```

`vec_version()` in a fresh connection also reports `v0.1.9`. Then run a vector
search to confirm the extension does real work at query time:

```
$ docpipe query search "widget authentication" --mode vector --limit 2
search 'widget authentication' [vector]: 2 hit(s)
```

and check the meta rows:

```
$ sqlite3 index.sqlite "SELECT key, value FROM meta WHERE key LIKE 'embed%';"
embed_dim|384
embedder|hashed-ngram
```

(If the extension could not be loaded, the operation raises
`VectorUnavailableError` rather than falling back to something else.)

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

How to reproduce the committed numbers:

```
python tools/make_corpus.py --root corpus --seed 20260918
docpipe ingest corpus --out manifest.json --quarantine-dir quarantine
docpipe parse --manifest manifest.json --out chunks.jsonl --corpus corpus
docpipe index --manifest manifest.json --chunks chunks.jsonl --out index.sqlite
docpipe eval --index index.sqlite --queries eval_queries.json --k 5 --mode hybrid
```

Real output of that run (this is the committed baseline in
`baseline_metrics.json`, produced by the local lexical embedder, hybrid mode,
k=5, 14 labelled queries):

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

About that 0.2: it is the metric definition working, not a retrieval failure.
The labelled set has one expected document per query, so at most one of the
five returned chunks can be relevant. `precision@5` is therefore structurally
capped at 1/5 = 0.2 per query even when ranking is perfect, and the corpus
meets that cap on every query. Read recall@k and MRR for quality here;
precision@k would only become meaningful with multi-document relevance labels.

The regression gate:

```
$ docpipe eval --index index.sqlite --queries eval_queries.json --check
eval: gate PASS - metrics at or above baseline_metrics.json
```

`--check` compares the three metrics against `baseline_metrics.json`
(`--baseline` overrides the path). Any metric below baseline by more than a
tiny tolerance prints the regression and exits 1, so a ranking change that
breaks the harness fails CI.

## The query layer

```
docpipe query search widget --index index.sqlite
docpipe query doc <sha256> --index index.sqlite
docpipe query list --index index.sqlite
docpipe query context <sha256> <chunk-index> --index index.sqlite
docpipe query stats --index index.sqlite
```

All of these open the index read-only. A query can never mutate the database.

## The HTTP service

```
docpipe api --index index.sqlite --host 127.0.0.1 --port 8000
```

Exposes `/health`, `/search`, `/documents`, `/documents/{doc_id}` and `/stats`.
See `EVIDENCE.md` for a real request and response.

## Development

```
pytest -q        # 53 tests
ruff check .     # lint
ruff format --check .
mypy             # type check
```

All three are clean on a fresh clone. CI runs them on push and pull request via
`.github/workflows/ci.yml`.

## License

MIT. See `LICENSE`.
